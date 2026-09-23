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

    The judge must not be the model under review, so `judge_model` defaults to a
    stronger and more expensive one than OPENROUTER_MODEL. `run.py` refuses to
    judge when the two are equal.
    """

    judge_model: str = Field("anthropic/claude-sonnet-5", description="OpenRouter model that scores the rubric")
    judge_temperature: float = Field(0.0, description="Judge sampling temperature; a rubric wants determinism")
    # the rubric's reply is under 100 tokens, but a reasoning model spends its
    # budget thinking before it writes any of them — at 1000 every verdict came
    # back cut off at the limit, which OpenRouterLLM rejects outright
    judge_max_tokens: int = Field(4000, description="Token ceiling for a judge reply, reasoning included")
    extraction_model: str = Field("", description="Model for the CUAD extraction task; empty means OPENROUTER_MODEL")

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
