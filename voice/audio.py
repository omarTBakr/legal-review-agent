"""
Audio in and out, with no model and no torch in sight.

Kept apart from asr.py and tts.py so the fiddly parts — channels, sample rates,
WAV headers — can be tested on a machine with no GPU and no weights downloaded.
"""

import io

import numpy as np
import soundfile as sf


class AudioError(ValueError):
    """The bytes we were handed are not audio we can use."""


def read_wav(data: bytes, target_rate: int = 16000) -> np.ndarray:
    """
    Decodes a WAV into mono float32 at `target_rate`.

    The browser already sends 16 kHz mono, but curl and other callers do not,
    so this is where any of that is made true rather than in the model code.
    """
    try:
        samples, rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except Exception as exc:
        raise AudioError(f"could not read the audio: {exc}") from exc

    if samples.size == 0:
        raise AudioError("the recording is empty")

    mono = samples.mean(axis=1)

    return resample(mono, rate, target_rate)


def resample(samples: np.ndarray, rate: int, target_rate: int) -> np.ndarray:
    """
    Linear resampling, which is plenty for speech going into an ASR model.

    A polyphase filter would be better for music; bringing scipy along for a
    16 kHz voice recording would not be.
    """
    if rate == target_rate or samples.size == 0:
        return samples.astype(np.float32, copy=False)

    duration = samples.size / rate
    target_count = max(1, int(round(duration * target_rate)))

    source_points = np.linspace(0.0, duration, num=samples.size, endpoint=False)
    target_points = np.linspace(0.0, duration, num=target_count, endpoint=False)

    return np.interp(target_points, source_points, samples).astype(np.float32)


def write_wav(samples: np.ndarray, rate: int) -> bytes:
    """Encodes samples as a 16-bit WAV, which every browser can play."""
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)

    if samples.size == 0:
        raise AudioError("there is no audio to write")

    peak = float(np.max(np.abs(samples)))
    if peak > 1.0:
        # a model can overshoot; clipping in the file sounds worse than scaling
        samples = samples / peak

    buffer = io.BytesIO()
    sf.write(buffer, samples, rate, format="WAV", subtype="PCM_16")

    return buffer.getvalue()


def duration_seconds(samples: np.ndarray, rate: int) -> float:
    return round(samples.size / rate, 2) if rate else 0.0
