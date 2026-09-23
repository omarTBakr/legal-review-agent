from abc import ABC, abstractmethod


class TTSInterface(ABC):
    """What the rest of the project is allowed to know about speech synthesis."""

    @abstractmethod
    async def speak(self, text: str, voice: str = "", language: str = "") -> bytes:
        """
        Turns text into a WAV file's bytes.

        `voice` names one of the service's voices; empty means the configured
        default.
        """

    async def speak_timed(self, text: str, voice: str = "", language: str = "") -> tuple[bytes, list[dict], str]:
        """
        The same audio, its media type, and `{word, start, end}` per word.

        The page uses the timings to follow the voice. An implementation whose
        model cannot say when it speaks each word returns an empty list, and
        the page simply does not highlight — better than one that drifts. The
        media type travels with the bytes because the service decides the
        format, and a stored clip has to be served back as what it is.
        """
        return await self.speak(text, voice, language), [], "audio/wav"
