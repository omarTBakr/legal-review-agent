"""Which synthesiser the service builds, and the patch that lets the distilled
Kokoro checkpoint load at all. No weights are downloaded here."""

import pytest
import torch

from config import VoiceSettings
from tts import build_speaker


def settings(**overrides) -> VoiceSettings:
    return VoiceSettings(**overrides)


# --- the factory ---------------------------------------------------------


def test_kokoro_is_the_default():
    assert type(build_speaker(settings())).__name__ == "KokoroSpeaker"


def test_qwen_is_still_available():
    """Ten languages, when one English voice is not enough."""
    assert type(build_speaker(settings(tts_engine="qwen"))).__name__ == "QwenSpeaker"


def test_an_unknown_engine_falls_back_rather_than_failing():
    assert type(build_speaker(settings(tts_engine="gramophone"))).__name__ == "KokoroSpeaker"


def test_the_engine_name_is_not_case_sensitive():
    assert type(build_speaker(settings(tts_engine="QWEN"))).__name__ == "QwenSpeaker"


# --- where it runs -------------------------------------------------------


def test_the_gpu_is_used_when_there_is_one():
    speaker = build_speaker(settings(tts_device="auto"))

    expected = "cuda" if torch.cuda.is_available() else "cpu"
    assert speaker._resolved_device() == expected


def test_cuda_asked_for_without_a_gpu_falls_back_to_the_cpu(monkeypatch):
    """A machine with no GPU should still speak, not refuse to start."""
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)

    assert build_speaker(settings(tts_device="cuda"))._resolved_device() == "cpu"


def test_the_cpu_can_be_asked_for_explicitly():
    assert build_speaker(settings(tts_device="cpu"))._resolved_device() == "cpu"


# --- the patch -----------------------------------------------------------


def test_the_decoder_takes_its_width_from_the_config():
    """Upstream hardcodes 1024/512, which cannot load a 7M distillation."""
    import kokoro_patch

    kokoro_patch.apply()
    from kokoro.istftnet import Decoder

    decoder = Decoder(
        dim_in=160,
        style_dim=128,
        dim_out=80,
        resblock_kernel_sizes=[3, 7],
        upsample_rates=[10, 6],
        upsample_initial_channel=128,
        resblock_dilation_sizes=[[1, 3, 5], [1, 3, 5]],
        upsample_kernel_sizes=[20, 12],
        gen_istft_n_fft=20,
        gen_istft_hop_size=5,
        hidden_channels=256,
        out_channels=128,
    )

    assert decoder.encode.conv1.in_channels == 160 + 2
    assert decoder.decode[-1].conv2.out_channels == 128
    # this one reads the text encoder's output, not the decode width
    assert decoder.asr_res[0].in_channels == 160


def test_the_weights_are_built_the_way_the_checkpoint_stores_them():
    """The checkpoint uses parametrized weight norm; the old API would silently
    skip every normalised layer and the model would speak noise."""
    import kokoro_patch

    kokoro_patch.apply()
    from kokoro.istftnet import Decoder

    decoder = Decoder(
        dim_in=160,
        style_dim=128,
        dim_out=80,
        resblock_kernel_sizes=[3, 7],
        upsample_rates=[10, 6],
        upsample_initial_channel=128,
        resblock_dilation_sizes=[[1, 3, 5], [1, 3, 5]],
        upsample_kernel_sizes=[20, 12],
        gen_istft_n_fft=20,
        gen_istft_hop_size=5,
        hidden_channels=256,
        out_channels=128,
    )

    keys = decoder.state_dict().keys()
    assert any("parametrizations.weight.original0" in key for key in keys)
    assert not any(key.endswith("weight_g") for key in keys)


def test_applying_the_patch_twice_is_harmless():
    import kokoro_patch

    kokoro_patch.apply()
    kokoro_patch.apply()


@pytest.mark.parametrize("default", [1024, 512])
def test_the_stock_widths_are_still_the_defaults(default):
    """An 82M checkpoint has to keep loading unchanged."""
    import kokoro_patch

    assert default in (kokoro_patch.DEFAULT_HIDDEN, kokoro_patch.DEFAULT_OUT)
