"""Which model is actually doing the reviewing.

`settings.openrouter_model` is the answer only when LLM_PROVIDER says so. Read
unconditionally it puts the wrong name on a scorecard — and a scorecard that
names a model other than the one that produced the numbers is worse than one
with no name on it, because it will be believed. That happened: a local run was
labelled `deepseek/deepseek-v4.1-flash` while gemma4 produced every token.

One table rather than a chain of ifs, so a provider added to the enum and not
here fails loudly at the KeyError instead of quietly reporting OpenRouter's.
"""

from enums.LLMProvider import LLMProvider
from utils.config import Settings

#: provider -> (the settings field holding its model id, the variable that set it)
REVIEWER_FIELDS = {
    LLMProvider.OPENROUTER: ("openrouter_model", "OPENROUTER_MODEL"),
    LLMProvider.OLLAMA: ("ollama_model", "OLLAMA_MODEL"),
    LLMProvider.NVIDIA: ("nvidia_model", "NVIDIA_MODEL"),
}


def reviewer_model(settings: Settings) -> str:
    """The model id the review pipeline will use, whichever provider is configured."""
    field, _ = REVIEWER_FIELDS[LLMProvider.parse(settings.llm_provider)]

    return getattr(settings, field)


def reviewer_setting(settings: Settings) -> str:
    """The environment variable that chose it, for an error message worth reading."""
    _, name = REVIEWER_FIELDS[LLMProvider.parse(settings.llm_provider)]

    return name
