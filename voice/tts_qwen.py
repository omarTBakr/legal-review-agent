"""
Qwen3-TTS: text in, a WAV out.

The CustomVoice model, which has its own voices built in. The Base model
clones a voice from a sample, which is a different feature with a different
consent question attached, and not one this service offers.

Not the default any more — `TTS_ENGINE=kokoro` is, being some 250 times faster
— but kept because it speaks ten languages to Kokoro's one, and because a
voice that can be compared against is worth more than one that cannot.
"""

import numpy as np

from audio import encode
from config import VoiceSettings
from logger import get_logger
from quantization import load_with

logger = get_logger(__name__)


class QwenSpeaker:
    """One loaded copy of Qwen3-TTS."""

    def __init__(self, settings: VoiceSettings):
        self._settings = settings
        self._model = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def device(self) -> str:
        return str(getattr(self._model, "device", "not loaded"))

    def load(self) -> None:
        if self.loaded:
            return

        import torch
        from qwen_tts import Qwen3TTSModel

        dtype = getattr(torch, self._settings.dtype)

        logger.info(
            "loading %s (%s, %s, %s)",
            self._settings.tts_model_id,
            self._settings.device,
            self._settings.dtype,
            self._settings.quantization,
        )

        self._model = load_with(
            Qwen3TTSModel.from_pretrained,
            self._settings.tts_model_id,
            self._settings.quantization,
            self._settings.dtype,
            device_map=self._settings.device,
            dtype=dtype,
        )

        logger.info("TTS ready on %s", self.device)

    def speak_with_timings(self, text: str, voice: str = "", language: str = "") -> tuple[bytes, int, list[dict], str]:
        """
        The audio, its media type, and no word timings.

        Qwen3-TTS does not report when each word is spoken. Estimating from
        word lengths was the alternative, and a highlight that drifts away from
        the voice is worse than no highlight at all.
        """
        audio, rate = self.speak(text, voice, language)
        media_type = "audio/ogg" if self._settings.audio_format == "opus" else "audio/wav"

        return audio, rate, [], media_type

    def speak(self, text: str, voice: str = "", language: str = "") -> tuple[bytes, int]:
        """
        Reads `text` aloud. Blocking: callers run it in a thread.

        Returns the WAV bytes and the sample rate the model produced, rather
        than assuming a rate the model is free to change.
        """
        self.load()

        wavs, rate = self._model.generate_custom_voice(
            text=text,
            language=language or self._settings.language,
            speaker=voice or self._settings.voice,
        )

        samples = np.asarray(wavs[0] if isinstance(wavs, (list, tuple)) else wavs, dtype=np.float32).reshape(-1)

        logger.info("spoke %d characters as %.2fs of audio", len(text), samples.size / rate if rate else 0)

        audio, _ = encode(samples, rate, self._settings.audio_format)

        return audio, rate
