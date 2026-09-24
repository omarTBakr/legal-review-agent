"""
OpenRouter, over the shared OpenAI-compatible client.

Everything about the protocol lives in `OpenAICompatibleLLM`. What is actually
OpenRouter's own is the 402: it bills per call and reserves credit for every
request in flight, so a small balance fails when many documents run at once,
and the fix is a number rather than a retry.
"""

import httpx

from exceptions.llm import LLMConfigurationError, LLMError
from interfaces.llm.openai_compatible import OpenAICompatibleLLM
from utils.config import Settings


class OpenRouterLLM(OpenAICompatibleLLM):
    """LLMInterface over OpenRouter's chat-completions API."""

    vendor = "OpenRouter"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        if not settings.openrouter_api_key:
            raise LLMConfigurationError("OPENROUTER_API_KEY is not set")

        super().__init__(settings, client)

    @property
    def model(self) -> str:
        return self._settings.openrouter_model

    @property
    def _base_url(self) -> str:
        return self._settings.openrouter_base_url

    @property
    def _api_key(self) -> str:
        return self._settings.openrouter_api_key

    def _raise_for(self, response: httpx.Response) -> None:
        # OpenRouter reserves credit for every request in flight, so a small
        # balance fails with 402 when many documents run at once
        if response.status_code == 402:
            raise LLMError(
                f"OpenRouter declined {self.model} for lack of credits (402): " "add credits, or lower LEGAL_MAX_CONCURRENT_PDFS"
            )

        super()._raise_for(response)
