"""
NVIDIA's hosted models, with a MockTransport instead of the network.

Most of the behaviour is `OpenAICompatibleLLM`'s and is covered by the
OpenRouter tests. What is worth pinning here is that this provider points at
NVIDIA's own settings — a client that quietly read `openrouter_*` would work in
every test and send a review to the wrong vendor — and that it does *not*
inherit OpenRouter's 402, which does not apply to it.
"""

import json

import httpx
import pytest

from enums.LLMProvider import LLMProvider
from enums.PromptName import PromptName
from exceptions.llm import LLMConfigurationError, LLMError, LLMRateLimitError
from interfaces.llm.factory import build_llm
from interfaces.llm.nvidia import NvidiaLLM
from prompts.prompt import Prompt
from utils.config import get_setting

PROMPT = Prompt(name=PromptName.LEGAL_ADVICE, system="You are careful.", user_template="Review {pdf_key}.")


def settings_for(**overrides):
    return get_setting().model_copy(
        update={
            "llm_provider": "nvidia",
            "nvidia_api_key": "nvapi-test",
            "nvidia_model": "z-ai/glm-5.3",
            **overrides,
        }
    )


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://integrate.api.nvidia.com/v1")


def reply(content='{"ok": true}'):
    return {"model": "z-ai/glm-5.3", "choices": [{"message": {"content": content}, "finish_reason": "stop"}]}


def test_it_refuses_to_build_without_a_key():
    with pytest.raises(LLMConfigurationError, match="NVIDIA_API_KEY"):
        NvidiaLLM(settings_for(nvidia_api_key=""))


def test_the_factory_returns_it_for_the_nvidia_provider():
    built = build_llm(LLMProvider.NVIDIA, settings_for())

    assert isinstance(built, NvidiaLLM)
    assert built.model == "z-ai/glm-5.3"


def test_it_reads_nvidias_settings_not_openrouters():
    """A client quietly reading openrouter_* would pass every other test."""
    llm = NvidiaLLM(settings_for(openrouter_model="wrong/model", openrouter_base_url="https://wrong.example"))

    assert llm.model == "z-ai/glm-5.3"
    assert llm._base_url == "https://integrate.api.nvidia.com/v1"
    assert llm._api_key == "nvapi-test"


async def test_it_asks_for_the_configured_model_at_the_right_path():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["path"] = request.url.path
        return httpx.Response(200, json=reply())

    llm = NvidiaLLM(settings_for(), client=client_for(handler))
    await llm.complete(PROMPT, pdf_key="a.pdf")

    assert seen["body"]["model"] == "z-ai/glm-5.3"
    assert seen["path"].endswith("/chat/completions")


async def test_the_client_it_builds_carries_the_key_and_the_address():
    """
    Checked on the client it builds rather than on a request, because an
    injected one — which every other test here uses — never goes through
    header construction at all.
    """
    llm = NvidiaLLM(settings_for())
    built = llm._http()

    try:
        assert built.headers["Authorization"] == "Bearer nvapi-test"
        assert str(built.base_url).startswith("https://integrate.api.nvidia.com")
    finally:
        await llm.aclose()


async def test_the_reply_comes_back():
    llm = NvidiaLLM(settings_for(), client=client_for(lambda _: httpx.Response(200, json=reply('{"summary": "ok"}'))))

    assert await llm.complete(PROMPT, pdf_key="a.pdf") == '{"summary": "ok"}'


async def test_throttling_is_reported_as_such():
    """The evaluation retries this one, so it must not arrive as a generic error."""
    llm = NvidiaLLM(settings_for(), client=client_for(lambda _: httpx.Response(429, text="slow down")))

    with pytest.raises(LLMRateLimitError, match="NVIDIA"):
        await llm.complete(PROMPT, pdf_key="a.pdf")


async def test_a_402_is_not_treated_as_a_credit_problem():
    """OpenRouter's credit message would be wrong advice here."""
    llm = NvidiaLLM(settings_for(), client=client_for(lambda _: httpx.Response(402, text="nope")))

    with pytest.raises(LLMError) as caught:
        await llm.complete(PROMPT, pdf_key="a.pdf")

    assert "credits" not in str(caught.value)
    assert "NVIDIA returned 402" in str(caught.value)


async def test_errors_name_nvidia_rather_than_the_class():
    llm = NvidiaLLM(settings_for(), client=client_for(lambda _: httpx.Response(500, text="boom")))

    with pytest.raises(LLMError, match="NVIDIA returned 500"):
        await llm.complete(PROMPT, pdf_key="a.pdf")


async def test_streaming_yields_the_deltas():
    stream = (
        'data: {"choices":[{"delta":{"content":"The "}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"cap is low."}}]}\n\n'
        "data: [DONE]\n\n"
    )
    llm = NvidiaLLM(settings_for(), client=client_for(lambda _: httpx.Response(200, text=stream)))

    assert "".join([chunk async for chunk in llm.stream(PROMPT, pdf_key="a.pdf")]) == "The cap is low."
