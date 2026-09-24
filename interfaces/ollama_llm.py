"""
LLMInterface over a local Ollama server.

A model on this machine costs nothing per call, which changes what is worth
doing: a full evaluation sweep stops being a budget decision. It is slower and
weaker than the hosted models, and the numbers it produces are about *this*
model — the point is that they can be produced at all, and repeatedly.

Ollama's native `/api/chat` rather than its OpenAI-compatible `/v1` endpoint,
for one reason that matters and one that follows from it:

**`num_ctx`.** Ollama does not use a model's full context by default; it uses
its own, much smaller, default and silently truncates anything longer. One of
this pipeline's batches is ~75,000 characters — about 21,000 tokens — so on the
default the model would read the first pages of a contract, never see the rest,
and confidently report no risks in the half it was never shown. There is no way
to set `num_ctx` through the `/v1` endpoint. That alone decides it.

**`think`.** Gemma 4 and its kind reason before answering, and Ollama returns
that reasoning in a separate `thinking` field. Left on, it is both slow and
dangerous here: measured on this machine, the same one-line answer took 26.1s
with thinking and 0.5s without, and on a long prompt the reasoning consumed the
whole `num_predict` budget and the reply came back **empty** with
`done_reason: length`. Off by default, `OLLAMA_THINK=true` to bring it back.
"""

import json
from collections.abc import AsyncIterator

import httpx

from exceptions.llm import LLMError, LLMResponseError, LLMTimeoutError
from interfaces.llm_interface import LLMInterface
from prompts.prompt import Prompt
from utils.config import Settings
from utils.logger import get_logger

logger = get_logger(__name__)

CHAT_PATH = "/api/chat"


class OllamaLLM(LLMInterface):
    """LLMInterface over Ollama's native chat API."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self._settings = settings
        # injectable so tests can supply a MockTransport instead of a server
        self._client = client

    @property
    def model(self) -> str:
        return self._settings.ollama_model

    @property
    def _timeout(self) -> float:
        return self._settings.ollama_timeout_seconds

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._settings.ollama_base_url,
                # its own timeout: a local model is slower than a hosted one,
                # not faster, and LLM_TIMEOUT_SECONDS is tuned for an API
                timeout=httpx.Timeout(self._timeout, connect=10.0),
            )
        return self._client

    def _payload(self, prompt: Prompt, **variables) -> dict:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.render(**variables)},
            ],
            "stream": False,
            "think": self._settings.ollama_think,
            "options": {
                "num_ctx": self._settings.ollama_context_tokens,
                "num_predict": self._settings.llm_max_tokens,
                "temperature": self._settings.llm_temperature,
            },
        }

        if prompt.expects_json:
            # Ollama constrains generation to valid JSON, which a small model
            # needs far more than a large one. The interface still validates:
            # valid JSON is not the same as the right shape.
            payload["format"] = "json"

        return payload

    async def complete(self, prompt: Prompt, **variables) -> str:
        payload = self._payload(prompt, **variables)

        logger.info("asking %s for %s", self.model, prompt.name.value)

        try:
            response = await self._http().post(CHAT_PATH, json=payload)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"{self.model} did not answer within {self._timeout}s") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"could not reach Ollama at {self._settings.ollama_base_url}: {exc}") from exc

        self._raise_for(response)

        return self._content_of(response)

    def _content_of(self, response: httpx.Response) -> str:
        """The message text, or why there is none."""
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMResponseError(f"Ollama reply was not JSON: {response.text[:200]}") from exc

        if body.get("error"):
            raise LLMError(f"Ollama reported an error: {body['error']}")

        message = body.get("message") or {}
        content = str(message.get("content") or "")

        if body.get("done_reason") == "length":
            # the same rule as the hosted client: a cut-off reply is mostly
            # valid JSON, and repairing it would quietly drop every risk the
            # model had not written yet
            raise LLMResponseError(f"{self.model} was cut off at LLM_MAX_TOKENS: raise it, or lower LEGAL_PAGES_PER_BATCH")

        if not content:
            # thinking left on, with the whole budget spent reasoning, lands here
            thought = len(str(message.get("thinking") or ""))
            hint = f" (it produced {thought} characters of thinking instead; set OLLAMA_THINK=false)" if thought else ""
            raise LLMResponseError(f"{self.model} returned an empty message{hint}")

        return content

    async def stream(self, prompt: Prompt, **variables) -> AsyncIterator[str]:
        """
        The reply as the model writes it.

        Ollama streams newline-delimited JSON objects rather than server-sent
        events: one object per chunk, each carrying the next piece of the
        message, the last one carrying `done`.
        """
        payload = self._payload(prompt, **variables) | {"stream": True}

        logger.info("streaming %s for %s", self.model, prompt.name.value)

        try:
            async with self._http().stream("POST", CHAT_PATH, json=payload) as response:
                if response.status_code >= 400:
                    # the body has to be read before it can be quoted
                    await response.aread()
                    self._raise_for(response)

                async for line in response.aiter_lines():
                    chunk = self._chunk_of(line)
                    if chunk:
                        yield chunk
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"{self.model} did not answer within {self._timeout}s") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"could not reach Ollama at {self._settings.ollama_base_url}: {exc}") from exc

    @staticmethod
    def _chunk_of(line: str) -> str:
        """The text in one streamed object, or "" for anything that carries none."""
        line = line.strip()
        if not line:
            return ""

        try:
            body = json.loads(line)
        except ValueError:
            # a malformed line is not worth failing a whole answer over
            return ""

        # `thinking` is deliberately not yielded: nobody wants to watch a model
        # reason at them, and the caller asked for the answer
        return str((body.get("message") or {}).get("content") or "")

    def _raise_for(self, response: httpx.Response) -> None:
        """The status-code failures shared by both paths."""
        if response.status_code == 404:
            raise LLMError(
                f"Ollama has no model called {self.model!r}: run `ollama pull {self.model}`, "
                "or set OLLAMA_MODEL to one of `ollama list`"
            )

        if response.status_code >= 400:
            raise LLMError(f"Ollama returned {response.status_code}: {response.text[:200]}")

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
