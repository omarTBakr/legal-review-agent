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

    async def speak_timed(self, text: str, voice: str = "", language: str = "") -> tuple[bytes, list[dict]]:
        """
        The same audio, plus `{word, start, end}` for each word spoken.

        The page uses these to follow the voice. An implementation whose model
        cannot say when it speaks each word returns an empty list, and the page
        simply does not highlight — better than a highlight that drifts.
        """
        return await self.speak(text, voice, language), []
