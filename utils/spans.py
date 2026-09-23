"""
Deciding when two pieces of contract text are the same span.

Deliberately crude and inspectable: lower-case the text, take its words, and
compare the two word sets with Jaccard. Spans match at 0.5, which in practice
means one is roughly the other plus or minus a leading "Notwithstanding the
foregoing," — it tolerates one side quoting a sentence the other did not
include, and rejects a quote that merely mentions the same clause number.

Character offsets are not used. A quote comes out of Markdown that pymupdf4llm
produced, and the same clause re-parsed from a different PDF lands at a
different offset, so a positional comparison would be measuring the parser.

Two callers, which is why this lives in `utils/` rather than in either of them:
`utils/compare.py` pairs the risks in two reviews of the same contract, and the
evaluation suite pairs a review's quotes against CUAD's annotated spans. One
definition of "the same text" across both, so a number in one can be traced to
the other.
"""

import re
from dataclasses import dataclass

from utils.evidence import normalize

# the bar for "these are the same span"
JACCARD_THRESHOLD = 0.5

_WORD = re.compile(r"[a-z0-9']+")


def tokens(text: str) -> set[str]:
    """
    The word set a span is compared by.

    Reuses utils.evidence.normalize so that the Markdown noise, curly quotes and
    page markers a review's quotes carry are stripped exactly as the evidence
    check strips them — one definition of "the same text", not two.
    """
    return set(_WORD.findall(normalize(text)))


def jaccard(left: str, right: str) -> float:
    """
    Token-set overlap of two spans, 0.0 to 1.0.

    Two empty spans score 0.0, not 1.0: an empty quote has matched nothing, and
    returning a perfect score for it would let a model abstain its way to a good
    number.
    """
    first, second = tokens(left), tokens(right)
    if not first or not second:
        return 0.0

    return len(first & second) / len(first | second)


@dataclass(frozen=True)
class Match:
    """A predicted span paired with the gold span it covers."""

    predicted_index: int
    gold_index: int
    score: float


@dataclass(frozen=True)
class MatchResult:
    """
    The outcome of matching one set of spans against another.

    What `unmatched_predicted` means depends on the caller: in the extraction
    task they are false positives, because the category was asked for; in a
    version comparison they are the risks that are new in this round.
    """

    matches: tuple[Match, ...]
    unmatched_predicted: tuple[int, ...]
    unmatched_gold: tuple[int, ...]

    @property
    def true_positives(self) -> int:
        return len(self.matches)


def match_spans(predicted: list[str], gold: list[str], threshold: float = JACCARD_THRESHOLD) -> MatchResult:
    """
    Pairs spans one to one, best overlap first.

    One to one so that one side cannot cover five spans by quoting the whole
    contract once, and greedy from the best pair down so the pairing does not
    depend on the order the spans arrived in. Ties break on the lower indices,
    which makes the result the same on every run.
    """
    scored = [
        (jaccard(candidate, target), predicted_index, gold_index)
        for predicted_index, candidate in enumerate(predicted)
        for gold_index, target in enumerate(gold)
    ]
    scored = [entry for entry in scored if entry[0] >= threshold]
    scored.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))

    matches: list[Match] = []
    used_predicted: set[int] = set()
    used_gold: set[int] = set()

    for score, predicted_index, gold_index in scored:
        if predicted_index in used_predicted or gold_index in used_gold:
            continue
        used_predicted.add(predicted_index)
        used_gold.add(gold_index)
        matches.append(Match(predicted_index=predicted_index, gold_index=gold_index, score=score))

    return MatchResult(
        matches=tuple(matches),
        unmatched_predicted=tuple(index for index in range(len(predicted)) if index not in used_predicted),
        unmatched_gold=tuple(index for index in range(len(gold)) if index not in used_gold),
    )


def covers_any(candidate: str, gold: list[str], threshold: float = JACCARD_THRESHOLD) -> int | None:
    """
    The index of the gold span `candidate` covers best, or None.

    Unlike match_spans this allows a span to be reused, which is what recall
    needs: "did anything we flagged surface this clause?" is a question about the
    clause, not a budget.
    """
    best_index, best_score = None, 0.0

    for index, target in enumerate(gold):
        score = jaccard(candidate, target)
        if score >= threshold and score > best_score:
            best_index, best_score = index, score

    return best_index
