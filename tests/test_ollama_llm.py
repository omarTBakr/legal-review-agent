"""
The local-model client, with a MockTransport instead of a server.

Three behaviours here are not shared with the hosted client and are the reason
this file exists: Ollama truncates to its own small context unless told
otherwise, it returns a reasoning model's thinking in a separate field that can
swallow the whole reply, and it streams newline-delimited JSON rather than
server-sent events.
"""

import json

import httpx
import pytest

from enums.PromptName import PromptName
from exceptions.llm import LLMError, LLMResponseError, LLMTimeoutError
from interfaces.llm.ollama import OllamaLLM
from prompts.prompt import Prompt
from utils.config import get_setting

PROMPT = Prompt(name=PromptName.LEGAL_ADVICE, system="You are careful.", user_template="Review {pdf_key}.")


def settings_for(**overrides):
    return get_setting().model_copy(update={"llm_provider": "ollama", "ollama_model": "gemma4:e4b", **overrides})


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://localhost:11434")


def reply(content="{}", **extra):
    return {"model": "gemma4:e4b", "message": {"role": "assistant", "content": content}, "done": True, **extra}


async def test_it_sends_the_context_window_it_was_configured_with():
    """
    The whole reason for the native API. On Ollama's default a 30-page batch is
    silently truncated and the model reports no risks in the half it never saw.
    """
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=reply('{"ok": true}'))

    llm = OllamaLLM(settings_for(ollama_context_tokens=65536), client=client_for(handler))
    await llm.complete(PROMPT, pdf_key="a.pdf")

    assert seen["options"]["num_ctx"] == 65536
    assert seen["model"] == "gemma4:e4b"
    assert seen["stream"] is False


async def test_thinking_is_off_by_default():
    """Measured on this machine: 26.1s with thinking, 0.5s without, same answer."""
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=reply())

    await OllamaLLM(settings_for(), client=client_for(handler)).complete(PROMPT, pdf_key="a.pdf")

    assert seen["think"] is False


async def test_thinking_can_be_turned_back_on():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=reply())

    await OllamaLLM(settings_for(ollama_think=True), client=client_for(handler)).complete(PROMPT, pdf_key="a.pdf")

    assert seen["think"] is True


async def test_a_json_prompt_asks_ollama_to_constrain_the_output():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=reply())

    json_prompt = Prompt(name=PromptName.LEGAL_ADVICE, system="s", user_template="u", expects_json=True)
    await OllamaLLM(settings_for(), client=client_for(handler)).complete(json_prompt)

    assert seen["format"] == "json"


async def test_the_message_content_comes_back():
    llm = OllamaLLM(settings_for(), client=client_for(lambda _: httpx.Response(200, json=reply('{"summary": "ok"}'))))

    assert await llm.complete(PROMPT, pdf_key="a.pdf") == '{"summary": "ok"}'


async def test_an_empty_reply_after_thinking_says_what_to_do():
    """
    The failure that actually happened: the reasoning consumed the whole reply
    budget and the content came back empty. The error has to name the cause.
    """
    body = {
        "model": "gemma4:e4b",
        "message": {"role": "assistant", "content": "", "thinking": "Let me consider the clause..." * 20},
        "done": True,
        "done_reason": "stop",
    }
    llm = OllamaLLM(settings_for(), client=client_for(lambda _: httpx.Response(200, json=body)))

    with pytest.raises(LLMResponseError, match="OLLAMA_THINK"):
        await llm.complete(PROMPT, pdf_key="a.pdf")


async def test_a_cut_off_reply_is_refused_rather_than_repaired():
    """A truncated reply is mostly valid JSON, and repairing it drops the risks
    the model had not written yet."""
    body = reply('{"key_risks": [{"desc', done_reason="length")
    llm = OllamaLLM(settings_for(), client=client_for(lambda _: httpx.Response(200, json=body)))

    with pytest.raises(LLMResponseError, match="cut off"):
        await llm.complete(PROMPT, pdf_key="a.pdf")


async def test_an_empty_reply_with_no_thinking_still_fails():
    llm = OllamaLLM(settings_for(), client=client_for(lambda _: httpx.Response(200, json=reply(""))))

    with pytest.raises(LLMResponseError, match="empty"):
        await llm.complete(PROMPT, pdf_key="a.pdf")


