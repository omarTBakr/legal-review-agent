"""Settings the evaluation needs and the service does not.

A separate BaseSettings rather than fields on utils.config.Settings: the
service should not have to know that an evaluation exists, and a missing
EVAL_JUDGE_MODEL must not be able to break a deployment. Everything is prefixed
EVAL_ and read from the same .env.
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).parent

# gitignored: downloaded corpus and per-run model output
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"


class EvalSettings(BaseSettings):
    """
    How the evaluation is wired, model ids included.

    One model per level, each stronger than the one below it, and no two the
    same. The reviewer (OPENROUTER_MODEL) is the system under test; the judge
    grades every matched finding; the adjudicator re-decides only the escalated
    ones, which is why it can afford to be the expensive model. `run.py` refuses
    to run when two levels share a model: a model grading its own output agrees
    with itself, and the agreement is not evidence.
    """

    judge_model: str = Field("anthropic/claude-sonnet-5", description="Model that scores the rubric")
    # each level picks its own provider. The reviewer is the system under test
    # and belongs wherever the product runs; the judge and the expert are
    # measuring instruments and can live anywhere — a hosted reviewer graded by
    # a model on this machine costs nothing to grade. Empty follows LLM_PROVIDER.
    judge_provider: str = Field("", description="openrouter or ollama; empty follows LLM_PROVIDER")
    judge_temperature: float = Field(0.0, description="Judge sampling temperature; a rubric wants determinism")
    # the rubric's reply is under 100 tokens, but a reasoning model spends its
    # budget thinking before it writes any of them — at 1000 every verdict came
    # back cut off at the limit, which OpenRouterLLM rejects outright
    judge_max_tokens: int = Field(4000, description="Token ceiling for a judge reply, reasoning included")
    extraction_model: str = Field("", description="Model for the CUAD extraction task; empty means OPENROUTER_MODEL")

    # layer 3: the expert. Only the escalation queue reaches it, so the biggest
    # model on the list costs least here — a few dozen calls against the judge's
    # few hundred and the reviewer's thousands.
    adjudicator_model: str = Field("anthropic/claude-opus-5", description="Model that re-decides escalations")
    adjudicator_provider: str = Field("", description="openrouter or ollama; empty follows LLM_PROVIDER")
    adjudicator_temperature: float = Field(0.0, description="Adjudicator sampling temperature")
    adjudicator_max_tokens: int = Field(6000, description="Token ceiling for an adjudication; it writes more than the judge")
    adjudicate_limit: int = Field(0, description="Most escalations to adjudicate, worst score first; 0 means all of them")

    sample_size: int = Field(25, description="Contracts drawn from CUAD when --limit is not given")
    sample_seed: int = Field(20260101, description="Seed for the stratified sample, so a run is repeatable")

    page_characters: int = Field(2500, description="Characters per synthetic page when paginating CUAD plain text")

    escalate_below: float = Field(0.8, description="Judge total_score under this escalates")
    escalate_above: float = Field(0.3, description="Judge total_score over this escalates; under it the miss is plain")

    request_concurrency: int = Field(4, description="Model calls in flight at once across the whole run")

    model_config = SettingsConfigDict(
        env_prefix="EVAL_",
        env_file=str(ROOT.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


_instance: EvalSettings | None = None


def get_eval_settings() -> EvalSettings:
    """The evaluation settings, read once."""
    global _instance
    if _instance is None:
        _instance = EvalSettings()
    return _instance
