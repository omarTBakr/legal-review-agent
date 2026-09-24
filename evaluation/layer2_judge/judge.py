"""Grading one finding against one clause with a stronger model.

The judge is a different model from the reviewer, at temperature 0. Not for
politeness: a model grading its own output agrees with itself, and the agreement
is not evidence. run.py refuses to judge when EVAL_JUDGE_MODEL equals
OPENROUTER_MODEL rather than producing a number that looks like a score.

The reply is parsed with LLMInterface.parse_json_object, which already strips
fences and repairs truncated JSON — the same parser the product trusts, so a
judge reply the product's parser would reject fails here too.

`total_score` is recomputed from the four dimensions and not taken from the
reply. Judges arithmetic badly, and a claimed 0.9 beside three zeroes would set
the escalation threshold off the wrong number. The claimed value is kept in
`reported_total` so the disagreement is visible.
"""

import asyncio
from dataclasses import dataclass

from evaluation.common.prompts import JUDGE_PROMPT
from evaluation.common.retry import with_backoff
from interfaces.llm_interface import LLMInterface
from schemas.key_risk import KeyRisk
from utils.logger import get_logger

logger = get_logger(__name__)

DIMENSIONS = ("correctness", "completeness", "precision", "explanation")

# a judge that passes everything at 0.5 is not grading; the rubric says 0.75
PASS_THRESHOLD = 0.75


@dataclass
class JudgeVerdict:
    """One graded finding."""

    document: str
    category: str
    description: str
    severity: str
    quote: str
    quote_verified: bool
    ground_truth: str
    correctness: int = 0
    completeness: int = 0
    precision: int = 0
    explanation: int = 0
    total_score: float = 0.0
    reported_total: float | None = None
    passed: bool = False
    reason: str = ""
    raw_reply: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "document": self.document,
            "category": self.category,
            "description": self.description,
            "severity": self.severity,
            "quote": self.quote,
            "quote_verified": self.quote_verified,
            "ground_truth": self.ground_truth,
            "correctness": self.correctness,
            "completeness": self.completeness,
            "precision": self.precision,
            "explanation": self.explanation,
            "total_score": round(self.total_score, 4),
            "reported_total": self.reported_total,
            "pass": self.passed,
            "reason": self.reason,
            "raw_reply": self.raw_reply,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, record: dict) -> "JudgeVerdict":
        fields = {key: value for key, value in record.items() if key in cls.__dataclass_fields__}
        fields["passed"] = bool(record.get("pass", record.get("passed")))

        return cls(**fields)


def _as_bit(value) -> int:
    """
    A dimension score as 0 or 1.

    Anything that is not recognisably 1 is 0: a judge that answers "partial" or
    0.5 has not followed the rubric, and rounding its hedge up would turn a
    failed dimension into a pass.
    """
    if isinstance(value, bool):
        return int(value)
    try:
        return 1 if int(value) == 1 else 0
    except (TypeError, ValueError):
        return 0


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def score_verdict(raw: dict, verdict: JudgeVerdict) -> JudgeVerdict:
    """
    Fills a verdict in from a parsed judge reply.

    The four dimensions are authoritative; total_score is their mean and `pass`
    follows from it, whatever the reply claimed for either.
    """
    for dimension in DIMENSIONS:
        setattr(verdict, dimension, _as_bit(raw.get(dimension)))

    verdict.total_score = sum(getattr(verdict, dimension) for dimension in DIMENSIONS) / len(DIMENSIONS)
    verdict.reported_total = _as_float(raw.get("total_score"))
    verdict.passed = verdict.total_score >= PASS_THRESHOLD
    verdict.reason = str(raw.get("reason", "")).strip()

    return verdict


async def judge_finding(
    llm: LLMInterface,
    document: str,
    category: str,
    ground_truth: str,
    risk: KeyRisk,
) -> JudgeVerdict:
    """
    Grades one risk against the clause it was matched to.

    A failed call becomes a verdict with an error and a total_score of 0, which
    escalates: an ungraded finding is exactly the case a human should see.
    """
    verdict = JudgeVerdict(
        document=document,
        category=category,
        description=risk.description,
        severity=risk.severity.value,
        quote=risk.quote,
        quote_verified=risk.quote_verified,
        ground_truth=ground_truth,
    )

    try:
        raw = await with_backoff(
            lambda: llm.complete(
                JUDGE_PROMPT,
                document=document,
                category=category,
                ground_truth=ground_truth,
                description=risk.description,
                severity=risk.severity.value,
                location=risk.location,
                quote=risk.quote,
                quote_verified=risk.quote_verified,
            ),
            what=f"the judge on {document}/{category}",
        )
        verdict.raw_reply = raw
        parsed = LLMInterface.parse_json_object(raw, prompt_name="judge")
    except Exception as exc:
        logger.warning("%s / %s: the judge failed (%s)", document, category, exc)
        verdict.error = f"{type(exc).__name__}: {exc}"
        return verdict

    return score_verdict(parsed, verdict)


async def judge_all(llm: LLMInterface, findings: list[tuple[str, str, str, KeyRisk]], concurrency: int) -> list[JudgeVerdict]:
    """Grades a batch of (document, category, ground truth, risk) tuples."""
    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(finding) -> JudgeVerdict:
        async with semaphore:
            return await judge_finding(llm, *finding)

    return list(await asyncio.gather(*(guarded(finding) for finding in findings)))


def summarize(verdicts: list[JudgeVerdict]) -> dict:
    """
    The judge's own numbers, dimension by dimension.

    Per dimension rather than only the mean, because "the judge passed 60%" hides
    which part failed, and PRECISION failing everywhere means something different
    from CORRECTNESS failing everywhere.
    """
    graded = [verdict for verdict in verdicts if not verdict.error]

    if not graded:
        return {"graded": 0, "errors": len(verdicts)}

    return {
        "graded": len(graded),
        "errors": len(verdicts) - len(graded),
        "pass_rate": round(sum(verdict.passed for verdict in graded) / len(graded), 4),
        "mean_total_score": round(sum(verdict.total_score for verdict in graded) / len(graded), 4),
        "by_dimension": {
            dimension: round(sum(getattr(verdict, dimension) for verdict in graded) / len(graded), 4) for dimension in DIMENSIONS
        },
        "arithmetic_disagreements": sum(
            1
            for verdict in graded
            if verdict.reported_total is not None and abs(verdict.reported_total - verdict.total_score) > 0.01
        ),
    }
