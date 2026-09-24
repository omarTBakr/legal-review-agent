"""
One client for every OpenAI-compatible chat-completions API.

OpenRouter and NVIDIA's `integrate.api.nvidia.com` speak the same protocol —
`POST /chat/completions`, the same message shape, the same server-sent events —
so they are one implementation with different settings rather than two clients
that drift apart. A subclass supplies three things: where to send it, what to
send as the key, and which model. Anything genuinely vendor-specific goes in
`_raise_for`, which is the only place the two actually differ: OpenRouter has a
402 for credits and NVIDIA does not.

`vendor` is only ever used in error messages, so a failure says which service
refused rather than naming whichever class happened to be instantiated.
"""

import json
from abc import abstractmethod
from collections.abc import AsyncIterator

import httpx

from exceptions.llm import (
    LLMError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
)
from interfaces.llm.interface import LLMInterface
from prompts.prompt import Prompt
from utils.config import Settings
from utils.logger import get_logger

logger = get_logger(__name__)


class OpenAICompatibleLLM(LLMInterface):
    """LLMInterface over any OpenAI-shaped chat-completions endpoint."""

    #: the name that appears in error messages
    vendor = "the model provider"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self._settings = settings
        # injectable so tests can supply a MockTransport instead of the network
        self._client = client

    @property
    @abstractmethod
    def model(self) -> str:
        """The model id to ask for."""

    @property
    @abstractmethod
    def _base_url(self) -> str:
        """Where the chat-completions endpoint lives."""

    @property
    @abstractmethod
    def _api_key(self) -> str:
        """The bearer token to present."""

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._settings.llm_timeout_seconds,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        return self._client

    def _payload(self, prompt: Prompt, **variables) -> dict:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.render(**variables)},
            ],
            "max_tokens": self._settings.llm_max_tokens,
            "temperature": self._settings.llm_temperature,
        }

        if prompt.expects_json:
            # ask for JSON; the interface still validates, because not every
            # model honours this. A prompt whose reply is read aloud must not
            # come back wrapped in an object.
            payload["response_format"] = {"type": "json_object"}

        return payload

    async def complete(self, prompt: Prompt, **variables) -> str:
        payload = self._payload(prompt, **variables)

        logger.info("asking %s for %s", self.model, prompt.name.value)

        try:
            response = await self._http().post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"{self.model} did not answer within {self._settings.llm_timeout_seconds}s") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"could not reach {self.vendor}: {exc}") from exc

        # OpenRouter reserves credit for every request in flight, so a small
        # balance fails with 402 when many documents run at once
        self._raise_for(response)

        return self._content_of(response)

    def _content_of(self, response: httpx.Response) -> str:
        """Digs the message text out of the envelope, or says why it could not."""
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMResponseError(f"{self.vendor} reply was not JSON: {response.text[:200]}") from exc

        if body.get("error"):
            raise LLMError(f"{self.vendor} reported an error: {body['error']}")

        try:
            choice = body["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError(f"unexpected {self.vendor} response shape: {str(body)[:200]}") from exc

        if choice.get("finish_reason") == "length":
            # a cut-off reply is still mostly valid JSON, and repairing it would
            # quietly drop every risk the model had not written yet
            raise LLMResponseError("the reply was cut off at LLM_MAX_TOKENS: raise it, or lower LEGAL_PAGES_PER_BATCH")

        if not content:
            raise LLMResponseError(f"{self.vendor} returned an empty message")

        return content

    async def stream(self, prompt: Prompt, **variables) -> AsyncIterator[str]:
        """
        The reply as the model writes it.

        The wait for a chat answer is mostly the model writing it, so the first
        words arriving early is the difference between an answer that feels
        immediate and one that feels broken. These providers send server-sent
        events; each carries a delta, and `[DONE]` ends it.
        """
        payload = self._payload(prompt, **variables) | {"stream": True}

        logger.info("streaming %s for %s", self.model, prompt.name.value)

        try:
            async with self._http().stream("POST", "/chat/completions", json=payload) as response:
                if response.status_code >= 400:
                    # the body has to be read before it can be quoted
                    await response.aread()
                    self._raise_for(response)

                async for line in response.aiter_lines():
                    chunk = self._delta_of(line)
                    if chunk:
                        yield chunk
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"{self.model} did not answer within {self._settings.llm_timeout_seconds}s") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"could not reach {self.vendor}: {exc}") from exc

    @staticmethod
    def _delta_of(line: str) -> str:
        """
        The text in one server-sent event, or "" for everything else.

        Keep-alive comments, the `[DONE]` sentinel and events carrying only a
        finish reason all arrive here and are not content.
        """
        line = line.strip()

        if not line.startswith("data:"):
            return ""

        data = line[len("data:") :].strip()
        if not data or data == "[DONE]":
            return ""

        try:
            choice = json.loads(data)["choices"][0]
        except (ValueError, KeyError, IndexError, TypeError):
            # a malformed event is not worth failing a whole answer over
            return ""

        return str(choice.get("delta", {}).get("content") or "")

    def _raise_for(self, response: httpx.Response) -> None:
        """
        The status-code failures every one of these endpoints can return.

        A subclass overrides this to add its own — and calls back here for the
        rest, so a new status handled in one place is handled everywhere.
        """
        if response.status_code == 429:
            raise LLMRateLimitError(f"{self.vendor} is throttling {self.model}")

        if response.status_code >= 400:
            raise LLMError(f"{self.vendor} returned {response.status_code}: {response.text[:200]}")

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
