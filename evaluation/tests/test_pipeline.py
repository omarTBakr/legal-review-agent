"""The review path the suite drives, with a fake model in place of a real one.

These are the tests that keep the evaluation honest about running the *real*
pipeline: if analyze_batch stops calling verify_risks, or the merge stops
carrying verification through, the assertions here fail rather than the numbers
quietly improving.
"""

import json

import pytest

import evaluation.config
from enums.PromptName import PromptName
from evaluation.common.meter import TokenMeter
from evaluation.common.pipeline import paginate, result_from_dict, review_pages, review_text, risks_from_dicts
from prompts.prompt import Prompt
from tests.conftest import FakeLLM

PAGE_ONE = "1.1 The Supplier shall provide the services described in each Order Form."
PAGE_TWO = "6.1 The Client's aggregate liability under this Agreement is unlimited."


def advice(quote: str, severity: str = "critical", page: int = 2) -> dict:
    return {
        "summary": "A one-sided services agreement.",
        "key_risks": [
            {"description": "Unlimited client liability", "severity": severity, "location": "6.1", "quote": quote, "page": page}
        ],
        "needs_human": False,
        "question": "",
    }


@pytest.fixture
def llm():
    """A FakeLLM, reusing the main suite's so the two cannot drift apart."""
    return FakeLLM()


def test_pagination_breaks_on_paragraphs_not_mid_clause():
    pages = paginate("a" * 40 + "\n\n" + "b" * 40 + "\n\n" + "c" * 40, page_characters=50)

    assert pages == ["a" * 40, "b" * 40, "c" * 40]


def test_pagination_keeps_a_paragraph_longer_than_the_budget_whole():
    """Splitting a clause would make the review find it twice from half the evidence."""
    long_clause = "x" * 200

    assert paginate(long_clause, page_characters=50) == [long_clause]


def test_pagination_of_empty_text_is_one_empty_page():
    assert paginate("", page_characters=50) == [""]


async def test_a_single_batch_review_skips_the_merge_call(llm):
    llm.script(PromptName.LEGAL_ADVICE, advice(PAGE_TWO))

    result = await review_pages(llm, "doc.pdf", [PAGE_ONE, PAGE_TWO], pages_per_batch=30)

    assert llm.calls_for(PromptName.MERGE_ADVICE) == []
    assert len(result.advice.key_risks) == 1


async def test_several_batches_go_through_the_merge_prompt(llm):
    llm.script(PromptName.LEGAL_ADVICE, advice(PAGE_TWO))
    llm.script(PromptName.MERGE_ADVICE, advice(PAGE_TWO))

    result = await review_pages(llm, "doc.pdf", [PAGE_ONE, PAGE_TWO], pages_per_batch=1)

    assert len(llm.calls_for(PromptName.LEGAL_ADVICE)) == 2
    assert len(llm.calls_for(PromptName.MERGE_ADVICE)) == 1
    assert result.advice.summary


async def test_the_batches_carry_the_page_markers_the_evidence_check_needs(llm):
    llm.script(PromptName.LEGAL_ADVICE, advice(PAGE_TWO))

    await review_pages(llm, "doc.pdf", [PAGE_ONE, PAGE_TWO], pages_per_batch=30)

    assert "<!-- page 2 -->" in llm.calls_for(PromptName.LEGAL_ADVICE)[0]["variables"]["markdown"]


async def test_a_quote_that_is_in_the_document_is_verified_and_takes_its_real_page(llm):
    """The evidence check runs, and the page comes from the document, not the model."""
    llm.script(PromptName.LEGAL_ADVICE, advice(PAGE_TWO, page=99))

    result = await review_pages(llm, "doc.pdf", [PAGE_ONE, PAGE_TWO], pages_per_batch=30)

    assert result.advice.key_risks[0].quote_verified is True
    assert result.advice.key_risks[0].page == 2


async def test_a_paraphrased_quote_is_kept_but_unverified(llm):
    llm.script(PromptName.LEGAL_ADVICE, advice("The client has a lot of liability, broadly speaking."))

    result = await review_pages(llm, "doc.pdf", [PAGE_ONE, PAGE_TWO], pages_per_batch=30)

    assert result.advice.key_risks[0].quote_verified is False
    assert len(result.advice.key_risks) == 1


