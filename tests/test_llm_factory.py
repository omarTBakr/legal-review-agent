import pytest

import interfaces.llm.factory
from enums.LLMProvider import LLMProvider
from exceptions.llm import LLMConfigurationError
from interfaces import LLMInterface, OpenRouterLLM, get_llm, reset_llm_cache, set_llm


@pytest.fixture(autouse=True)
def clear_cache(monkeypatch):
    monkeypatch.setattr(interfaces.llm.factory, "_instances", {})


def test_the_default_provider_comes_from_settings(settings):
    assert isinstance(get_llm(), OpenRouterLLM)


def test_the_result_is_an_llm_interface(settings):
    assert isinstance(get_llm(), LLMInterface)


def test_instances_are_cached_per_provider(settings):
    assert get_llm() is get_llm()


def test_a_provider_can_be_named_explicitly(settings):
    assert get_llm(LLMProvider.OPENROUTER) is get_llm("openrouter")


def test_an_unknown_provider_is_a_configuration_error(settings):
    with pytest.raises(LLMConfigurationError, match="unknown LLM provider"):
        get_llm("definitely-not-a-provider")


def test_a_missing_api_key_is_a_configuration_error(monkeypatch):
    import utils.config

    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setattr(utils.config, "_settings_instance", None)

    with pytest.raises(LLMConfigurationError, match="OPENROUTER_API_KEY"):
        get_llm()


def test_set_llm_installs_an_implementation(settings):
    class Stub(LLMInterface):
        async def complete(self, prompt, **variables):
            return "{}"

    stub = Stub()
    set_llm(LLMProvider.OPENROUTER, stub)

    assert get_llm() is stub


def test_reset_clears_the_cache(settings):
    first = get_llm()
    reset_llm_cache()

    assert get_llm() is not first


def test_activities_never_import_a_vendor_client():
    """The point of the factory: only interfaces/ knows about OpenRouter."""
    import pathlib

    for path in pathlib.Path("activities").glob("*.py"):
        source = path.read_text()
        assert "OpenRouter" not in source, path
        assert "openrouter" not in source, path
