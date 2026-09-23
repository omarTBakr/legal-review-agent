"""Layer 2: reading the judge's reply, and the escalation rules."""

import json

import pytest

from evaluation.layer2_judge.escalation import build_queue, escalation_reasons, hedges, read_queue, should_escalate, write_queue
from evaluation.layer2_judge.judge import JudgeVerdict, score_verdict, summarize
from interfaces.llm_interface import LLMInterface

PERFECT = {
    "correctness": 1,
    "completeness": 1,
    "precision": 1,
    "explanation": 1,
    "total_score": 1.0,
    "pass": True,
    "reason": "ok",
}


def blank() -> JudgeVerdict:
    return JudgeVerdict(
        document="ACME",
        category="Uncapped Liability",
        description="d",
        severity="high",
        quote="q",
        quote_verified=True,
        ground_truth="g",
    )


def graded(score: float, reason: str = "clear", category: str = "Non-Compete", error: str = "") -> JudgeVerdict:
    verdict = blank()
    verdict.category = category
    verdict.total_score = score
    verdict.reason = reason
    verdict.error = error
    verdict.passed = score >= 0.75

    return verdict


def test_a_fenced_reply_is_parsed_by_the_products_own_parser():
    """The judge reply goes through the parser the pipeline already trusts."""
    raw = "Here you go:\n```json\n" + json.dumps(PERFECT) + "\n```"

    assert LLMInterface.parse_json_object(raw, "judge") == PERFECT


def test_a_reply_truncated_at_the_token_limit_is_repaired():
    raw = '{"correctness": 1, "completeness": 1, "precision": 0, "explanation": 1, "reason": "cut off'

    parsed = LLMInterface.parse_json_object(raw, "judge")

    assert parsed["correctness"] == 1
    assert parsed["precision"] == 0


def test_a_reply_with_no_json_at_all_is_an_error():
    from exceptions.llm import LLMResponseError

    with pytest.raises(LLMResponseError):
        LLMInterface.parse_json_object("I cannot grade this.", "judge")


def test_the_total_is_recomputed_from_the_dimensions_not_believed():
    """Judges do arithmetic badly, and the escalation threshold reads the total."""
    verdict = score_verdict({**PERFECT, "correctness": 0, "completeness": 0, "precision": 0, "total_score": 0.95}, blank())

    assert verdict.total_score == 0.25
    assert verdict.reported_total == 0.95
    assert verdict.passed is False


def test_pass_follows_from_the_score_whatever_the_reply_claimed():
    verdict = score_verdict({**PERFECT, "pass": False}, blank())

    assert verdict.passed is True


def test_a_hedged_dimension_score_is_zero_not_rounded_up():
    verdict = score_verdict({**PERFECT, "precision": "partial"}, blank())

    assert verdict.precision == 0
    assert verdict.total_score == 0.75


def test_a_boolean_dimension_is_accepted():
    verdict = score_verdict({"correctness": True, "completeness": False, "precision": 1, "explanation": 0}, blank())

    assert (verdict.correctness, verdict.completeness, verdict.precision, verdict.explanation) == (1, 0, 1, 0)


def test_a_missing_dimension_is_zero():
    verdict = score_verdict({"correctness": 1}, blank())

    assert verdict.total_score == 0.25


def test_hedging_is_detected_in_the_judges_own_words():
    assert hedges("This is borderline.")
    assert hedges("I am uncertain whether the span is complete.")
    assert not hedges("The system named the wrong clause.")


def test_an_unclear_score_escalates(categories):
    reasons = escalation_reasons(graded(0.5), categories.high_stakes)

    assert any("unclear score" in reason for reason in reasons)


def test_a_clearly_wrong_finding_does_not_escalate_on_its_score(categories):
    """Below 0.3 the finding is plainly wrong; a human adds nothing by confirming it."""
    assert not should_escalate(graded(0.25), categories.high_stakes)


def test_a_perfect_finding_does_not_escalate_on_its_score(categories):
    assert not should_escalate(graded(1.0), categories.high_stakes)


def test_a_hedged_reason_escalates_even_at_a_confident_score(categories):
    reasons = escalation_reasons(graded(1.0, reason="Correct, though arguably too broad."), categories.high_stakes)

    assert reasons == ("the judge hedged",)


def test_a_high_stakes_category_always_escalates(categories):
    reasons = escalation_reasons(graded(1.0, category="Uncapped Liability"), categories.high_stakes)

    assert reasons == ("high-stakes category (Uncapped Liability)",)


def test_an_ungraded_finding_escalates(categories):
    reasons = escalation_reasons(graded(0.0, error="LLMTimeoutError: too slow"), categories.high_stakes)

    assert reasons == ("the judge failed to grade it",)


def test_every_reason_is_reported_not_just_the_first(categories):
    verdict = graded(0.5, reason="borderline", category="Uncapped Liability")

    assert len(escalation_reasons(verdict, categories.high_stakes)) == 3


def test_the_queue_puts_the_worst_score_first(categories):
    queue = build_queue([graded(0.75), graded(0.5), graded(0.5, category="Uncapped Liability")], categories.high_stakes)

    assert [item.verdict.total_score for item in queue] == [0.5, 0.5, 0.75]


def test_the_queue_round_trips_through_jsonl(tmp_path, categories):
    queue = build_queue([graded(0.5)], categories.high_stakes)
    path = write_queue(queue, tmp_path / "escalations.jsonl")

    restored = read_queue(path)

    assert restored[0]["category"] == "Non-Compete"
    assert restored[0]["escalation_reasons"] == ["unclear score (0.50)"]


def test_the_queue_is_appended_so_an_interrupted_run_keeps_what_it_wrote(tmp_path, categories):
    path = tmp_path / "escalations.jsonl"
    write_queue(build_queue([graded(0.5)], categories.high_stakes), path)
    write_queue(build_queue([graded(0.5, category="Exclusivity")], categories.high_stakes), path)

    assert len(read_queue(path)) == 2


def test_the_summary_reports_each_dimension_not_only_the_mean():
    verdicts = [score_verdict({**PERFECT, "precision": 0}, blank()), score_verdict(PERFECT, blank())]

    summary = summarize(verdicts)

    assert summary["by_dimension"]["precision"] == 0.5
    assert summary["by_dimension"]["correctness"] == 1.0
    assert summary["mean_total_score"] == 0.875


def test_the_summary_counts_the_judges_arithmetic_mistakes():
    verdicts = [score_verdict({**PERFECT, "correctness": 0, "total_score": 1.0}, blank())]

    assert summarize(verdicts)["arithmetic_disagreements"] == 1


def test_a_verdict_survives_a_round_trip_through_the_results_file():
    verdict = score_verdict(PERFECT, blank())
    restored = JudgeVerdict.from_dict(verdict.to_dict())

    assert restored.passed is True
    assert restored.total_score == verdict.total_score
