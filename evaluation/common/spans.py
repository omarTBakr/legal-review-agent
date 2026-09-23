"""Span matching for the suite, and the confusion-matrix counts built on it.

The matching itself lives in `utils/spans.py`, because the product needs it too:
`utils/compare.py` pairs the risks in two reviews of the same contract with the
same rule this suite uses to pair a quote against a CUAD annotation. Importing
it rather than keeping a copy is the same discipline as everywhere else here —
the suite calls the product's parser, prompts and evidence check, so a change to
what "the same text" means moves both numbers together instead of silently
moving one.

`Counts` stays here. Precision, recall and F1 are things a benchmark says about
a model; the product has no use for them.
"""

from dataclasses import dataclass

from utils.spans import (
    JACCARD_THRESHOLD,
    Match,
    MatchResult,
    covers_any,
    jaccard,
    match_spans,
    tokens,
)

__all__ = [
    "JACCARD_THRESHOLD",
    "Counts",
    "Match",
    "MatchResult",
    "covers_any",
    "jaccard",
    "match_spans",
    "tokens",
]


@dataclass(frozen=True)
class Counts:
    """Confusion-matrix counts, kept separate so they can be summed across categories."""

    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0

    def __add__(self, other: "Counts") -> "Counts":
        return Counts(
            self.true_positives + other.true_positives,
            self.false_positives + other.false_positives,
            self.false_negatives + other.false_negatives,
        )

    @property
    def precision(self) -> float:
        predicted = self.true_positives + self.false_positives
        return self.true_positives / predicted if predicted else 0.0

    @property
    def recall(self) -> float:
        actual = self.true_positives + self.false_negatives
        return self.true_positives / actual if actual else 0.0

    @property
    def f1(self) -> float:
        precision, recall = self.precision, self.recall
        return 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    def to_dict(self) -> dict:
        return {
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
        }
