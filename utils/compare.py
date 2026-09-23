"""
What changed between two reviews of the same contract.

Contract review is iterative — you send a markup, they send one back — and until
now every upload was treated as the first one anyone had ever seen. The question
a lawyer actually has on round two is not "what are the risks" but "did they fix
6.1, and what did they slip in while they were there".

**No model call.** Two reviews already contain everything needed: each risk
carries the passage it rests on, verified against the document. Pairing them is
string matching, so the answer is deterministic, free, and the same every time
it is asked — which matters more here than nuance, because a comparison that
reported a different set of fixes each time it ran would be worthless.

Risks are paired on their quotes through `utils/spans.py`, the same Jaccard rule
the evaluation uses to decide a quote covers an annotated clause. A clause that
was reworded enough to fall below the threshold reads as one risk gone and
another arrived, which is the honest answer: at that point it is not the same
sentence any more, and saying "unchanged" would be worse than saying "new".
"""

from dataclasses import dataclass, field

from enums.RiskSeverity import RiskSeverity
from schemas.key_risk import KeyRisk
from utils.logger import get_logger
from utils.spans import match_spans

logger = get_logger(__name__)

# how a paired risk moved between the two rounds
UNCHANGED = "unchanged"
WORSE = "worse"
BETTER = "better"
# and the two that appear on one side only
FIXED = "fixed"
NEW = "new"

VERDICTS = (FIXED, BETTER, UNCHANGED, WORSE, NEW)


@dataclass(frozen=True)
class RiskChange:
    """One risk, and what became of it."""

    verdict: str
    description: str
    severity: str
    quote: str
    page: int | None = None
    # what it was in the earlier round, for a risk that survived into this one
    was_severity: str = ""
    was_description: str = ""
    match_score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "description": self.description,
            "severity": self.severity,
            "quote": self.quote,
            "page": self.page,
            "was_severity": self.was_severity,
            "was_description": self.was_description,
            "match_score": round(self.match_score, 4),
        }


@dataclass
class Comparison:
    """Two reviews of one document, side by side."""

    document: str
    base_document: str = ""
    changes: list[RiskChange] = field(default_factory=list)

    def of(self, verdict: str) -> list[RiskChange]:
        return [change for change in self.changes if change.verdict == verdict]

    @property
    def counts(self) -> dict[str, int]:
        return {verdict: len(self.of(verdict)) for verdict in VERDICTS}

    @property
    def resolved(self) -> int:
        """Risks the new round no longer has: the number the markup was for."""
        return len(self.of(FIXED))

    @property
    def introduced(self) -> int:
        """Risks that were not there before, whether or not anything was fixed."""
        return len(self.of(NEW))

    def to_dict(self) -> dict:
        return {
            "document": self.document,
            "base_document": self.base_document,
            "counts": self.counts,
            "resolved": self.resolved,
            "introduced": self.introduced,
            "net_severity_change": self.net_severity_change,
            "changes": [change.to_dict() for change in self.changes],
        }

    @property
    def net_severity_change(self) -> int:
        """
        Total severity gained minus lost, in bands.

        One number for "is this draft better or worse than the last one". A
        fixed critical is -3 against a new medium at +1, so trading one away for
        the other reads as the improvement it is. It is a summary and nothing
        more: a single new critical outweighs six fixed lows, and should.
        """
        total = 0

        for change in self.changes:
            if change.verdict == FIXED:
                total -= RiskSeverity.parse(change.severity).rank + 1
            elif change.verdict == NEW:
                total += RiskSeverity.parse(change.severity).rank + 1
            elif change.verdict in (WORSE, BETTER):
                total += RiskSeverity.parse(change.severity).rank - RiskSeverity.parse(change.was_severity).rank

        return total


def _verdict(before: KeyRisk, after: KeyRisk) -> str:
    """How a risk that survived into the new round moved."""
    difference = after.severity.rank - before.severity.rank

    if difference > 0:
        return WORSE
    if difference < 0:
        return BETTER

    return UNCHANGED


def compare_risks(base: list[KeyRisk], against: list[KeyRisk]) -> list[RiskChange]:
    """
    Pairs two rounds' risks and says what happened to each.

    `base` is the earlier round. Everything in it that nothing in `against`
    matched is `fixed`; everything in `against` that matched nothing is `new`.
    """
    matched = match_spans([risk.quote for risk in against], [risk.quote for risk in base])
    changes: list[RiskChange] = []

    for match in matched.matches:
        after, before = against[match.predicted_index], base[match.gold_index]
        changes.append(
            RiskChange(
                verdict=_verdict(before, after),
                description=after.description,
                severity=after.severity.value,
                quote=after.quote,
                page=after.page,
                was_severity=before.severity.value,
                was_description=before.description,
                match_score=match.score,
            )
        )

    for index in matched.unmatched_predicted:
        risk = against[index]
        changes.append(
            RiskChange(
                verdict=NEW,
                description=risk.description,
                severity=risk.severity.value,
                quote=risk.quote,
                page=risk.page,
            )
        )

    for index in matched.unmatched_gold:
        risk = base[index]
        changes.append(
            RiskChange(
                verdict=FIXED,
                description=risk.description,
                severity=risk.severity.value,
                quote=risk.quote,
                page=risk.page,
            )
        )

    # worst first, and within a verdict the ones that moved most: a reviewer with
    # five minutes should spend them on what got worse and what is new
    changes.sort(key=lambda change: (VERDICTS.index(change.verdict), -RiskSeverity.parse(change.severity).rank))

    return changes


def compare_advice(document: str, base_advice, against_advice, base_document: str = "") -> Comparison:
    """Compares two `LegalAdvice` objects for the same document."""
    changes = compare_risks(list(base_advice.key_risks), list(against_advice.key_risks))

    comparison = Comparison(document=document, base_document=base_document or document, changes=changes)

    logger.info(
        "compared %s against %s: %d fixed, %d new, %d worse",
        document,
        comparison.base_document,
        comparison.resolved,
        comparison.introduced,
        len(comparison.of(WORSE)),
    )

    return comparison


def pair_documents(base_keys: list[str], against_keys: list[str]) -> list[tuple[str, str]]:
    """
    Which document in the new round answers which in the old one.

    A key is `<prefix>/services-agreement-a1b2c3d4.pdf`: the same contract
    uploaded twice gets a different random suffix each time, so the pairing is on
    the name with that suffix removed. One document on each side is the common
    case and is paired whatever it is called, because re-uploading round two
    under a tidier filename should not read as "everything was fixed and
    everything is new".
    """
    if len(base_keys) == 1 and len(against_keys) == 1:
        return [(base_keys[0], against_keys[0])]

    by_stem = {_stem(key): key for key in base_keys}
    pairs = [(by_stem[_stem(key)], key) for key in against_keys if _stem(key) in by_stem]

    return pairs


def _stem(key: str) -> str:
    """A document key without its folder, extension or upload suffix."""
    name = key.rsplit("/", 1)[-1]
    name = name[: -len(".pdf")] if name.lower().endswith(".pdf") else name

    # build_run_artifacts appends "-" plus eight hex characters
    head, _, tail = name.rpartition("-")

    return head if head and len(tail) == 8 and all(character in "0123456789abcdef" for character in tail.lower()) else name
