"""Layer 1a: the reviewer model on CUAD's own task.

Ask for one category's spans in one contract, or an abstention, and score the
answer against the annotated spans at Jaccard >= 0.5. Precision, recall and F1
per category and overall.

What this number is comparable to, exactly: the published DeBERTa-xlarge
baseline reports AUPR 47.8 and Precision@80%Recall 44.0, both of which need a
ranked confidence over candidate spans. A generative model asked to list spans
gives no ranking, so what comes out here is precision/recall/F1 at a single
operating point. It is the closest measurement of the same task on the same data
with the same matching rule, and it is not the paper's AUPR. evaluation/README.md
says so too, because this is the number most likely to be quoted out of context.

Abstentions are scored: a category CUAD annotates as absent and the model
declines to answer is correct and contributes nothing to any count, which is
right — inflating precision with 30 correct abstentions per contract would hide
the model's behaviour on the categories that are there.
"""

import asyncio
from dataclasses import dataclass, field

from evaluation.common.prompts import EXTRACTION_PROMPT
from evaluation.common.spans import JACCARD_THRESHOLD, Counts, match_spans
from evaluation.cuad.loader import Clause, Contract
from interfaces.llm.interface import LLMInterface
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ExtractionAttempt:
    """One (contract, category) question and what came back."""

    document: str
    category: str
    gold_spans: tuple[str, ...]
    predicted_spans: tuple[str, ...] = ()
    abstained: bool = False
    raw_reply: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "document": self.document,
            "category": self.category,
            "gold_spans": list(self.gold_spans),
            "predicted_spans": list(self.predicted_spans),
            "abstained": self.abstained,
            "raw_reply": self.raw_reply,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, record: dict) -> "ExtractionAttempt":
        return cls(
            document=record["document"],
            category=record["category"],
            gold_spans=tuple(record.get("gold_spans") or ()),
            predicted_spans=tuple(record.get("predicted_spans") or ()),
            abstained=bool(record.get("abstained")),
            raw_reply=record.get("raw_reply", ""),
            error=record.get("error", ""),
        )


def parse_extraction(raw: dict) -> tuple[tuple[str, ...], bool]:
    """
    The spans and the abstention out of a model reply.

    A reply claiming to abstain while listing spans is treated as not abstaining:
    the spans are the claim being scored, and taking the flag's word for it would
    let a model score an abstention and a guess at the same time.
    """
    spans = raw.get("spans")
    if isinstance(spans, str):
        spans = [spans]
    if not isinstance(spans, list):
        spans = []

    cleaned = tuple(str(span).strip() for span in spans if str(span).strip())

    return cleaned, bool(raw.get("abstain")) and not cleaned


async def extract_clause(llm: LLMInterface, contract: Contract, clause: Clause) -> ExtractionAttempt:
    """
    Asks for one category's spans.

    A failed call becomes an attempt with an error rather than an exception:
    one unparseable reply out of a thousand should cost that question, not the
    run, and the error is in the results file to be counted.
    """
    attempt = ExtractionAttempt(document=contract.title, category=clause.category, gold_spans=clause.spans)

    try:
        raw = await llm.complete_json(
            EXTRACTION_PROMPT,
            category=clause.category,
            definition=clause.definition,
            contract=contract.text,
        )
    except Exception as exc:
        logger.warning("%s / %s: extraction failed (%s)", contract.title, clause.category, exc)
        attempt.error = f"{type(exc).__name__}: {exc}"
        return attempt

    attempt.predicted_spans, attempt.abstained = parse_extraction(raw)

    return attempt


async def extract_contract(
    llm: LLMInterface,
    contract: Contract,
    categories: set[str] | None,
    semaphore: asyncio.Semaphore,
) -> list[ExtractionAttempt]:
    """Every asked-for category of one contract, respecting the run's concurrency cap."""
    wanted = [clause for clause in contract.clauses if categories is None or clause.category in categories]

    async def guarded(clause: Clause) -> ExtractionAttempt:
        async with semaphore:
            return await extract_clause(llm, contract, clause)

    return list(await asyncio.gather(*(guarded(clause) for clause in wanted)))


@dataclass
class ExtractionScore:
    """Per-category and overall counts, plus how the abstentions went."""

    per_category: dict[str, Counts] = field(default_factory=dict)
    overall: Counts = field(default_factory=Counts)
    correct_abstentions: int = 0
    missed_abstentions: int = 0
    errors: int = 0
    attempts: int = 0

    def to_dict(self) -> dict:
        return {
            "note": (
                "Precision/recall/F1 at a single operating point, token-set Jaccard "
                f">= {JACCARD_THRESHOLD}. Not the AUPR the CUAD paper reports."
            ),
            "attempts": self.attempts,
            "errors": self.errors,
            "correct_abstentions": self.correct_abstentions,
            "missed_abstentions": self.missed_abstentions,
            "overall": self.overall.to_dict(),
            "per_category": {category: counts.to_dict() for category, counts in sorted(self.per_category.items())},
        }


def score_extraction(attempts: list[ExtractionAttempt], threshold: float = JACCARD_THRESHOLD) -> ExtractionScore:
    """
    Counts the attempts up.

    A category CUAD leaves empty contributes false positives when the model
    answers anyway and a correct abstention when it does not; a category CUAD
    annotates contributes matched spans, unmatched predictions and missed golds.
    """
    score = ExtractionScore(attempts=len(attempts))

    for attempt in attempts:
        if attempt.error:
            score.errors += 1
            continue

        counts = score.per_category.setdefault(attempt.category, Counts())

        if not attempt.gold_spans:
            if attempt.predicted_spans:
                score.missed_abstentions += 1
                counts = counts + Counts(false_positives=len(attempt.predicted_spans))
            else:
                score.correct_abstentions += 1
        else:
            result = match_spans(list(attempt.predicted_spans), list(attempt.gold_spans), threshold)
            counts = counts + Counts(
                true_positives=result.true_positives,
                false_positives=len(result.unmatched_predicted),
                false_negatives=len(result.unmatched_gold),
            )

        score.per_category[attempt.category] = counts

    for counts in score.per_category.values():
        score.overall = score.overall + counts

    return score
