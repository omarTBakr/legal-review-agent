"""Scoring a review of testingDocs against expected.json.

Three numbers, because they fail for different reasons and want different fixes:

  * **recall** — was the clause found at all, by keyword. The blunt "is the
    review still working" signal.
  * **severity agreement within one band** — of the clauses found, how often the
    severity was the expected one or its neighbour. Catches the failure the
    testingDocs README names: everything coming back `medium`.
  * **quote verification rate** — how many reported risks carry a quote the
    evidence check found in the document. Catches the model paraphrasing instead
    of copying, which no keyword test would notice.

`anchor_hits` sits beside recall: the clause was found *and* the quote was the
right sentence. Recall without anchor hits means the review is describing the
right risks off the wrong evidence.

Anything the review flags that no expectation covers is `extra_findings` —
counted and listed, never a false positive. The README that this file encodes
says the model may reasonably flag things the table does not.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from enums.RiskSeverity import RiskSeverity
from evaluation.common.pipeline import ReviewResult
from evaluation.common.spans import JACCARD_THRESHOLD, jaccard
from schemas.key_risk import KeyRisk
from utils.evidence import normalize

EXPECTED_FILE = Path(__file__).parent / "expected.json"

# testingDocs/ is the product's fixture directory, not the evaluation's
DOCUMENTS_DIR = Path(__file__).parent.parent.parent / "testingDocs"

# how far a severity may be from the expected band and still count as agreeing
SEVERITY_TOLERANCE = 1


@dataclass(frozen=True)
class Expectation:
    """One row of the testingDocs table."""

    id: str
    clause: str
    page: int
    severity_band: RiskSeverity
    what: str
    must_include: tuple[tuple[str, ...], ...]
    anchor: str
    # true for a clause the design says to *ask* about rather than flag — the
    # blank governing law. A review that raises it through needs_human has done
    # what the prompt asks, and scoring that as a missed risk would penalise it.
    satisfied_by_needs_human: bool = False


@dataclass(frozen=True)
class Document:
    """One fixture document and everything expected of a review of it."""

    filename: str
    pages: int
    expect_needs_human: bool
    expectations: tuple[Expectation, ...]

    @property
    def path(self) -> Path:
        return DOCUMENTS_DIR / self.filename


def load_expected(path: Path = EXPECTED_FILE) -> list[Document]:
    """Reads expected.json."""
    payload = json.loads(path.read_text(encoding="utf-8"))["documents"]

    return [
        Document(
            filename=filename,
            pages=int(entry["pages"]),
            expect_needs_human=bool(entry.get("expect_needs_human")),
            expectations=tuple(
                Expectation(
                    id=row["id"],
                    clause=row["clause"],
                    page=int(row["page"]),
                    severity_band=RiskSeverity.parse(row["severity_band"]),
                    what=row["what"],
                    must_include=tuple(tuple(group) for group in row["must_include"]),
                    anchor=row["anchor"],
                    satisfied_by_needs_human=bool(row.get("satisfied_by_needs_human")),
                )
                for row in entry["expectations"]
            ),
        )
        for filename, entry in payload.items()
    ]


def risk_haystack(risk: KeyRisk) -> str:
    """
    The text a keyword is looked for in.

    Description, location and quote together: the model sometimes puts the clause
    number in `location` and the substance in `description`, and which of them it
    chooses is not something the suite should be sensitive to.
    """
    return normalize(f"{risk.description} {risk.location} {risk.quote}")


def matches(risk: KeyRisk, expectation: Expectation) -> bool:
    """Whether this risk is the clause: every group of alternatives has a hit."""
    haystack = risk_haystack(risk)

    return all(any(normalize(word) in haystack for word in group) for group in expectation.must_include)


def severity_gap(found: RiskSeverity, expected: RiskSeverity) -> int:
    """How many bands apart two severities are."""
    return abs(found.rank - expected.rank)


def raises_question(review: ReviewResult, expectation: Expectation) -> bool:
    """
    Whether the review asked about this clause instead of flagging it.

    Only the first group of must_include is required, because the question is
    about the gap and cannot be expected to name it the way a finding would: the
    review asks "what is the governing law?", not "the governing law is blank".
    """
    if not review.advice.needs_human or not expectation.must_include:
        return False

    question = normalize(review.advice.question)

    return any(normalize(word) in question for word in expectation.must_include[0])


@dataclass
class ExpectationOutcome:
    """What happened to one expectation."""

    expectation: Expectation
    matched: list[KeyRisk] = field(default_factory=list)
    raised_as_question: bool = False

    @property
    def found(self) -> bool:
        return bool(self.matched) or self.raised_as_question

    @property
    def best_severity(self) -> RiskSeverity | None:
        """
        The severity of the matching risk closest to the expected band.

        Closest rather than highest: a review that reports the clause twice, once
        as critical and once as low, should be credited with having got it about
        right, and penalising it for the extra copy would be scoring verbosity.
        """
        if not self.matched:
            return None

        return min(
            (risk.severity for risk in self.matched), key=lambda severity: severity_gap(severity, self.expectation.severity_band)
        )

    @property
    def scoreable(self) -> bool:
        """
        Whether there is a reported risk here to judge the severity and quote of.

        An expectation satisfied only by the review asking a question has neither,
        so it counts towards recall and towards nothing else — averaging a missing
        severity in as a disagreement would punish the review for asking.
        """
        return bool(self.matched)

    @property
    def severity_agrees(self) -> bool:
        best = self.best_severity

        return best is not None and severity_gap(best, self.expectation.severity_band) <= SEVERITY_TOLERANCE

    @property
    def anchor_hit(self) -> bool:
        """Whether a matching risk quotes the sentence the clause actually turns on."""
        return any(jaccard(risk.quote, self.expectation.anchor) >= JACCARD_THRESHOLD for risk in self.matched)

    @property
    def page_hit(self) -> bool:
        """Whether a matching risk puts the clause on the right page."""
        return any(risk.page == self.expectation.page for risk in self.matched)

    def to_dict(self) -> dict:
        best = self.best_severity

        return {
            "id": self.expectation.id,
            "clause": self.expectation.clause,
            "expected_page": self.expectation.page,
            "expected_severity": self.expectation.severity_band.value,
            "found": self.found,
            "raised_as_question": self.raised_as_question,
            "reported_severity": best.value if best else None,
            "severity_agrees": self.severity_agrees,
            "anchor_hit": self.anchor_hit,
            "page_hit": self.page_hit,
            "matched_risks": [risk.description for risk in self.matched],
        }


@dataclass
class DocumentScore:
    """One document's outcomes and the review that produced them."""

    document: str
    outcomes: list[ExpectationOutcome] = field(default_factory=list)
    extra_findings: list[dict] = field(default_factory=list)
    risks_reported: int = 0
    quotes_verified: int = 0
    needs_human: bool = False
    expect_needs_human: bool = False
    error: str = ""

    @property
    def found(self) -> int:
        return sum(outcome.found for outcome in self.outcomes)

    def to_dict(self) -> dict:
        scoreable = [outcome for outcome in self.outcomes if outcome.scoreable]

        return {
            "document": self.document,
            "error": self.error,
            "expectations": len(self.outcomes),
            "found": self.found,
            "recall": round(self.found / len(self.outcomes), 4) if self.outcomes else 0.0,
            "anchor_hits": sum(outcome.anchor_hit for outcome in scoreable),
            "page_hits": sum(outcome.page_hit for outcome in scoreable),
            "severity_agreement": (
                round(sum(outcome.severity_agrees for outcome in scoreable) / len(scoreable), 4) if scoreable else 0.0
            ),
            "risks_reported": self.risks_reported,
            "quotes_verified": self.quotes_verified,
            "quote_verification_rate": round(self.quotes_verified / self.risks_reported, 4) if self.risks_reported else 0.0,
            "needs_human": self.needs_human,
            "expect_needs_human": self.expect_needs_human,
            "needs_human_as_expected": self.needs_human == self.expect_needs_human,
            "missed": [outcome.to_dict() for outcome in self.outcomes if not outcome.found],
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "extra_findings": self.extra_findings,
        }


