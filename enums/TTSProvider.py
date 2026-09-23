from enum import Enum


class TTSProvider(Enum):
    """Which TTSInterface implementation the factory should build."""

    VOICE_SERVICE = "voice_service"

    @classmethod
    def parse(cls, value: str) -> "TTSProvider":
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            known = ", ".join(provider.value for provider in cls)
            raise ValueError(f"unknown TTS provider {value!r}; known providers: {known}") from exc
