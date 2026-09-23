from abc import ABC, abstractmethod


class ASRInterface(ABC):
    """
    What the rest of the project is allowed to know about speech recognition.

    Routes depend on this, never on a model or a vendor, so the implementation
    can be swapped and tests can substitute a fake.
    """

    @abstractmethod
    async def transcribe(self, audio: bytes, language: str = "") -> str:
        """
        Turns spoken audio into text.

        `audio` is a WAV file's bytes. `language` is a hint; empty means the
        model decides for itself.
        """
