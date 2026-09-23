"""
`get_asr()` and `get_tts()`, the way `interfaces/llm_factory.py` provides
`get_llm()`: a route asks for the model it needs and never names a vendor.

Both halves are served by one `VoiceService` connection per provider, because
one process holds both models.
"""

from enums.ASRProvider import ASRProvider
from enums.TTSProvider import TTSProvider
from exceptions.voice import VoiceConfigurationError
from interfaces.asr_interface import ASRInterface
from interfaces.tts_interface import TTSInterface
from interfaces.voice_service import VoiceService
from utils.config import Settings, get_setting

_asr: dict[ASRProvider, ASRInterface] = {}
_tts: dict[TTSProvider, TTSInterface] = {}


def build_asr(provider: ASRProvider, settings: Settings) -> ASRInterface:
    if provider is ASRProvider.VOICE_SERVICE:
        return VoiceService(settings)

    raise VoiceConfigurationError(f"no implementation registered for ASR provider {provider.value}")


def build_tts(provider: TTSProvider, settings: Settings) -> TTSInterface:
    if provider is TTSProvider.VOICE_SERVICE:
        return VoiceService(settings)

    raise VoiceConfigurationError(f"no implementation registered for TTS provider {provider.value}")


def get_asr(provider: ASRProvider | str | None = None) -> ASRInterface:
    """The speech recogniser to use, cached per provider."""
    settings = get_setting()
    provider = _parse(ASRProvider, provider if provider is not None else settings.asr_provider)

    if provider not in _asr:
        _asr[provider] = build_asr(provider, settings)

    return _asr[provider]


def get_tts(provider: TTSProvider | str | None = None) -> TTSInterface:
    """The speech synthesiser to use, cached per provider."""
    settings = get_setting()
    provider = _parse(TTSProvider, provider if provider is not None else settings.tts_provider)

    if provider not in _tts:
        _tts[provider] = build_tts(provider, settings)

    return _tts[provider]


def set_asr(provider: ASRProvider, instance: ASRInterface) -> None:
    """Installs an implementation, for tests and for local fakes."""
    _asr[provider] = instance


def set_tts(provider: TTSProvider, instance: TTSInterface) -> None:
    _tts[provider] = instance


def reset_voice_cache() -> None:
    _asr.clear()
    _tts.clear()


def _parse(enum, value):
    if isinstance(value, enum):
        return value
    try:
        return enum.parse(value)
    except ValueError as exc:
        raise VoiceConfigurationError(str(exc)) from exc
