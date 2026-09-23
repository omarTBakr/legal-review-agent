"""Layer 1b: recall, the off-benchmark caveat, and the severity curve."""

import pytest

from evaluation.common.pipeline import ReviewResult
from evaluation.layer1_metrics.risk_recall import (
    RiskRecord,
    area_under_curve,
    build_records,
    gold_spans,
    score_risk_recall,
    severity_curve,
)
from schemas.legal_advice import LegalAdvice

UNLIMITED = "The Buyer's liability under this Agreement is unlimited."


def review(*risks, document="ACME-SUPPLY-AGREEMENT", error="") -> ReviewResult:
    return ReviewResult(document=document, advice=LegalAdvice(summary="s", key_risks=list(risks)), error=error)


def record(severity: str, category: str = "", span: str = "") -> RiskRecord:
    return RiskRecord(
        document="ACME",
        description="d",
        severity=severity,
        location="",
        quote="q",
        page=1,
        quote_verified=True,
        covered_category=category,
        covered_span=span,
    )


def test_only_risky_categories_are_in_the_denominator(contract, categories):
    """The annotated Parties span is real ground truth and is not a risk."""
    spans = gold_spans(contract, categories)

    assert [span.category for span in spans] == ["Uncapped Liability"]


def test_a_matching_quote_covers_the_span(contract, categories, make_risk):
    spans = gold_spans(contract, categories)
    records = build_records(review(make_risk("Unlimited liability", quote=UNLIMITED)), spans)

    assert records[0].covered_category == "Uncapped Liability"
    assert records[0].match_score == 1.0


def test_the_description_alone_does_not_cover_a_span(contract, categories, make_risk):
    """Descriptions share vocabulary with any liability clause; the quote is the claim."""
    spans = gold_spans(contract, categories)
    records = build_records(review(make_risk("The buyer's liability under this agreement is unlimited", quote="")), spans)

    assert records[0].on_benchmark is False


def test_a_finding_outside_the_41_categories_is_off_benchmark_not_a_false_positive(contract, categories, make_risk):
    indemnity = make_risk("One-sided indemnity", quote="The Buyer shall indemnify the Seller against all claims.")
    score, _ = score_risk_recall([(contract, review(indemnity))], categories)

    report = score.to_dict()

    assert report["off_benchmark_findings"]["count"] == 1
    assert "false positive" in report["note"].lower()
    assert report["covered_spans"] == 0


def test_two_risks_covering_one_span_count_once(contract, categories, make_risk):
    """Recall must not be inflated by the merge leaving a duplicate behind."""
    score, _ = score_risk_recall(
        [(contract, review(make_risk("a", quote=UNLIMITED), make_risk("b", quote=UNLIMITED)))],
        categories,
    )

    assert score.covered_spans == 1
    assert score.risks_reported == 2


def test_an_unverified_quote_still_counts_as_coverage_but_is_flagged(contract, categories, make_risk):
    unverified = make_risk("Unlimited liability", quote=UNLIMITED, quote_verified=False)
    score, _ = score_risk_recall([(contract, review(unverified))], categories)

    assert score.covered_spans == 1
    assert score.unverified_coverage == 1


def test_a_failed_review_counts_its_spans_as_missed(contract, categories):
    """A crash is a miss; excluding it would make an unreliable pipeline look accurate."""
    score, _ = score_risk_recall([(contract, review(error="LLMTimeoutError: too slow"))], categories)

    assert score.gold_spans == 1
    assert score.covered_spans == 0
    assert score.miss_rate == 1.0
    assert len(score.failed_documents) == 1


def test_the_curve_has_one_point_per_severity_and_accumulates():
    records = [record("critical", "Uncapped Liability", "a"), record("low", "Non-Compete", "b")]

    points = severity_curve(records, gold_total=2)

    assert [point["severity_at_least"] for point in points] == ["critical", "high", "medium", "low"]
    assert [point["risks"] for point in points] == [1, 1, 1, 2]
    assert points[0]["recall"] == 0.5
    assert points[-1]["recall"] == 1.0


def test_off_benchmark_findings_stay_in_the_precision_denominator():
    """precision_lower_bound is a lower bound on purpose, and the name says so."""
    records = [record("high", "Uncapped Liability", "a"), record("high")]

    points = severity_curve(records, gold_total=1)

    assert points[1]["precision_lower_bound"] == 0.5


def test_the_area_is_anchored_at_recall_zero():
    points = [
        {"recall": 0.5, "precision_lower_bound": 1.0},
        {"recall": 1.0, "precision_lower_bound": 1.0},
    ]

    assert area_under_curve(points) == pytest.approx(1.0)


def test_a_review_that_finds_nothing_has_area_zero():
    points = [{"recall": 0.0, "precision_lower_bound": 0.0}]

    assert area_under_curve(points) == 0.0


def test_the_report_refuses_to_be_mistaken_for_the_papers_aupr(contract, categories):
    score, _ = score_risk_recall([(contract, review())], categories)

    assert "NOT the CUAD paper's AUPR" in score.to_dict()["note"]
    assert "aupr_by_severity" in score.to_dict()


def test_records_survive_a_round_trip_through_the_results_file():
    original = record("high", "Uncapped Liability", "a")

    assert RiskRecord.from_dict(original.to_dict()) == original
