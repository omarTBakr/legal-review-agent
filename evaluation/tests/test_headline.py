"""The end-to-end miss rate, which is the number that gets quoted."""

from evaluation.layer1_metrics.risk_recall import RiskRecallScore, RiskRecord
from evaluation.layer2_judge.escalation import build_queue
from evaluation.layer2_judge.judge import JudgeVerdict
from evaluation.run import end_to_end_miss_rate, findings_to_judge


def verdict(score: float, category: str = "Non-Compete", reason: str = "clear", error: str = "") -> JudgeVerdict:
    item = JudgeVerdict(
        document="ACME",
        category=category,
        description="d",
        severity="high",
        quote="q",
        quote_verified=True,
        ground_truth=f"the {category} clause",
    )
    item.total_score = score
    item.passed = score >= 0.75
    item.reason = reason
    item.error = error

    return item


def score(gold: int, covered: int) -> RiskRecallScore:
    return RiskRecallScore(gold_spans=gold, covered_spans=covered)


def test_a_clause_nothing_surfaced_is_a_miss(categories):
    headline = end_to_end_miss_rate(score(10, 0), [], [])

    assert headline["missed"] == 10
    assert headline["miss_rate"] == 1.0


def test_a_clause_the_judge_scored_zero_is_not_a_catch(categories):
    """The right clause with the wrong risk leaves a client exposed all the same."""
    verdicts = [verdict(0.0)]
    queue = build_queue(verdicts, categories.high_stakes)

    headline = end_to_end_miss_rate(score(10, 4), verdicts, queue)

    assert headline["surfaced_by_review"] == 4
    assert headline["failed_by_judge"] == 1
    assert headline["caught"] == 3
    assert headline["miss_rate"] == 0.7


def test_the_review_only_miss_rate_is_reported_alongside(categories):
    """So the judge's contribution to the headline is visible rather than baked in."""
    headline = end_to_end_miss_rate(score(10, 4), [verdict(0.0)], [])

    assert headline["review_only_miss_rate"] == 0.6
    assert headline["miss_rate"] == 0.7


def test_pending_human_matches_the_queue_even_when_the_judge_passed(categories):
    """A passed verdict escalated for hedging is still waiting on a human."""
    verdicts = [verdict(1.0, reason="Correct, though arguably too broad.")]
    queue = build_queue(verdicts, categories.high_stakes)

    headline = end_to_end_miss_rate(score(10, 1), verdicts, queue)

    assert len(queue) == 1
    assert headline["pending_human"] == 1


def test_a_clause_the_judge_already_failed_is_not_also_counted_as_pending(categories):
    verdicts = [verdict(0.0, reason="borderline")]
    queue = build_queue(verdicts, categories.high_stakes)

    headline = end_to_end_miss_rate(score(10, 1), verdicts, queue)

    assert headline["failed_by_judge"] == 1
    assert headline["pending_human"] == 0


def test_a_perfect_run_has_no_misses(categories):
    verdicts = [verdict(1.0)]

    assert end_to_end_miss_rate(score(1, 1), verdicts, [])["miss_rate"] == 0.0


def test_an_empty_run_does_not_divide_by_zero(categories):
    assert end_to_end_miss_rate(score(0, 0), [], [])["miss_rate"] == 0.0


def test_only_matched_findings_are_sent_to_the_judge():
    """The rubric grades against a clause, and an off-benchmark finding has none."""
    records = [
        RiskRecord(
            document="ACME",
            description="matched",
            severity="high",
            location="",
            quote="q",
            page=1,
            quote_verified=True,
            covered_category="Non-Compete",
            covered_span="the non-compete clause",
        ),
        RiskRecord(
            document="ACME",
            description="off benchmark",
            severity="high",
            location="",
            quote="q",
            page=1,
            quote_verified=True,
        ),
    ]

    findings = findings_to_judge(records)

    assert len(findings) == 1
    assert findings[0][1] == "Non-Compete"
    assert findings[0][3].description == "matched"
