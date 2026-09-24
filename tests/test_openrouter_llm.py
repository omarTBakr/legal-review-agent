"""The OpenRouter client is tested against httpx's MockTransport, so the shape
of the request and the handling of each failure are covered without the network
or a bill."""

import json

import httpx
import pytest

from enums.PromptName import PromptName
from exceptions.llm import (
    LLMConfigurationError,
    LLMError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
)
from interfaces.llm.openrouter import OpenRouterLLM
from prompts import get_prompt

PROMPT_VARS = {
    "pdf_key": "contract.pdf",
    "batch_label": "pages 1-2",
    "batch_number": 1,
    "batch_count": 3,
    "markdown": "Clause 1. The supplier shall...",
}


def client_returning(handler, settings) -> OpenRouterLLM:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url=settings.openrouter_base_url)
    return OpenRouterLLM(settings, client=http)


def reply(content: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})


def events(*chunks: str, done: bool = True) -> bytes:
    """The server-sent events OpenRouter would send for those deltas."""
    lines = [f'data: {json.dumps({"choices": [{"delta": {"content": chunk}}]})}' for chunk in chunks]
    if done:
        lines.append("data: [DONE]")

    return ("\n\n".join(lines) + "\n\n").encode()


async def collect(llm, prompt_name=PromptName.REVIEW_CHAT, **variables):
    return [chunk async for chunk in llm.stream(get_prompt(prompt_name), **(variables or CHAT_VARS))]


CHAT_VARS = {"advice": "...", "pages": "...", "history": "", "question": "What is the cap?", "document_count": 1}


# --- streaming -----------------------------------------------------------


async def test_the_reply_arrives_in_pieces(settings):
    llm = client_returning(lambda request: httpx.Response(200, content=events("Liability ", "is ", "capped.")), settings)

    assert await collect(llm) == ["Liability ", "is ", "capped."]


async def test_streaming_asks_for_a_stream(settings):
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=events("ok"))

    await collect(client_returning(handler, settings))

    assert seen["stream"] is True


async def test_the_done_sentinel_and_keepalives_are_not_content(settings):
    """Everything on the wire is not text; only the deltas are."""
    body = b": keep-alive\n\n" + events("real text") + b'data: {"choices": [{"finish_reason": "stop"}]}\n\n'
    llm = client_returning(lambda request: httpx.Response(200, content=body), settings)

    assert await collect(llm) == ["real text"]


async def test_a_malformed_event_does_not_end_the_answer(settings):
    body = b"data: {not json}\n\n" + events("the rest arrived")
    llm = client_returning(lambda request: httpx.Response(200, content=body), settings)

    assert await collect(llm) == ["the rest arrived"]


async def test_a_refusal_while_streaming_is_reported(settings):
    llm = client_returning(lambda request: httpx.Response(429, text="slow down"), settings)

    with pytest.raises(LLMRateLimitError):
        await collect(llm)


async def test_an_unreachable_service_while_streaming_is_reported(settings):
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMError):
        await collect(client_returning(handler, settings))


async def test_a_provider_that_cannot_stream_still_satisfies_the_interface(settings, llm):
    """The default implementation yields the whole reply as one piece."""
    llm.script(PromptName.REVIEW_CHAT, "One piece.")

    assert [chunk async for chunk in llm.stream(get_prompt(PromptName.REVIEW_CHAT), **CHAT_VARS)] == ["One piece."]


async def test_a_prose_prompt_does_not_demand_a_json_object(settings):
    """The chat answer is read aloud; response_format would wrap it in an object."""
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return reply("The liability cap is twelve months of fees (p. 7).")

    llm = client_returning(handler, settings)

    await llm.complete(get_prompt(PromptName.REVIEW_CHAT), advice="...", pages="...", history="", question="?", document_count=1)

    assert "response_format" not in seen


async def test_a_json_prompt_still_asks_for_json(settings):
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return reply('{"summary": "ok"}')

    llm = client_returning(handler, settings)

    await llm.complete(get_prompt(PromptName.LEGAL_ADVICE), **PROMPT_VARS)

    assert seen["response_format"] == {"type": "json_object"}


async def test_a_successful_call_returns_the_content(settings):
    llm = client_returning(lambda request: reply('{"summary": "ok"}'), settings)

    assert await llm.complete(get_prompt(PromptName.LEGAL_ADVICE), **PROMPT_VARS) == '{"summary": "ok"}'


async def test_the_request_carries_the_model_and_both_messages(settings):
    seen = {}

    def handler(request):
        seen.update(request.read() and __import__("json").loads(request.read()))
        return reply('{"summary": "ok"}')

    llm = client_returning(handler, settings)
    await llm.complete(get_prompt(PromptName.LEGAL_ADVICE), **PROMPT_VARS)

    assert seen["model"] == settings.openrouter_model
    assert [m["role"] for m in seen["messages"]] == ["system", "user"]
    assert "contract.pdf" in seen["messages"][1]["content"]
    assert seen["temperature"] == settings.llm_temperature
    assert seen["max_tokens"] == settings.llm_max_tokens
    assert seen["response_format"] == {"type": "json_object"}