def score_document(document: Document, review: ReviewResult) -> DocumentScore:
    """Pairs every expectation with the risks that match it."""
    score = DocumentScore(
        document=document.filename,
        expect_needs_human=document.expect_needs_human,
        needs_human=review.advice.needs_human,
        error=review.error,
        risks_reported=len(review.advice.key_risks),
        quotes_verified=sum(risk.quote_verified for risk in review.advice.key_risks),
    )

    claimed: set[int] = set()

    for expectation in document.expectations:
        outcome = ExpectationOutcome(expectation=expectation)
        for index, risk in enumerate(review.advice.key_risks):
            if matches(risk, expectation):
                outcome.matched.append(risk)
                claimed.add(index)

        if not outcome.matched and expectation.satisfied_by_needs_human:
            outcome.raised_as_question = raises_question(review, expectation)

        score.outcomes.append(outcome)

    score.extra_findings = [
        {
            "description": risk.description,
            "severity": risk.severity.value,
            "location": risk.location,
            "page": risk.page,
            "quote_verified": risk.quote_verified,
        }
        for index, risk in enumerate(review.advice.key_risks)
        if index not in claimed
    ]

    return score


def summarize(scores: list[DocumentScore]) -> dict:
    """The three headline numbers across both documents."""
    outcomes = [outcome for score in scores for outcome in score.outcomes]
    found = [outcome for outcome in outcomes if outcome.found]
    scoreable = [outcome for outcome in outcomes if outcome.scoreable]
    risks = sum(score.risks_reported for score in scores)
    verified = sum(score.quotes_verified for score in scores)

    return {
        "expectations": len(outcomes),
        "found": len(found),
        "recall": round(len(found) / len(outcomes), 4) if outcomes else 0.0,
        "raised_as_question": sum(outcome.raised_as_question for outcome in outcomes),
        "scoreable": len(scoreable),
        "anchor_hits": sum(outcome.anchor_hit for outcome in scoreable),
        "anchor_hit_rate": (round(sum(outcome.anchor_hit for outcome in scoreable) / len(scoreable), 4) if scoreable else 0.0),
        "severity_agreement_within_one_band": (
            round(sum(outcome.severity_agrees for outcome in scoreable) / len(scoreable), 4) if scoreable else 0.0
        ),
        "risks_reported": risks,
        "quote_verification_rate": round(verified / risks, 4) if risks else 0.0,
        "extra_findings": sum(len(score.extra_findings) for score in scores),
        "needs_human_as_expected": sum(score.needs_human == score.expect_needs_human for score in scores),
        "documents": len(scores),
        "failed_documents": [score.document for score in scores if score.error],
        "missed": [outcome.expectation.id for outcome in outcomes if not outcome.found],
    }
