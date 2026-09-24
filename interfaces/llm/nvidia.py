"""
NVIDIA's hosted models, over the shared OpenAI-compatible client.

`integrate.api.nvidia.com/v1` speaks the same protocol as OpenRouter, so this is
settings and an error message. There is no 402: the account is not billed per
call the way OpenRouter is, and a refusal is a 401 for a bad key or a 429 for
too many of them, both of which the base already reports.
"""

import httpx

from exceptions.llm import LLMConfigurationError
from interfaces.llm.openai_compatible import OpenAICompatibleLLM
from utils.config import Settings


class NvidiaLLM(OpenAICompatibleLLM):
    """LLMInterface over NVIDIA's chat-completions API."""

    vendor = "NVIDIA"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        if not settings.nvidia_api_key:
            raise LLMConfigurationError("NVIDIA_API_KEY is not set")

        super().__init__(settings, client)

    @property
    def model(self) -> str:
        return self._settings.nvidia_model

    @property
    def _base_url(self) -> str:
        return self._settings.nvidia_base_url

    @property
    def _api_key(self) -> str:
        return self._settings.nvidia_api_key
