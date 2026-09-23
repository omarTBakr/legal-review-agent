"""
Kokoro: text in, a WAV out, very fast.

`oddadmix/Kokoro-7M-Distill` is a 7.5M-parameter distillation of Kokoro-82M —
small enough to be quick on a laptop CPU and, on a GPU, to synthesise in a
fraction of the time the answer takes to read out. It is Apache 2.0, like the
model it was distilled from.

What it is not: multilingual. One English voice, `af_msa`, which is the style
pack it was distilled against — `af_heart` ships in the same repository and
sounds worse here, because the student never learnt it. For another language,
`TTS_ENGINE=qwen` is still there.

`voice/kokoro_patch.py` explains why the stock `kokoro` package needs a nudge
before it can load this checkpoint at all.
"""

import numpy as np
import torch

import kokoro_patch
from audio import encode
from config import VoiceSettings
from logger import get_logger

logger = get_logger(__name__)


def _timings_of(result, offset: float) -> list[dict]:
    """
    When each word in one segment is spoken, in seconds from the start.

    A token without timings — a word the model merged into its neighbour — is
    skipped rather than guessed at: a highlight on the wrong word is worse than
    one that stays put for a moment. So is punctuation, which is spoken as a
    pause and has nothing to light up.
    """
    timings = []

    for token in getattr(result, "tokens", None) or []:
        word = (getattr(token, "text", "") or "").strip()
        start, end = getattr(token, "start_ts", None), getattr(token, "end_ts", None)

        if not word or start is None or end is None:
            continue
        if not any(character.isalnum() for character in word):
            continue

        timings.append({"word": word, "start": round(offset + float(start), 3), "end": round(offset + float(end), 3)})

    return timings


# what the model produces; not negotiable, the vocoder was trained for it
SAMPLE_RATE = 24000

# American English, in kokoro's alphabet of language codes
LANG_CODE = "a"


class KokoroSpeaker:
    """One loaded copy of the distilled Kokoro model."""

    def __init__(self, settings: VoiceSettings):
        self._settings = settings
        self._pipeline = None
        self._voice = None
        self._device = "not loaded"

    @property
    def loaded(self) -> bool:
        return self._pipeline is not None

    @property
    def device(self) -> str:
        return self._device

    def load(self) -> None:
        """Downloads (first time) and loads the weights. Blocking."""
        if self.loaded:
            return

        # imported here so this module can be read on a machine with no torch
        from huggingface_hub import hf_hub_download
        from kokoro import KModel, KPipeline

        kokoro_patch.apply()

        repo = self._settings.tts_model_id
        device = self._resolved_device()

        logger.info("loading %s (%s)", repo, device)

        model = KModel(
            config=hf_hub_download(repo, self._settings.kokoro_config),
            model=hf_hub_download(repo, self._settings.kokoro_weights),
            # the complex-number path is slower and not needed here
            disable_complex=True,
        ).eval()

        self._pipeline = KPipeline(lang_code=LANG_CODE, model=model.to(device), device=device)
        self._voice = torch.load(
            hf_hub_download(repo, f"{self._settings.voice}.pt"),
            map_location="cpu",
            weights_only=True,
        )
        self._device = device

        logger.info("TTS ready on %s, voice %s", device, self._settings.voice)

    def _resolved_device(self) -> str:
        """
        Where to run it: the GPU if there is one, and it barely uses it.

        40 MB of VRAM against seven times the speed is an easy trade, but a
        machine with no CUDA, or one whose GPU is wanted for something else,
        still gets a synthesiser that is faster than real time.
        """
        wanted = (self._settings.tts_device or self._settings.device or "auto").lower()

        if wanted in ("auto", ""):
            return "cuda" if torch.cuda.is_available() else "cpu"

        if wanted.startswith("cuda") and not torch.cuda.is_available():
            logger.warning("no CUDA available; running the voice on the CPU")
            return "cpu"

        return wanted

    def speak(self, text: str, voice: str = "", language: str = "") -> tuple[bytes, int]:
        """
        Reads `text` aloud. Blocking: callers run it in a thread.

        `voice` and `language` are accepted so that every engine looks the same
        to the service; this model has one of each, and says so rather than
        pretending otherwise.
        """
        audio, rate, _, _ = self.speak_with_timings(text, voice, language)

        return audio, rate

    def speak_with_timings(self, text: str, voice: str = "", language: str = "") -> tuple[bytes, int, list[dict], str]:
        """
        The same audio, plus when each word is said.

        Kokoro reports a start and end for every token it speaks, so the page
        can follow the voice exactly rather than guessing from word lengths.
        The offsets are per segment, and the pipeline yields one segment at a
        time, so each segment's timings are shifted by the audio already
        produced before it.
        """
        self.load()

        if voice and voice != self._settings.voice:
            logger.warning("%s speaks only as %s; ignoring %r", self._settings.tts_model_id, self._settings.voice, voice)

        chunks, words = [], []
        elapsed = 0.0

        for result in self._pipeline(text, voice=self._voice, speed=1.0):
            piece = result.audio
            piece = piece.detach().cpu().numpy() if torch.is_tensor(piece) else np.asarray(piece)
            chunks.append(piece)

            words.extend(_timings_of(result, elapsed))
            elapsed += piece.size / SAMPLE_RATE

        if not chunks:
            raise ValueError("the model produced no audio for that text")

        samples = np.concatenate(chunks).astype(np.float32)

        audio, media_type = encode(samples, SAMPLE_RATE, self._settings.audio_format)

        logger.info(
            "spoke %d characters as %.1fs of audio, %d timed words, %d KB of %s",
            len(text),
            elapsed,
            len(words),
            len(audio) // 1024,
            self._settings.audio_format,
        )

        return audio, SAMPLE_RATE, words, media_type
