"""The audio handling, which needs no model and no GPU."""

import io

import numpy as np
import pytest
import soundfile as sf

from audio import AudioError, duration_seconds, read_wav, resample, write_wav


def tone(seconds=0.25, rate=44100, channels=1, frequency=440.0) -> bytes:
    """A WAV of a sine wave, the way a browser or curl would hand one over."""
    t = np.linspace(0.0, seconds, int(seconds * rate), endpoint=False)
    wave = (0.5 * np.sin(2 * np.pi * frequency * t)).astype(np.float32)

    if channels > 1:
        wave = np.stack([wave] * channels, axis=1)

    buffer = io.BytesIO()
    sf.write(buffer, wave, rate, format="WAV", subtype="PCM_16")

    return buffer.getvalue()


# --- reading -------------------------------------------------------------


def test_a_wav_is_read_as_mono_float32():
    samples = read_wav(tone())

    assert samples.dtype == np.float32
    assert samples.ndim == 1


def test_the_rate_is_brought_to_the_models_rate():
    samples = read_wav(tone(seconds=1.0, rate=44100), target_rate=16000)

    assert 15900 <= samples.size <= 16100


def test_audio_already_at_the_right_rate_is_left_alone():
    samples = read_wav(tone(seconds=1.0, rate=16000), target_rate=16000)

    assert samples.size == 16000


def test_stereo_is_mixed_down():
    """Two channels of the same voice must not become two half-loud ones."""
    stereo = read_wav(tone(channels=2))
    mono = read_wav(tone(channels=1))

    assert stereo.size == mono.size
    assert np.allclose(stereo, mono, atol=1e-3)


def test_bytes_that_are_not_audio_say_so():
    with pytest.raises(AudioError, match="could not read"):
        read_wav(b"this is not a wav file")


def test_an_empty_recording_says_so():
    with pytest.raises(AudioError):
        read_wav(b"")


# --- resampling ----------------------------------------------------------


def test_resampling_keeps_the_duration():
    samples = np.linspace(-1.0, 1.0, 8000, dtype=np.float32)

    resampled = resample(samples, 8000, 16000)

    assert resampled.size == 16000
    assert duration_seconds(resampled, 16000) == duration_seconds(samples, 8000)


def test_resampling_to_the_same_rate_changes_nothing():
    samples = np.linspace(-1.0, 1.0, 100, dtype=np.float32)

    assert np.array_equal(resample(samples, 16000, 16000), samples)


# --- writing -------------------------------------------------------------


def test_what_is_written_can_be_read_back():
    samples = np.sin(np.linspace(0, 20, 16000)).astype(np.float32)

    read, rate = sf.read(io.BytesIO(write_wav(samples, 16000)), dtype="float32")

    assert rate == 16000
    assert read.size == samples.size
    assert np.allclose(read, samples, atol=1e-3)


def test_audio_louder_than_full_scale_is_scaled_not_clipped():
    """A model can overshoot; clipping sounds far worse than turning it down."""
    samples = np.array([2.0, -2.0, 1.0], dtype=np.float32)

    read, _ = sf.read(io.BytesIO(write_wav(samples, 16000)), dtype="float32")

    assert np.max(np.abs(read)) <= 1.0
    assert np.sign(read[0]) == 1 and np.sign(read[1]) == -1


def test_writing_nothing_says_so():
    with pytest.raises(AudioError):
        write_wav(np.array([], dtype=np.float32), 16000)
