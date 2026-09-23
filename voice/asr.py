"""
Qwen3-ASR: spoken audio in, text out.

Through Qwen's own `qwen-asr` package rather than the transformers `-hf` path:
the `-hf` variant needs transformers 5.x, and `qwen-tts` — which this service
also loads — pins 4.57. One process cannot have both, and two processes for
two 0.6B models sharing one GPU would be worse.
"""

import tempfile
from pathlib import Path

import soundfile as sf

from audio import read_wav
from config import VoiceSettings
from logger import get_logger
from quantization import load_with

logger = get_logger(__name__)


class Transcription:
    """What one recording turned out to be."""

    def __init__(self, text: str, language: str = ""):
        self.text = text
        self.language = language


class Transcriber:
    """One loaded copy of the ASR model."""

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
        """Downloads (first time) and loads the weights. Blocking, and slow."""
        if self.loaded:
            return

        # imported here so this module can be read, and its tests run, on a
        # machine with no torch installed
        import torch
        from qwen_asr import Qwen3ASRModel

        logger.info(
            "loading %s (%s, %s, %s)",
            self._settings.asr_model_id,
            self._settings.device,
            self._settings.dtype,
            self._settings.quantization,
        )

        self._model = load_with(
            Qwen3ASRModel.from_pretrained,
            self._settings.asr_model_id,
            self._settings.quantization,
            self._settings.dtype,
            dtype=getattr(torch, self._settings.dtype),
            device_map=self._settings.device,
        )

        logger.info("ASR ready on %s", self.device)

    def transcribe(self, data: bytes, language: str = "") -> Transcription:
        """
        Turns one recording into text. Blocking: callers run it in a thread.

        The audio is normalised to 16 kHz mono here and handed over as a file,
        which is the form the package documents; it saves guessing at how it
        would interpret a bare array's sample rate.
        """
        self.load()

        samples = read_wav(data, self._settings.sample_rate)

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "question.wav"
            sf.write(str(path), samples, self._settings.sample_rate, format="WAV", subtype="PCM_16")

            request = {"audio": str(path)}
            if language:
                request["language"] = language

            results = self._model.transcribe(**request)

        if not results:
            return Transcription(text="")

        first = results[0]

        return Transcription(text=str(getattr(first, "text", "")).strip(), language=str(getattr(first, "language", "") or ""))