async def test_the_merge_cannot_invent_a_verified_quote(llm):
    """carry_verification runs: the merge never sees the pages, so a changed quote loses its flag."""
    llm.script(PromptName.LEGAL_ADVICE, advice(PAGE_TWO))
    llm.script(PromptName.MERGE_ADVICE, advice("Something the document never said at all."))

    result = await review_pages(llm, "doc.pdf", [PAGE_ONE, PAGE_TWO], pages_per_batch=1)

    assert result.advice.key_risks[0].quote_verified is False


async def test_the_raw_replies_are_kept_so_scoring_can_be_repeated_for_free(llm):
    llm.script(PromptName.LEGAL_ADVICE, advice(PAGE_TWO))

    result = await review_pages(llm, "doc.pdf", [PAGE_ONE, PAGE_TWO], pages_per_batch=1)

    assert len(result.raw_replies) == 3  # two batches and the merge


async def test_a_failed_batch_records_the_error_instead_of_raising(llm):
    """A crash must show up as a miss in the results file, not kill the run."""
    llm.error = RuntimeError("the model fell over")

    result = await review_text(llm, "doc.pdf", PAGE_ONE, pages_per_batch=30, page_characters=2500)

    assert "RuntimeError" in result.error
    assert result.advice.key_risks == []


async def test_an_unusable_reply_fails_the_document_rather_than_being_papered_over(llm):
    llm.script(PromptName.LEGAL_ADVICE, {"key_risks": []})

    result = await review_pages(llm, "doc.pdf", [PAGE_ONE], pages_per_batch=30)

    assert "summary" in result.error


async def test_a_result_survives_a_round_trip_through_the_results_file(llm):
    llm.script(PromptName.LEGAL_ADVICE, advice(PAGE_TWO))
    original = await review_pages(llm, "doc.pdf", [PAGE_ONE, PAGE_TWO], pages_per_batch=30)

    restored = result_from_dict(json.loads(json.dumps(original.to_dict())))

    assert restored.advice.key_risks[0].quote == PAGE_TWO
    assert restored.advice.key_risks[0].quote_verified is True
    assert restored.advice.key_risks[0].page == 2


def test_verification_is_restored_from_the_record_not_recomputed():
    """Re-deriving it without the batch it came from would be checking a different thing."""
    risks = risks_from_dicts([{"description": "d", "severity": "high", "quote": "q", "quote_verified": True}])

    assert risks[0].quote_verified is True


def test_the_meter_totals_what_the_provider_reported():
    meter = TokenMeter()
    meter.record("test/reviewer", 1000, 200)
    meter.record("test/reviewer", 500, 100)
    meter.prices["test/reviewer"] = (0.000001, 0.00001)

    report = meter.report()

    assert report["calls"] == 2
    assert report["prompt_tokens"] == 1500
    assert report["completion_tokens"] == 300
    assert report["estimated_cost_usd"] == pytest.approx(0.0045)


def test_an_unpriced_model_is_named_rather_than_costed_at_zero():
    meter = TokenMeter()
    meter.record("mystery/model", 1000, 100)

    report = meter.report()

    assert report["unpriced_models"] == ["mystery/model"]
    assert "unpriced" in meter.summary_line()


def test_the_metered_llm_points_at_the_model_it_was_asked_for(settings):
    """The judge override must not be able to change which model the reviewer gets."""
    meter = TokenMeter()

    judge = meter.llm(settings, model="test/judge", temperature=0.0)
    reviewer = meter.llm(settings)

    assert judge.model == "test/judge"
    assert reviewer.model == "test/reviewer"
    assert settings.openrouter_model == "test/reviewer"


