"""
Settings for the voice service, read from the environment.

Deliberately plain: this process holds two models and an HTTP server, and
nothing here needs to know about buckets, Temporal or reviews.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceSettings:
    asr_model_id: str = "Qwen/Qwen3-ASR-0.6B"
    # "kokoro" (one English voice, hundreds of times faster than real time) or
    # "qwen" (ten languages, roughly real time)
    tts_engine: str = "kokoro"
    tts_model_id: str = "oddadmix/Kokoro-7M-Distill"
    # which files to pull from that repository; only kokoro uses them
    kokoro_config: str = "config.json"
    kokoro_weights: str = "kokoro_en_7m.pth"
    # where the voice runs, when it should differ from VOICE_DEVICE. Kokoro
    # wants about 40 MB of VRAM, so the GPU is nearly free for it.
    tts_device: str = ""
    # "opus" (a tenth the size, what the browser is served) or "wav"
    audio_format: str = "opus"
    device: str = "auto"
    dtype: str = "bfloat16"
    # "4bit", "8bit" or "none". Quantising a 0.6B model saves VRAM rather than
    # making it faster, and costs some accuracy; see the README before changing
    quantization: str = "4bit"
    # the style pack the distilled model was trained against; af_heart is in
    # the same repository and sounds worse, because the student never learnt it
    voice: str = "af_msa"
    language: str = "English"
    host: str = "127.0.0.1"
    port: int = 8100
    # the sample rate Qwen3-ASR expects; anything else is resampled on the way in
    sample_rate: int = 16000
    max_new_tokens: int = 256
    # load both models at startup, so the first question is not the slow one
    eager_load: bool = True
    # ...and synthesise one short phrase, because loading is not the whole cost:
    # torch compiles kernels on the first generation, which takes far longer
    # than the generation itself
    warm_up: bool = True

    @classmethod
    def from_env(cls) -> "VoiceSettings":
        return cls(
            asr_model_id=os.getenv("ASR_MODEL_ID", cls.asr_model_id),
            tts_engine=os.getenv("TTS_ENGINE", cls.tts_engine).strip().lower(),
            tts_model_id=os.getenv("TTS_MODEL_ID", cls.tts_model_id),
            kokoro_config=os.getenv("KOKORO_CONFIG", cls.kokoro_config),
            kokoro_weights=os.getenv("KOKORO_WEIGHTS", cls.kokoro_weights),
            tts_device=os.getenv("TTS_DEVICE", cls.tts_device),
            audio_format=os.getenv("VOICE_AUDIO_FORMAT", cls.audio_format).strip().lower(),
            device=os.getenv("VOICE_DEVICE", cls.device),
            dtype=os.getenv("VOICE_DTYPE", cls.dtype),
            quantization=os.getenv("VOICE_QUANTIZATION", cls.quantization).strip().lower(),
            voice=os.getenv("TTS_VOICE", cls.voice),
            language=os.getenv("TTS_LANGUAGE", cls.language),
            host=os.getenv("VOICE_HOST", cls.host),
            port=int(os.getenv("VOICE_PORT", cls.port)),
            max_new_tokens=int(os.getenv("ASR_MAX_NEW_TOKENS", cls.max_new_tokens)),
            eager_load=os.getenv("VOICE_EAGER_LOAD", "true").strip().lower() not in ("false", "0", "no"),
            warm_up=os.getenv("VOICE_WARM_UP", "true").strip().lower() not in ("false", "0", "no"),
        )


def get_settings() -> VoiceSettings:
    return VoiceSettings.from_env()
