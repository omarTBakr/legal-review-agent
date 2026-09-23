"""The expert layer, with no model behind it.

Every test here feeds `score_adjudication` a reply the way a model would have
returned it, or drives `adjudicate` through a stub. The interesting behaviour is
what happens to a reply that does not follow the rubric — those are the ones that
would quietly turn into a score.
"""

import json

import pytest

from evaluation.layer2_judge.judge import JudgeVerdict
from evaluation.layer3_expert.adjudicator import (
    Adjudication,
    adjudicate,
    adjudicate_all,
    human_queue,
    load_adjudications,
    score_adjudication,
    summarize,
    write_adjudications,
)
from evaluation.layer3_expert.agreement import expert_vs_human
from evaluation.run import check_distinct_models, end_to_end_miss_rate

GOOD_REPLY = {
    "decision": "upheld",
    "correct_severity": "high",
    "severity_was": "correct",
    "judge_was": "wrong",
    "material_omission": "",
    "needs_human": False,
    "confidence": "high",
    "reason": "The clause caps the client's liability and not the supplier's.",
}


def blank(**overrides) -> Adjudication:
    fields = {"document": "contract-a", "category": "Uncapped Liability", "quote": "the quote"}

    return Adjudication(**{**fields, **overrides})


def queue_item(**overrides) -> dict:
    item = {
        "document": "contract-a",
        "category": "Uncapped Liability",
        "quote": "the quote",
        "ground_truth": "the annotated clause",
        "description": "liability is unlimited",
        "severity": "critical",
        "quote_verified": True,
        "correctness": 1,
        "completeness": 0,
        "precision": 1,
        "explanation": 0,
        "total_score": 0.5,
        "pass": False,
        "reason": "borderline",
        "escalation_reasons": ["unclear score (0.50)"],
    }

    return {**item, **overrides}


