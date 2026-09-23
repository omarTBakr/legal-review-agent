"""
Which synthesiser the service speaks with.

Two are available and they are not equivalent: Kokoro is a single English
voice that is hundreds of times faster than real time, Qwen3-TTS is ten
languages at roughly real time. The service asks for "the speaker" and this
decides, the same way `interfaces/llm_factory.py` decides which model answers
a question in the main project.
"""

from config import VoiceSettings
from logger import get_logger

logger = get_logger(__name__)

KOKORO = "kokoro"
QWEN = "qwen"
ENGINES = (KOKORO, QWEN)


def build_speaker(settings: VoiceSettings):
    """The synthesiser named by TTS_ENGINE."""
    engine = (settings.tts_engine or KOKORO).strip().lower()

    if engine == QWEN:
        from tts_qwen import QwenSpeaker

        return QwenSpeaker(settings)

    if engine != KOKORO:
        logger.warning("unknown TTS_ENGINE %r; using %s", engine, KOKORO)

    from tts_kokoro import KokoroSpeaker

    return KokoroSpeaker(settings)
