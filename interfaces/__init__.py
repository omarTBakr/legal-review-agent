"""Boundaries between this project and the services it talks to.

Only `LLMInterface` for now. Depend on the interface and get a concrete object
from the factory; nothing outside this package should import a vendor client.

    from interfaces import get_llm

    advice = await get_llm().complete_json(prompt, pdf_key=...)
"""

from interfaces.llm.factory import build_llm, get_llm, reset_llm_cache, set_llm
from interfaces.llm.interface import LLMInterface
from interfaces.llm.openrouter import OpenRouterLLM

__all__ = ["LLMInterface", "OpenRouterLLM", "build_llm", "get_llm", "reset_llm_cache", "set_llm"]
