from enum import Enum


class LLMProvider(Enum):
    """Which LLMInterface implementation the factory should build."""

    OPENROUTER = "openrouter"
    OLLAMA = "ollama"
    NVIDIA = "nvidia"

    @classmethod
    def parse(cls, value: str) -> "LLMProvider":
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            known = ", ".join(p.value for p in cls)
            raise ValueError(f"unknown LLM provider {value!r}; known providers: {known}") from exc