async def test_a_missing_model_says_how_to_get_it():
    def handler(request):
        return httpx.Response(404, text="model 'gemma4:e4b' not found")

    llm = OllamaLLM(settings_for(), client=client_for(handler))

    with pytest.raises(LLMError, match="ollama pull"):
        await llm.complete(PROMPT, pdf_key="a.pdf")


async def test_a_server_error_is_reported():
    llm = OllamaLLM(settings_for(), client=client_for(lambda _: httpx.Response(500, text="boom")))

    with pytest.raises(LLMError, match="500"):
        await llm.complete(PROMPT, pdf_key="a.pdf")


async def test_an_unreachable_server_names_the_address():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    llm = OllamaLLM(settings_for(), client=client_for(handler))

    with pytest.raises(LLMError, match="localhost:11434"):
        await llm.complete(PROMPT, pdf_key="a.pdf")


async def test_a_timeout_is_its_own_error():
    def handler(request):
        raise httpx.ReadTimeout("too slow")

    llm = OllamaLLM(settings_for(), client=client_for(handler))

    with pytest.raises(LLMTimeoutError):
        await llm.complete(PROMPT, pdf_key="a.pdf")


async def test_a_reply_that_is_not_json_is_reported():
    llm = OllamaLLM(settings_for(), client=client_for(lambda _: httpx.Response(200, text="<html>nope</html>")))

    with pytest.raises(LLMResponseError, match="not JSON"):
        await llm.complete(PROMPT, pdf_key="a.pdf")


async def test_an_error_field_in_a_200_is_still_an_error():
    llm = OllamaLLM(settings_for(), client=client_for(lambda _: httpx.Response(200, json={"error": "out of memory"})))

    with pytest.raises(LLMError, match="out of memory"):
        await llm.complete(PROMPT, pdf_key="a.pdf")


# --- streaming ------------------------------------------------------------


def ndjson(*objects) -> str:
    return "\n".join(json.dumps(o) for o in objects)


async def test_streaming_yields_each_chunk():
    """Newline-delimited JSON, not server-sent events."""
    stream = ndjson(
        {"message": {"content": "The "}, "done": False},
        {"message": {"content": "cap "}, "done": False},
        {"message": {"content": "is low."}, "done": False},
        {"message": {"content": ""}, "done": True, "done_reason": "stop"},
    )
    llm = OllamaLLM(settings_for(), client=client_for(lambda _: httpx.Response(200, text=stream)))

    assert "".join([chunk async for chunk in llm.stream(PROMPT, pdf_key="a.pdf")]) == "The cap is low."


async def test_streaming_does_not_yield_the_models_thinking():
    """Nobody asked to watch it reason."""
    stream = ndjson(
        {"message": {"content": "", "thinking": "Hmm, clause 6.1..."}, "done": False},
        {"message": {"content": "Unlimited."}, "done": False},
        {"message": {"content": ""}, "done": True},
    )
    llm = OllamaLLM(settings_for(), client=client_for(lambda _: httpx.Response(200, text=stream)))

    assert "".join([chunk async for chunk in llm.stream(PROMPT, pdf_key="a.pdf")]) == "Unlimited."


async def test_a_malformed_line_does_not_kill_the_answer():
    stream = (
        ndjson({"message": {"content": "Good"}, "done": False})
        + "\n{ not json\n"
        + ndjson({"message": {"content": " enough."}, "done": True})
    )
    llm = OllamaLLM(settings_for(), client=client_for(lambda _: httpx.Response(200, text=stream)))

    assert "".join([chunk async for chunk in llm.stream(PROMPT, pdf_key="a.pdf")]) == "Good enough."


async def test_streaming_reports_a_failure_before_the_first_chunk():
    llm = OllamaLLM(settings_for(), client=client_for(lambda _: httpx.Response(404, text="no such model")))

    with pytest.raises(LLMError, match="ollama pull"):
        [chunk async for chunk in llm.stream(PROMPT, pdf_key="a.pdf")]


async def test_streaming_asks_for_a_stream():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, text=ndjson({"message": {"content": "hi"}, "done": True}))

    llm = OllamaLLM(settings_for(), client=client_for(handler))
    [chunk async for chunk in llm.stream(PROMPT, pdf_key="a.pdf")]

    assert seen["stream"] is True


async def test_no_api_key_is_required():
    """The point of a local model: nothing to configure but the address."""
    local = get_setting().model_copy(update={"llm_provider": "ollama", "openrouter_api_key": ""})

    # the hosted client refuses to build without a key; this one must not
    OllamaLLM(local, client=client_for(lambda _: httpx.Response(200, json=reply())))
