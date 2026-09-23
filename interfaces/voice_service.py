"""
The HTTP client for the voice service.

The models live in a separate process with its own dependencies (torch,
transformers, qwen-tts) and, usually, a GPU. This is the only place that knows
that; everything else asks `get_asr()` or `get_tts()` for "the model".

Shaped like `interfaces/openrouter_llm.py`: an injectable httpx client so tests
use MockTransport instead of the network, and every failure translated at the
boundary into the project's own exceptions.
"""

import base64
import binascii

import httpx

from exceptions.voice import SynthesisError, TranscriptionError, VoiceConfigurationError, VoiceUnavailableError
from interfaces.asr_interface import ASRInterface
from interfaces.tts_interface import TTSInterface
from utils.config import Settings
from utils.logger import get_logger

logger = get_logger(__name__)


class VoiceService(ASRInterface, TTSInterface):
    """Both halves of the voice service, behind one connection."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        if not settings.voice_service_url:
            raise VoiceConfigurationError("VOICE_SERVICE_URL is not set; the voice service runs as its own process")

        self._settings = settings
        self._client = client

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._settings.voice_service_url,
                timeout=self._settings.voice_timeout_seconds,
            )
        return self._client

    async def transcribe(self, audio: bytes, language: str = "") -> str:
        if not audio:
            raise TranscriptionError("there is no audio to transcribe")

        logger.info("transcribing %d bytes of audio", len(audio))

        body = await self._post(
            "/transcribe",
            TranscriptionError,
            files={"audio": ("question.wav", audio, "audio/wav")},
            data={"language": language or self._settings.tts_language},
        )

        try:
            text = str(body.json()["text"]).strip()
        except (ValueError, KeyError, TypeError) as exc:
            raise TranscriptionError(f"unexpected reply from the voice service: {body.text[:200]}") from exc

        if not text:
            raise TranscriptionError("nothing was said, or the recording was silent")

        return text

    async def speak(self, text: str, voice: str = "", language: str = "") -> bytes:
        audio, _ = await self.speak_timed(text, voice, language)

        return audio

    async def speak_timed(self, text: str, voice: str = "", language: str = "") -> tuple[bytes, list[dict]]:
        """
        The audio, and when each word in it is spoken.

        Asks the service for JSON rather than a WAV: the timings of a long
        answer run to kilobytes, which is too much for a response header and
        the reason the audio comes back base64-encoded here.
        """
        if not text.strip():
            raise SynthesisError("there is nothing to say")

        logger.info("synthesising %d characters", len(text))

        body = await self._post(
            "/speak",
            SynthesisError,
            params={"format": "json"},
            json={
                "text": text,
                "voice": voice or self._settings.tts_voice,
                "language": language or self._settings.tts_language,
            },
        )

        try:
            payload = body.json()
            audio = base64.b64decode(payload["audio"])
            words = [dict(word) for word in payload.get("words") or []]
        except (ValueError, KeyError, TypeError, binascii.Error) as exc:
            raise SynthesisError(f"unexpected reply from the voice service: {body.text[:200]}") from exc

        if not audio:
            raise SynthesisError("the voice service returned no audio")

        return audio, words

    async def _post(self, path: str, failure: type[Exception], **kwargs) -> httpx.Response:
        """One request, with the failures every call shares translated once."""
        try:
            response = await self._http().post(path, **kwargs)
        except httpx.TimeoutException as exc:
            raise VoiceUnavailableError(
                f"the voice service did not answer within {self._settings.voice_timeout_seconds}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise VoiceUnavailableError(
                f"could not reach the voice service at {self._settings.voice_service_url}: {exc}"
            ) from exc

        if response.status_code == 503:
            # the service is up but its models are not loaded yet
            raise VoiceUnavailableError(f"the voice service is not ready: {response.text[:200]}")

        if response.status_code >= 400:
            raise failure(f"the voice service returned {response.status_code}: {response.text[:200]}")

        return response

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
