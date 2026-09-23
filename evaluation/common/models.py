"""Which model is actually doing the reviewing.

`settings.openrouter_model` is the answer only when LLM_PROVIDER says so. Read
unconditionally it puts the wrong name on a scorecard — and a scorecard that
names a model other than the one that produced the numbers is worse than one
with no name on it, because it will be believed.
"""

from enums.LLMProvider import LLMProvider
from utils.config import Settings


def reviewer_model(settings: Settings) -> str:
    """The model id the review pipeline will use, whichever provider is configured."""
    if LLMProvider.parse(settings.llm_provider) is LLMProvider.OLLAMA:
        return settings.ollama_model

    return settings.openrouter_model


def reviewer_setting(settings: Settings) -> str:
    """The environment variable that chose it, for an error message worth reading."""
    if LLMProvider.parse(settings.llm_provider) is LLMProvider.OLLAMA:
        return "OLLAMA_MODEL"

    return "OPENROUTER_MODEL"
