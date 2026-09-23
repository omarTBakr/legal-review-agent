"""The fast suite: matching testingDocs expectations, and the three numbers."""

from enums.RiskSeverity import RiskSeverity
from evaluation.common.pipeline import ReviewResult
from evaluation.fixtures.scorer import (
    DOCUMENTS_DIR,
    Expectation,
    load_expected,
    matches,
    score_document,
    severity_gap,
    summarize,
)
from schemas.legal_advice import LegalAdvice

ANCHOR = "The Client's aggregate liability under this Agreement is unlimited and is not capped by reference to the fees paid."


def expectation(
    must_include=(("liability",), ("unlimited", "uncapped")),
    severity="critical",
    page=2,
    satisfied_by_needs_human=False,
) -> Expectation:
    return Expectation(
        id="services-6.1-client-unlimited-liability",
        clause="6.1",
        page=page,
        severity_band=RiskSeverity.parse(severity),
        what="unlimited client liability",
        must_include=tuple(tuple(group) for group in must_include),
        anchor=ANCHOR,
        satisfied_by_needs_human=satisfied_by_needs_human,
    )


def review(*risks, document="services-agreement.pdf") -> ReviewResult:
    return ReviewResult(document=document, advice=LegalAdvice(summary="s", key_risks=list(risks)))


def document(*expectations, expect_needs_human=False):
    from evaluation.fixtures.scorer import Document

    return Document(
        filename="services-agreement.pdf",
        pages=4,
        expect_needs_human=expect_needs_human,
        expectations=tuple(expectations),
    )


def test_every_group_of_alternatives_must_hit(make_risk):
    assert matches(make_risk("The client's liability is unlimited"), expectation())
    assert not matches(make_risk("The client's liability is capped"), expectation())
    assert not matches(make_risk("Everything is unlimited"), expectation())


def test_a_synonym_from_the_same_group_is_enough(make_risk):
    """'uncapped' where the README says 'unlimited' is phrasing, not a miss."""
    assert matches(make_risk("The client's liability is uncapped"), expectation())


def test_the_keyword_is_looked_for_across_description_location_and_quote(make_risk):
    risk = make_risk("Exposure is not bounded", location="clause 6.1 liability", quote="unlimited")

    assert matches(risk, expectation())


def test_markdown_in_a_quote_does_not_hide_a_keyword(make_risk):
    assert matches(make_risk("**liability** is **unlimited**"), expectation())


def test_a_found_clause_with_the_right_quote_is_an_anchor_hit(make_risk):
    score = score_document(document(expectation()), review(make_risk("Unlimited liability", quote=ANCHOR)))

    assert score.outcomes[0].found is True
    assert score.outcomes[0].anchor_hit is True


def test_describing_the_risk_while_quoting_the_wrong_sentence_is_found_but_not_anchored(make_risk):
    """Two different failures; the suite has to tell them apart."""
    wrong = make_risk("Unlimited liability", quote="The Supplier shall provide the services described in each Order Form.")
    score = score_document(document(expectation()), review(wrong))

    assert score.outcomes[0].found is True
    assert score.outcomes[0].anchor_hit is False


def test_severity_within_one_band_agrees(make_risk):
    score = score_document(document(expectation(severity="critical")), review(make_risk("liability is unlimited", "high")))

    assert score.outcomes[0].severity_agrees is True


def test_severity_two_bands_out_disagrees(make_risk):
    """The README's complaint: everything coming back medium."""
    score = score_document(document(expectation(severity="critical")), review(make_risk("liability is unlimited", "medium")))

    assert score.outcomes[0].severity_agrees is False


def test_a_duplicate_risk_is_credited_by_its_closest_severity(make_risk):
    both = review(make_risk("liability is unlimited", "low"), make_risk("liability is unlimited", "critical"))
    score = score_document(document(expectation(severity="critical")), both)

    assert score.outcomes[0].best_severity is RiskSeverity.CRITICAL


def test_severity_gap_counts_bands():
    assert severity_gap(RiskSeverity.LOW, RiskSeverity.CRITICAL) == 3
    assert severity_gap(RiskSeverity.HIGH, RiskSeverity.CRITICAL) == 1


