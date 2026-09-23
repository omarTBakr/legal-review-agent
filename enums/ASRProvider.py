from enum import Enum


class ASRProvider(Enum):
    """Which ASRInterface implementation the factory should build."""

    VOICE_SERVICE = "voice_service"

    @classmethod
    def parse(cls, value: str) -> "ASRProvider":
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            known = ", ".join(provider.value for provider in cls)
            raise ValueError(f"unknown ASR provider {value!r}; known providers: {known}") from exc
