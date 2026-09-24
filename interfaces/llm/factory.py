from enums.LLMProvider import LLMProvider
from exceptions.llm import LLMConfigurationError
from interfaces.llm.interface import LLMInterface
from interfaces.llm.ollama import OllamaLLM
from interfaces.llm.openrouter import OpenRouterLLM
from utils.config import Settings, get_setting

_instances: dict[LLMProvider, LLMInterface] = {}


def build_llm(provider: LLMProvider, settings: Settings) -> LLMInterface:
    """Constructs a fresh implementation for a provider."""
    if provider is LLMProvider.OPENROUTER:
        return OpenRouterLLM(settings)

    if provider is LLMProvider.OLLAMA:
        return OllamaLLM(settings)

    # a provider added to the enum and not here should fail loudly rather than
    # silently returning the wrong client
    raise LLMConfigurationError(f"no implementation registered for provider {provider.value}")


def get_llm(provider: LLMProvider | str | None = None) -> LLMInterface:
    """
    Returns the LLM to use, cached per provider.

    With no argument it reads LLM_PROVIDER, so activities ask for "the model"
    and never name a vendor.
    """
    settings = get_setting()

    if provider is None:
        provider = settings.llm_provider
    if isinstance(provider, str):
        try:
            provider = LLMProvider.parse(provider)
        except ValueError as exc:
            raise LLMConfigurationError(str(exc)) from exc

    if provider not in _instances:
        _instances[provider] = build_llm(provider, settings)

    return _instances[provider]


def set_llm(provider: LLMProvider, instance: LLMInterface) -> None:
    """Installs an implementation, for tests and for local fakes."""
    _instances[provider] = instance


def reset_llm_cache() -> None:
    _instances.clear()
