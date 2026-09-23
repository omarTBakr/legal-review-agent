"""The expert model, re-deciding what the judge escalated.

Layer 2 grades every finding that matched a clause; layer 3 only sees the ones
it could not settle. That is what makes a large model affordable here: a 25
contract run judges a few hundred findings and escalates a few dozen, so the
expensive model is asked a fortieth as many questions as the reviewer was.

It is the *third* opinion, not a second judge. Its answer is a decision on the
finding — upheld, overturned, partial — rather than the judge's four bits, and it
is given the judge's verdict to disagree with, because "does a stronger model
overturn a weaker one's calls" is the question layer 3 exists to answer. It also
answers one the judge cannot: whether this needs a lawyer at all. A finding the
adjudicator settles from the clause text is a finding a human does not have to
read, which is the only way a human review layer scales past a demo.

`adjudicate_all` never raises on a bad reply. A failed adjudication is recorded
with its error and left needing a human, which is where it already was.

    uv run python -m evaluation.layer3_expert.adjudicator evaluation/results/cuad-<run>
"""

import argparse
import asyncio
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from evaluation.common.prompts import ADJUDICATION_PROMPT
from interfaces.llm_interface import LLMInterface
from utils.logger import get_logger

logger = get_logger(__name__)

ADJUDICATIONS_FILE = "adjudications.jsonl"

DECISIONS = ("upheld", "partial", "overturned")

# what each decision is worth when the miss rate is recomputed: a clause whose
# finding was overturned is a clause nobody caught, and a partial one is half
# caught, which is the honest weight for "right risk, wrong half of it"
CREDIT = {"upheld": 1.0, "partial": 0.5, "overturned": 0.0}


def _one_of(value, allowed: tuple[str, ...] | set[str], fallback: str = "") -> str:
    """A field the rubric fixed the vocabulary of, or the fallback."""
    text = str(value or "").strip().lower()

    return text if text in allowed else fallback


@dataclass
class Adjudication:
    """One escalated finding, settled."""

    document: str
    category: str
    quote: str
    ground_truth: str = ""
    escalation_reasons: tuple[str, ...] = ()
    judge_total_score: float | None = None
    judge_pass: bool | None = None
    decision: str = ""
    correct_severity: str = ""
    severity_was: str = ""
    judge_was: str = ""
    material_omission: str = ""
    needs_human: bool = True
    confidence: str = ""
    reason: str = ""
    raw_reply: str = ""
    error: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        """How an adjudication is matched to a queue item and a human's form."""
        return self.document, self.category, self.quote

    @property
    def credit(self) -> float:
        """How much of a catch this finding is, per CREDIT."""
        return CREDIT.get(self.decision, 0.0)

    def to_dict(self) -> dict:
        return {
            "document": self.document,
            "category": self.category,
            "quote": self.quote,
            "ground_truth": self.ground_truth,
            "escalation_reasons": list(self.escalation_reasons),
            "judge_total_score": self.judge_total_score,
            "judge_pass": self.judge_pass,
            "decision": self.decision,
            "correct_severity": self.correct_severity,
            "severity_was": self.severity_was,
            "judge_was": self.judge_was,
            "material_omission": self.material_omission,
            "needs_human": self.needs_human,
            "confidence": self.confidence,
            "reason": self.reason,
            "raw_reply": self.raw_reply,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, record: dict) -> "Adjudication":
        fields = {key: value for key, value in record.items() if key in cls.__dataclass_fields__}
        fields["escalation_reasons"] = tuple(record.get("escalation_reasons") or ())

        return cls(**fields)