def test_an_unmatched_risk_is_an_extra_finding_not_a_false_positive(make_risk):
    score = score_document(document(expectation()), review(make_risk("The indemnity is one-sided")))

    assert score.extra_findings[0]["description"] == "The indemnity is one-sided"
    assert score.found == 0


def test_the_quote_verification_rate_is_over_every_reported_risk(make_risk):
    risks = review(
        make_risk("liability is unlimited", quote=ANCHOR, quote_verified=True),
        make_risk("something paraphrased", quote="not in the document", quote_verified=False),
    )
    score = score_document(document(expectation()), risks)

    assert score.to_dict()["quote_verification_rate"] == 0.5


def test_the_page_is_checked_against_the_table(make_risk):
    score = score_document(document(expectation(page=2)), review(make_risk("liability is unlimited", page=2)))

    assert score.outcomes[0].page_hit is True


def test_needs_human_is_scored_against_the_expectation(make_risk):
    result = review(make_risk("liability is unlimited"))
    result.advice.needs_human = True

    assert score_document(document(expectation(), expect_needs_human=True), result).to_dict()["needs_human_as_expected"]


def test_the_summary_divides_severity_agreement_by_what_was_found(make_risk):
    """A clause that was never found must not count as a severity disagreement."""
    scores = [score_document(document(expectation(), expectation()), review(make_risk("liability is unlimited", "critical")))]

    summary = summarize(scores)

    assert summary["recall"] == 1.0
    assert summary["severity_agreement_within_one_band"] == 1.0


def test_a_clause_the_design_says_to_ask_about_counts_as_found_when_asked():
    """The blank governing law: a review that raises it through needs_human did right."""
    result = review()
    result.advice.needs_human = True
    result.advice.question = "What governing law did the parties intend for clause 12.1?"

    expected = expectation(must_include=(("governing law",), ("blank", "to be agreed")), satisfied_by_needs_human=True)
    outcome = score_document(document(expected), result).outcomes[0]

    assert outcome.found is True
    assert outcome.raised_as_question is True


def test_a_question_that_asks_about_something_else_does_not_count():
    result = review()
    result.advice.needs_human = True
    result.advice.question = "Which party is the client?"

    expected = expectation(must_include=(("governing law",), ("blank",)), satisfied_by_needs_human=True)

    assert score_document(document(expected), result).outcomes[0].found is False


def test_the_same_clause_without_the_flag_is_a_plain_miss():
    """Only the expectations that opt in may be satisfied by a question."""
    result = review()
    result.advice.needs_human = True
    result.advice.question = "What governing law did the parties intend?"

    expected = expectation(must_include=(("governing law",), ("blank",)))

    assert score_document(document(expected), result).outcomes[0].found is False


def test_a_question_raised_clause_is_not_averaged_into_the_severity_rate(make_risk):
    """It has no severity to agree with; counting it as a disagreement punishes asking."""
    result = review(make_risk("liability is unlimited", "critical"))
    result.advice.needs_human = True
    result.advice.question = "What governing law applies?"

    asked = expectation(must_include=(("governing law",), ("blank",)), satisfied_by_needs_human=True)
    summary = summarize([score_document(document(expectation(), asked), result)])

    assert summary["recall"] == 1.0
    assert summary["scoreable"] == 1
    assert summary["raised_as_question"] == 1
    assert summary["severity_agreement_within_one_band"] == 1.0


def test_the_shipped_expectations_are_loadable_and_point_at_real_pdfs():
    documents = load_expected()

    assert {document.filename for document in documents} == {"services-agreement.pdf", "mutual-nda.pdf"}
    for entry in documents:
        assert entry.path.is_file(), entry.path
        assert entry.expectations


def test_every_shipped_anchor_really_appears_in_its_document():
    """
    The anchors are quoted out of the parsed PDFs, so this catches an expectation
    that drifted from the document it describes — a broken suite that would
    otherwise read as a broken review.
    """
    from parsers.pymupdf_parser import parse_pdf_pages
    from utils.evidence import normalize

    for entry in load_expected():
        pages = [normalize(page) for page in parse_pdf_pages(DOCUMENTS_DIR / entry.filename)]
        for row in entry.expectations:
            assert normalize(row.anchor) in pages[row.page - 1], f"{row.id} is not on page {row.page}"