def test_a_local_run_is_metered_and_named_correctly(settings, monkeypatch):
    """
    A scorecard that names a model other than the one that produced the numbers
    is worse than one with no name on it, because it will be believed.
    """
    import evaluation.config
    import utils.config
    from evaluation.common.models import reviewer_model, reviewer_setting
    from interfaces.ollama_llm import OllamaLLM

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setattr(utils.config, "_settings_instance", None)
    monkeypatch.setattr(evaluation.config, "_instance", None)
    local = utils.config.get_setting()

    assert reviewer_model(local) == "test/local"
    assert reviewer_setting(local) == "OLLAMA_MODEL"

    built = TokenMeter().llm(local)
    assert isinstance(built, OllamaLLM)
    assert built.model == "test/local"


def test_a_local_judge_override_names_the_local_model(settings, monkeypatch):
    """The override has to land on the field the active provider actually reads."""
    import evaluation.config
    import utils.config

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setattr(utils.config, "_settings_instance", None)
    monkeypatch.setattr(evaluation.config, "_instance", None)
    local = utils.config.get_setting()

    assert TokenMeter().llm(local, model="other/model").model == "other/model"
    # and the reviewer is untouched by it
    assert local.ollama_model == "test/local"


def test_the_evaluation_prompts_are_not_in_the_products_registry():
    """An evaluation prompt in prompts/ is one the service could send to a client."""
    from prompts import get_prompt

    for name in PromptName:
        prompt = get_prompt(name)
        assert isinstance(prompt, Prompt)
        assert "legal dataset" not in prompt.system
        assert "Score each dimension" not in prompt.system


# --- mixing providers across levels ---------------------------------------


class Flags:
    """The arguments the caveat and the guard read."""

    def __init__(self, no_judge=False, no_adjudicator=False):
        self.no_judge = no_judge
        self.no_adjudicator = no_adjudicator


def test_a_hosted_reviewer_can_be_graded_by_a_local_judge(settings):
    """
    The point of a per-level provider: the system under test stays where the
    product runs, and the instrument measuring it costs nothing.
    """
    from interfaces.ollama_llm import OllamaLLM
    from interfaces.openrouter_llm import OpenRouterLLM

    meter = TokenMeter()

    reviewer = meter.llm(settings)
    judge = meter.llm(settings, model="gemma4:e4b", provider="ollama")

    assert isinstance(reviewer, OpenRouterLLM)
    assert reviewer.model == "test/reviewer"
    assert isinstance(judge, OllamaLLM)
    assert judge.model == "gemma4:e4b"


def test_asking_for_a_local_judge_does_not_move_the_reviewer(settings):
    """model_copy, not the environment, so one level cannot reach another."""
    meter = TokenMeter()

    meter.llm(settings, model="gemma4:e4b", provider="ollama")

    assert settings.llm_provider == "openrouter"
    assert settings.openrouter_model == "test/reviewer"


def test_a_local_judge_over_a_hosted_reviewer_is_flagged(settings):
    """
    The instrument is weaker than the thing it measures, and the scorecard has
    to say so where the numbers are read.
    """
    from evaluation.run import grading_caveat

    eval_settings = evaluation.config.get_eval_settings().model_copy(
        update={"judge_provider": "ollama", "judge_model": "gemma4:e4b"}
    )

    caveat = grading_caveat(settings, eval_settings, Flags())

    assert "regression signal" in caveat
    assert "gemma4:e4b" in caveat


def test_a_hosted_judge_over_a_hosted_reviewer_is_not_flagged(settings):
    from evaluation.run import grading_caveat

    eval_settings = evaluation.config.get_eval_settings()

    assert grading_caveat(settings, eval_settings, Flags()) == ""


def test_an_all_local_run_is_not_flagged(settings, monkeypatch):
    """Both sides local is a fair fight; the caveat is about the mismatch."""
    import utils.config
    from evaluation.run import grading_caveat

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setattr(utils.config, "_settings_instance", None)
    local = utils.config.get_setting()

    eval_settings = evaluation.config.get_eval_settings().model_copy(update={"judge_provider": "ollama"})

    assert grading_caveat(local, eval_settings, Flags()) == ""


def test_skipping_the_judge_skips_the_caveat(settings):
    from evaluation.run import grading_caveat

    eval_settings = evaluation.config.get_eval_settings().model_copy(update={"judge_provider": "ollama"})

    assert grading_caveat(settings, eval_settings, Flags(no_judge=True)) == ""
