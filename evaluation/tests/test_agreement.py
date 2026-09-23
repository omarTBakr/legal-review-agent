"""Layer 3: the form's answers, and the kappa that refuses to be computed."""

import json

from evaluation.layer3_human.agreement import cohens_kappa, human_pass, judge_vs_human, load_reviews, report
from evaluation.layer3_human.review import ask, collect, render


def human(risk_identified="yes", judge_pass=True, reviewer="ob", quote="q", category="Uncapped Liability") -> dict:
    return {
        "reviewer": reviewer,
        "document": "ACME",
        "category": category,
        "quote": quote,
        "risk_identified": risk_identified,
        "judge_pass": judge_pass,
    }


def test_yes_and_no_reduce_to_one_bit():
    assert human_pass(human("yes")) is True
    assert human_pass(human("no")) is False


def test_partially_is_not_folded_into_either():
    """It is exactly the case the judge's 0-or-1 rubric cannot express."""
    assert human_pass(human("partially")) is None


def test_agreement_counts_only_the_items_the_human_settled():
    agreement = judge_vs_human([human("yes", True), human("partially", True), human("no", False)])

    assert agreement.compared == 2
    assert agreement.agreed == 2
    assert agreement.undecided_by_human == 1
    assert agreement.rate == 1.0


def test_the_two_directions_of_disagreement_are_reported_separately():
    """A judge that passes what a human fails is a different problem from the reverse."""
    agreement = judge_vs_human([human("no", True), human("yes", False)])

    assert agreement.judge_pass_human_fail == 1
    assert agreement.judge_fail_human_pass == 1
    assert agreement.rate == 0.0


def test_kappa_is_none_with_one_annotator():
    assert cohens_kappa([human(reviewer="ob"), human(reviewer="ob", quote="q2")]) is None


def test_the_report_explains_why_kappa_is_none_rather_than_printing_a_number():
    payload = report([human(reviewer="ob")])

    assert payload["cohens_kappa"] is None
    assert "two annotators" in payload["cohens_kappa_note"]
    assert "not chance-corrected" in payload["cohens_kappa_note"]


def test_kappa_is_none_with_no_annotators_at_all():
    assert cohens_kappa([]) is None
    assert report([])["cohens_kappa"] is None


def test_kappa_is_computed_once_two_annotators_share_items():
    reviews = [
        human(reviewer="ob", quote="q1", risk_identified="yes"),
        human(reviewer="ob", quote="q2", risk_identified="no"),
        human(reviewer="sm", quote="q1", risk_identified="yes"),
        human(reviewer="sm", quote="q2", risk_identified="no"),
    ]

    assert cohens_kappa(reviews) == 1.0
    assert report(reviews)["cohens_kappa"] == 1.0


def test_two_annotators_who_never_overlap_still_get_none():
    reviews = [human(reviewer="ob", quote="q1"), human(reviewer="sm", quote="q2")]

    assert cohens_kappa(reviews) is None


def test_kappa_is_none_when_both_annotators_always_said_the_same_thing():
    """Expected agreement is 1, so kappa is undefined; a 0 there would be a lie."""
    reviews = [
        human(reviewer="ob", quote="q1", risk_identified="yes"),
        human(reviewer="sm", quote="q1", risk_identified="yes"),
    ]

    assert cohens_kappa(reviews) is None


def test_kappa_is_zero_when_two_annotators_agree_no_more_than_chance():
    reviews = [
        human(reviewer="ob", quote="q1", risk_identified="yes"),
        human(reviewer="ob", quote="q2", risk_identified="no"),
        human(reviewer="sm", quote="q1", risk_identified="no"),
        human(reviewer="sm", quote="q2", risk_identified="yes"),
    ]

    assert cohens_kappa(reviews) == -1.0


def test_reviews_load_from_a_run_directory(tmp_path):
    (tmp_path / "human_reviews.jsonl").write_text(json.dumps(human()) + "\n", encoding="utf-8")

    assert len(load_reviews(tmp_path)) == 1


def test_a_missing_reviews_file_is_no_reviews(tmp_path):
    assert load_reviews(tmp_path) == []


def test_the_form_shows_ground_truth_before_the_judges_verdict():
    """A reviewer who reads the grade first tends to agree with it."""
    rendered = render(queue_item(), 1, 1)

    assert rendered.index("GROUND TRUTH") < rendered.index("SYSTEM OUTPUT") < rendered.index("JUDGE:")


def test_the_form_rejects_an_answer_that_is_not_on_the_menu():
    answers = iter(["maybe", "y"])

    assert ask("Correct?", {"y": "yes", "n": "no"}, reader=lambda _: next(answers)) == "yes"


def test_skipping_an_item_records_nothing():
    """A forced answer to a question the reviewer cannot answer is a guess."""
    assert collect(queue_item(), "ob", reader=lambda _: "s") is None


def queue_item(**overrides) -> dict:
    """One line of escalations.jsonl, as JudgeVerdict.to_dict writes it."""
    return {
        "document": "ACME",
        "category": "Uncapped Liability",
        "quote": "q",
        "ground_truth": "the annotated clause",
        "total_score": 0.5,
        "pass": True,
        "reason": "looks right",
        "escalation_reasons": ["high-stakes category (Uncapped Liability)"],
        **overrides,
    }


def test_a_completed_form_carries_the_judges_verdict_with_it():
    answers = iter(["y", "e", "c", "y", "y", "no notes"])
    record = collect(queue_item(), "ob", reader=lambda _: next(answers))

    assert record["risk_identified"] == "yes"
    assert record["span_accuracy"] == "exact"
    assert record["severity"] == "correct"
    assert record["would_flag_to_client"] == "yes"
    assert record["judge_pass"] is True
    assert record["judge_total_score"] == 0.5
    assert record["escalation_reasons"] == ["high-stakes category (Uncapped Liability)"]
    assert record["notes"] == "no notes"
    assert record["reviewer"] == "ob"