def score_adjudication(raw: dict, adjudication: Adjudication) -> Adjudication:
    """
    Fills an adjudication in from a parsed reply.

    A decision outside the three the rubric allows is treated as no decision at
    all: it scores nothing and keeps the item in front of a human. An adjudicator
    that invents a fourth verdict has not followed the rubric, and mapping its
    invention onto the nearest real one would be us deciding, not it.
    """
    adjudication.decision = _one_of(raw.get("decision"), DECISIONS)
    adjudication.correct_severity = _one_of(raw.get("correct_severity"), ("critical", "high", "medium", "low"))
    adjudication.severity_was = _one_of(raw.get("severity_was"), ("correct", "overstated", "understated"))
    adjudication.judge_was = _one_of(raw.get("judge_was"), ("right", "wrong", "partly right"))
    adjudication.material_omission = str(raw.get("material_omission") or "").strip()
    adjudication.confidence = _one_of(raw.get("confidence"), ("high", "medium", "low"))
    adjudication.reason = str(raw.get("reason") or "").strip()

    # a low-confidence verdict, or one the adjudicator could not give, still goes
    # to a lawyer whatever the reply said about needing one
    asked_for_human = bool(raw.get("needs_human"))
    adjudication.needs_human = asked_for_human or not adjudication.decision or adjudication.confidence == "low"

    return adjudication


async def adjudicate(llm: LLMInterface, item: dict) -> Adjudication:
    """Settles one queue item, as written by layer2_judge.escalation."""
    adjudication = Adjudication(
        document=item.get("document", ""),
        category=item.get("category", ""),
        quote=item.get("quote", ""),
        ground_truth=item.get("ground_truth", ""),
        escalation_reasons=tuple(item.get("escalation_reasons") or ()),
        judge_total_score=item.get("total_score"),
        judge_pass=item.get("pass"),
    )

    try:
        raw = await llm.complete(
            ADJUDICATION_PROMPT,
            document=adjudication.document,
            category=adjudication.category,
            escalation_reasons=", ".join(adjudication.escalation_reasons) or "(unrecorded)",
            ground_truth=adjudication.ground_truth,
            description=item.get("description", ""),
            severity=item.get("severity", ""),
            quote=adjudication.quote,
            quote_verified=item.get("quote_verified"),
            correctness=item.get("correctness"),
            completeness=item.get("completeness"),
            precision=item.get("precision"),
            explanation=item.get("explanation"),
            total_score=item.get("total_score"),
            judge_pass=item.get("pass"),
            reason=item.get("reason", ""),
        )
        adjudication.raw_reply = raw
        parsed = LLMInterface.parse_json_object(raw, prompt_name="adjudication")
    except Exception as exc:
        logger.warning("%s / %s: the adjudicator failed (%s)", adjudication.document, adjudication.category, exc)
        adjudication.error = f"{type(exc).__name__}: {exc}"
        return adjudication

    return score_adjudication(parsed, adjudication)


async def adjudicate_all(llm: LLMInterface, queue: list[dict], concurrency: int, limit: int = 0) -> list[Adjudication]:
    """
    Settles the queue, worst judge score first when `limit` cuts it short.

    The queue arrives sorted that way from build_queue, so a limit spends the
    budget on the findings most likely to be wrong rather than on whichever
    contract sorted first.
    """
    items = queue[:limit] if limit else queue
    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(item: dict) -> Adjudication:
        async with semaphore:
            return await adjudicate(llm, item)

    return list(await asyncio.gather(*(guarded(item) for item in items)))


@dataclass
class AdjudicationSummary:
    """What layer 3's model said, and how often it disagreed with layer 2."""

    settled: int = 0
    errors: int = 0
    decisions: Counter = field(default_factory=Counter)
    overturned_a_pass: int = 0
    upheld_a_fail: int = 0
    still_needs_human: int = 0
    severity_wrong: int = 0
    confidence: Counter = field(default_factory=Counter)

    def to_dict(self) -> dict:
        return {
            "settled": self.settled,
            "errors": self.errors,
            "decisions": dict(self.decisions),
            "judge_passed_expert_overturned": self.overturned_a_pass,
            "judge_failed_expert_upheld": self.upheld_a_fail,
            "disagreed_with_judge": self.overturned_a_pass + self.upheld_a_fail,
            "still_needs_human": self.still_needs_human,
            "severity_wrong": self.severity_wrong,
            "confidence": dict(self.confidence),
        }