class StubLLM:
    """Returns a canned reply, or raises, and remembers what it was asked."""

    def __init__(self, reply="", error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.calls: list[dict] = []

    async def complete(self, prompt, **variables):
        self.calls.append(variables)
        if self.error:
            raise self.error

        return self.reply

    async def aclose(self):
        pass


def test_a_good_reply_is_scored_as_given():
    result = score_adjudication(GOOD_REPLY, blank())

    assert result.decision == "upheld"
    assert result.credit == 1.0
    assert result.needs_human is False
    assert result.judge_was == "wrong"


def test_partial_is_worth_half_a_catch():
    assert score_adjudication({**GOOD_REPLY, "decision": "partial"}, blank()).credit == 0.5
    assert score_adjudication({**GOOD_REPLY, "decision": "overturned"}, blank()).credit == 0.0


def test_an_invented_decision_scores_nothing_and_goes_to_a_human():
    """A fourth verdict is not mapped onto the nearest real one."""
    result = score_adjudication({**GOOD_REPLY, "decision": "mostly right"}, blank())

    assert result.decision == ""
    assert result.credit == 0.0
    assert result.needs_human is True


def test_low_confidence_goes_to_a_human_whatever_the_reply_said():
    result = score_adjudication({**GOOD_REPLY, "confidence": "low", "needs_human": False}, blank())

    assert result.needs_human is True


def test_the_expert_can_ask_for_a_human_on_a_confident_decision():
    result = score_adjudication({**GOOD_REPLY, "needs_human": True}, blank())

    assert result.decision == "upheld"
    assert result.needs_human is True


@pytest.mark.parametrize("field", ["correct_severity", "severity_was", "judge_was", "confidence"])
def test_a_value_outside_the_vocabulary_is_dropped(field):
    result = score_adjudication({**GOOD_REPLY, field: "somewhat"}, blank())

    assert getattr(result, field) == ""


async def test_adjudicate_carries_both_models_output_into_the_prompt():
    llm = StubLLM(reply=json.dumps(GOOD_REPLY))

    result = await adjudicate(llm, queue_item())

    assert result.decision == "upheld"
    sent = llm.calls[0]
    assert sent["ground_truth"] == "the annotated clause"
    assert sent["description"] == "liability is unlimited"
    assert sent["total_score"] == 0.5
    assert sent["escalation_reasons"] == "unclear score (0.50)"


async def test_a_failed_call_becomes_an_error_that_needs_a_human():
    llm = StubLLM(error=RuntimeError("502 upstream"))

    result = await adjudicate(llm, queue_item())

    assert result.error.startswith("RuntimeError")
    assert result.decision == ""
    assert result.needs_human is True


async def test_unparseable_output_is_an_error_not_a_crash():
    llm = StubLLM(reply="I would rather explain this in prose.")

    result = await adjudicate(llm, queue_item())

    assert result.error
    assert result.needs_human is True


async def test_a_limit_takes_the_worst_scores_first():
    """build_queue sorts worst first, so a limit is a prefix."""
    llm = StubLLM(reply=json.dumps(GOOD_REPLY))
    queue = [queue_item(quote="worst", total_score=0.0), queue_item(quote="better", total_score=0.75)]

    results = await adjudicate_all(llm, queue, concurrency=2, limit=1)

    assert [result.quote for result in results] == ["worst"]


def test_summarize_names_the_two_disagreements_separately():
    adjudications = [
        score_adjudication({**GOOD_REPLY, "decision": "overturned"}, blank(judge_pass=True, quote="a")),
        score_adjudication({**GOOD_REPLY, "decision": "upheld"}, blank(judge_pass=False, quote="b")),
        score_adjudication({**GOOD_REPLY, "decision": "upheld"}, blank(judge_pass=True, quote="c")),
        blank(quote="d", error="RuntimeError: nope"),
    ]

    summary = summarize(adjudications)

    assert summary["settled"] == 3
    assert summary["errors"] == 1
    assert summary["judge_passed_expert_overturned"] == 1
    assert summary["judge_failed_expert_upheld"] == 1
    assert summary["disagreed_with_judge"] == 2
    assert summary["still_needs_human"] == 1


def test_the_human_queue_drops_what_the_expert_settled():
    settled = score_adjudication(GOOD_REPLY, blank(quote="settled"))
    unsettled = score_adjudication({**GOOD_REPLY, "needs_human": True, "reason": "turns on the bargain"}, blank(quote="open"))
    queue = [queue_item(quote="settled"), queue_item(quote="open")]

    remaining = human_queue(queue, [settled, unsettled])

    assert [item["quote"] for item in remaining] == ["open"]
    assert remaining[0]["expert_reason"] == "turns on the bargain"
    assert remaining[0]["expert_decision"] == "upheld"


def test_an_item_the_expert_never_saw_stays_in_the_human_queue():
    """--adjudicate-limit must not silently clear the queue behind it."""
    remaining = human_queue([queue_item(quote="never asked")], [])

    assert [item["quote"] for item in remaining] == ["never asked"]
    assert "expert_decision" not in remaining[0]


def test_adjudications_survive_a_round_trip(tmp_path):
    original = [score_adjudication(GOOD_REPLY, blank(escalation_reasons=("unclear score (0.50)",)))]

    write_adjudications(original, tmp_path / "adjudications.jsonl")
    loaded = load_adjudications(tmp_path)

    assert loaded[0].to_dict() == original[0].to_dict()
    assert loaded[0].escalation_reasons == ("unclear score (0.50)",)


def test_loading_from_a_directory_without_adjudications_is_empty(tmp_path):
    assert load_adjudications(tmp_path) == []


class FakeRiskScore:
    """Just the two fields end_to_end_miss_rate reads."""

    def __init__(self, gold_spans: int, covered_spans: int):
        self.gold_spans = gold_spans
        self.covered_spans = covered_spans
        self.miss_rate = (gold_spans - covered_spans) / gold_spans if gold_spans else 0.0


def verdict(**overrides) -> JudgeVerdict:
    fields = {
        "document": "contract-a",
        "category": "Uncapped Liability",
        "description": "d",
        "severity": "critical",
        "quote": "the quote",
        "quote_verified": True,
        "ground_truth": "the annotated clause",
    }

    return JudgeVerdict(**{**fields, **overrides})


def test_the_expert_rescues_a_clause_the_judge_failed():
    """A clause the judge scored 0 and the expert upheld is caught after all."""
    score = FakeRiskScore(gold_spans=10, covered_spans=6)
    verdicts = [verdict(total_score=0.0)]
    rescued = score_adjudication(GOOD_REPLY, blank(ground_truth="the annotated clause"))

    without = end_to_end_miss_rate(score, verdicts, [], [])
    with_expert = end_to_end_miss_rate(score, verdicts, [], [rescued])

    assert without["caught"] == 5
    assert with_expert["caught"] == 6
    assert with_expert["rescued_by_expert"] == 1
    assert with_expert["miss_rate"] == 0.4


def test_the_expert_overturns_a_clause_the_judge_passed():
    score = FakeRiskScore(gold_spans=10, covered_spans=6)
    verdicts = [verdict(total_score=1.0, passed=True)]
    overturned = score_adjudication({**GOOD_REPLY, "decision": "overturned"}, blank(ground_truth="the annotated clause"))

    report = end_to_end_miss_rate(score, verdicts, [], [overturned])

    assert report["overturned_by_expert"] == 1
    assert report["caught"] == 5
    assert report["missed"] == 5


def test_a_partial_costs_half_a_clause():
    score = FakeRiskScore(gold_spans=10, covered_spans=6)
    partial = score_adjudication({**GOOD_REPLY, "decision": "partial"}, blank(ground_truth="the annotated clause"))

    report = end_to_end_miss_rate(score, [verdict(total_score=1.0, passed=True)], [], [partial])

    assert report["caught"] == 5.5
    assert report["partial_by_expert"] == 1


def test_two_findings_on_one_clause_take_the_better_verdict():
    """A clause is caught if any finding caught it, not if the last one did."""
    score = FakeRiskScore(gold_spans=10, covered_spans=6)
    good = score_adjudication(GOOD_REPLY, blank(quote="a", ground_truth="the annotated clause"))
    bad = score_adjudication({**GOOD_REPLY, "decision": "overturned"}, blank(quote="b", ground_truth="the annotated clause"))

    report = end_to_end_miss_rate(score, [verdict(total_score=1.0, passed=True)], [], [bad, good])

    assert report["overturned_by_expert"] == 0
    assert report["caught"] == 6


def test_a_failed_adjudication_leaves_the_judges_verdict_standing():
    score = FakeRiskScore(gold_spans=10, covered_spans=6)
    failed = blank(ground_truth="the annotated clause", error="RuntimeError: nope")

    report = end_to_end_miss_rate(score, [verdict(total_score=0.0)], [], [failed])

    assert report["caught"] == 5
    assert report["rescued_by_expert"] == 0


def test_expert_versus_human_only_counts_what_both_settled():
    reviews = [
        {"expert_decision": "upheld", "risk_identified": "yes"},
        {"expert_decision": "overturned", "risk_identified": "no"},
        {"expert_decision": "upheld", "risk_identified": "no"},
        {"expert_decision": "partial", "risk_identified": "yes"},
        {"expert_decision": "", "risk_identified": "yes"},
        {"expert_decision": "upheld", "risk_identified": "partially"},
    ]

    result = expert_vs_human(reviews)

    assert result["compared"] == 3
    assert result["agreed"] == 2
    assert result["expert_upheld_human_failed"] == 1
    assert result["not_comparable"] == 3


class Arguments:
    """The two flags check_distinct_models reads."""

    def __init__(self, no_judge=False, no_adjudicator=False):
        self.no_judge = no_judge
        self.no_adjudicator = no_adjudicator


class Models:
    """Stands in for both Settings and EvalSettings in the distinctness check."""

    def __init__(self, reviewer="a/one", judge="b/two", adjudicator="c/three", provider="openrouter"):
        self.llm_provider = provider
        self.openrouter_model = reviewer
        self.ollama_model = reviewer
        self.judge_model = judge
        self.adjudicator_model = adjudicator


def test_three_different_models_are_allowed():
    check_distinct_models(Models(), Models(), Arguments())


@pytest.mark.parametrize(
    "models",
    [
        Models(reviewer="same/model", judge="same/model"),
        Models(reviewer="same/model", adjudicator="same/model"),
        Models(judge="same/model", adjudicator="same/model"),
    ],
    ids=["reviewer is the judge", "reviewer is the expert", "the judge is the expert"],
)
def test_two_levels_sharing_a_model_is_refused(models):
    """A model cannot grade or overturn its own output; the run stops rather than reporting it."""
    with pytest.raises(SystemExit, match="same/model"):
        check_distinct_models(models, models, Arguments())


def test_skipping_a_layer_removes_it_from_the_check():
    """--no-adjudicator means the adjudicator's model never runs, so a clash cannot matter."""
    models = Models(judge="same/model", adjudicator="same/model")

    check_distinct_models(models, models, Arguments(no_adjudicator=True))