async def test_the_api_key_is_sent(settings):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return reply('{"summary": "ok"}')

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=settings.openrouter_base_url,
        headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
    )
    await OpenRouterLLM(settings, client=http).complete(get_prompt(PromptName.LEGAL_ADVICE), **PROMPT_VARS)

    assert seen["auth"] == "Bearer test-llm-key"


async def test_complete_json_parses_the_reply(settings):
    llm = client_returning(lambda request: reply('```json\n{"summary": "ok"}\n```'), settings)

    assert await llm.complete_json(get_prompt(PromptName.LEGAL_ADVICE), **PROMPT_VARS) == {"summary": "ok"}


async def test_a_429_is_a_rate_limit_error(settings):
    llm = client_returning(lambda request: httpx.Response(429, json={}), settings)

    with pytest.raises(LLMRateLimitError):
        await llm.complete(get_prompt(PromptName.LEGAL_ADVICE), **PROMPT_VARS)


async def test_a_402_says_the_account_is_out_of_credits(settings):
    """OpenRouter answers 402 both for an empty balance and for too many requests in flight on a small one."""
    llm = client_returning(lambda request: httpx.Response(402, json={"error": {"message": "insufficient credits"}}), settings)

    with pytest.raises(LLMError, match="lack of credits"):
        await llm.complete(get_prompt(PromptName.LEGAL_ADVICE), **PROMPT_VARS)


async def test_a_500_is_an_llm_error(settings):
    llm = client_returning(lambda request: httpx.Response(500, text="upstream exploded"), settings)

    with pytest.raises(LLMError, match="500"):
        await llm.complete(get_prompt(PromptName.LEGAL_ADVICE), **PROMPT_VARS)


async def test_a_timeout_is_a_timeout_error(settings):
    def handler(request):
        raise httpx.TimeoutException("too slow")

    llm = client_returning(handler, settings)

    with pytest.raises(LLMTimeoutError, match="did not answer"):
        await llm.complete(get_prompt(PromptName.LEGAL_ADVICE), **PROMPT_VARS)


async def test_a_transport_failure_is_an_llm_error(settings):
    def handler(request):
        raise httpx.ConnectError("no route to host")

    llm = client_returning(handler, settings)

    with pytest.raises(LLMError, match="could not reach OpenRouter"):
        await llm.complete(get_prompt(PromptName.LEGAL_ADVICE), **PROMPT_VARS)


async def test_an_error_body_is_surfaced(settings):
    llm = client_returning(lambda request: httpx.Response(200, json={"error": {"message": "no credits"}}), settings)

    with pytest.raises(LLMError, match="no credits"):
        await llm.complete(get_prompt(PromptName.LEGAL_ADVICE), **PROMPT_VARS)


async def test_an_unexpected_envelope_is_a_response_error(settings):
    llm = client_returning(lambda request: httpx.Response(200, json={"unexpected": True}), settings)

    with pytest.raises(LLMResponseError, match="unexpected OpenRouter response shape"):
        await llm.complete(get_prompt(PromptName.LEGAL_ADVICE), **PROMPT_VARS)


async def test_an_empty_message_is_a_response_error(settings):
    llm = client_returning(lambda request: reply(""), settings)

    with pytest.raises(LLMResponseError, match="empty message"):
        await llm.complete(get_prompt(PromptName.LEGAL_ADVICE), **PROMPT_VARS)


async def test_a_reply_cut_off_at_max_tokens_is_rejected_not_repaired(settings):
    """Repair would turn a truncated reply into advice with every unwritten risk missing."""
    truncated = '{"summary": "ok", "key_risks": [{"description": "Unlimited liab'
    body = {"choices": [{"message": {"content": truncated}, "finish_reason": "length"}]}
    llm = client_returning(lambda request: httpx.Response(200, json=body), settings)

    with pytest.raises(LLMResponseError, match="cut off at LLM_MAX_TOKENS"):
        await llm.complete_json(get_prompt(PromptName.LEGAL_ADVICE), **PROMPT_VARS)


async def test_a_reply_that_finished_normally_is_accepted(settings):
    body = {"choices": [{"message": {"content": '{"summary": "ok"}'}, "finish_reason": "stop"}]}
    llm = client_returning(lambda request: httpx.Response(200, json=body), settings)

    assert await llm.complete(get_prompt(PromptName.LEGAL_ADVICE), **PROMPT_VARS) == '{"summary": "ok"}'


async def test_a_non_json_body_is_a_response_error(settings):
    llm = client_returning(lambda request: httpx.Response(200, text="<html>gateway</html>"), settings)

    with pytest.raises(LLMResponseError, match="not JSON"):
        await llm.complete(get_prompt(PromptName.LEGAL_ADVICE), **PROMPT_VARS)


def test_construction_without_a_key_fails_fast(settings, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "")

    with pytest.raises(LLMConfigurationError):
        OpenRouterLLM(settings)