def summarize(adjudications: list[Adjudication]) -> dict:
    """
    Layer 3's numbers, with the two disagreements named separately.

    `judge_passed_expert_overturned` and `judge_failed_expert_upheld` are
    different failures of the judge and cost different things: the first is a bad
    finding that reached a client, the second is human time spent on a finding
    that was fine. One number for both would hide which one the judge is making.
    """
    summary = AdjudicationSummary()

    for item in adjudications:
        if item.error:
            summary.errors += 1
            summary.still_needs_human += 1
            continue

        summary.settled += 1
        summary.decisions[item.decision or "(unrecognised)"] += 1
        summary.confidence[item.confidence or "(unstated)"] += 1

        if item.needs_human:
            summary.still_needs_human += 1
        if item.severity_was in ("overstated", "understated"):
            summary.severity_wrong += 1
        if item.judge_pass and item.decision == "overturned":
            summary.overturned_a_pass += 1
        if item.judge_pass is False and item.decision == "upheld":
            summary.upheld_a_fail += 1

    return summary.to_dict()


def write_adjudications(adjudications: list[Adjudication], path: Path) -> Path:
    """Writes the adjudications as JSONL, replacing any earlier pass over the same run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(item.to_dict()) + "\n" for item in adjudications), encoding="utf-8")

    return path


def load_adjudications(path: Path) -> list[Adjudication]:
    """The adjudications from a run directory or a JSONL file."""
    if path.is_dir():
        path = path / ADJUDICATIONS_FILE

    if not path.is_file():
        return []

    return [Adjudication.from_dict(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def human_queue(queue: list[dict], adjudications: list[Adjudication]) -> list[dict]:
    """
    The queue a lawyer should actually work through, and why each item survived.

    An item the adjudicator settled with confidence is dropped; one it could not
    settle stays, carrying the expert's reason so the lawyer starts from an
    opinion rather than from nothing. Items with no adjudication at all — the run
    skipped layer 3, or `adjudicate_limit` cut it short — stay untouched: an
    unasked question is not an answered one.
    """
    settled = {item.key: item for item in adjudications}
    remaining = []

    for entry in queue:
        key = (entry.get("document", ""), entry.get("category", ""), entry.get("quote", ""))
        expert = settled.get(key)

        if expert is None:
            remaining.append(entry)
            continue
        if not expert.needs_human:
            continue

        remaining.append(
            {
                **entry,
                "expert_decision": expert.decision,
                "expert_reason": expert.reason,
                "expert_confidence": expert.confidence,
                "expert_material_omission": expert.material_omission,
            }
        )

    return remaining


async def main_async(arguments) -> dict:
    """Adjudicates a finished run's queue, for a run that was made with --no-adjudicator."""
    from evaluation.common.meter import TokenMeter
    from evaluation.config import get_eval_settings
    from evaluation.layer2_judge.escalation import read_queue
    from utils.config import get_setting

    settings = get_setting()
    eval_settings = get_eval_settings()
    run_dir = arguments.run

    queue = read_queue(run_dir / "escalations.jsonl")
    if not queue:
        raise SystemExit(f"no escalations under {run_dir}")

    meter = TokenMeter()
    await meter.load_prices()
    llm = meter.llm(
        settings,
        model=eval_settings.adjudicator_model,
        temperature=eval_settings.adjudicator_temperature,
        max_tokens=eval_settings.adjudicator_max_tokens,
    )

    logger.info("adjudicating %d escalation(s) with %s", len(queue), eval_settings.adjudicator_model)
    try:
        adjudications = await adjudicate_all(llm, queue, eval_settings.request_concurrency, arguments.limit)
    finally:
        await llm.aclose()

    write_adjudications(adjudications, run_dir / ADJUDICATIONS_FILE)
    summary = summarize(adjudications)

    print(json.dumps(summary, indent=2))
    print(f"\n  {len(human_queue(queue, adjudications))} of {len(queue)} escalation(s) still need a lawyer")
    print(f"  {meter.summary_line()}")

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Adjudicate a run's escalation queue with the expert model.")
    parser.add_argument("run", type=Path, help="a results/cuad-<timestamp> directory")
    parser.add_argument("--limit", type=int, default=0, help="adjudicate only the worst N escalations")
    arguments = parser.parse_args()

    asyncio.run(main_async(arguments))


if __name__ == "__main__":
    main()
