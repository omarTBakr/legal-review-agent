"""The voice service client against httpx's MockTransport, the factories, and
the two routes that proxy to it. No models are loaded anywhere here."""

import httpx
import pytest
from fastapi.testclient import TestClient

from enums.ASRProvider import ASRProvider
from enums.TTSProvider import TTSProvider
from exceptions.voice import (
    SynthesisError,
    TranscriptionError,
    VoiceConfigurationError,
    VoiceUnavailableError,
)
from interfaces.voice_factory import get_asr, get_tts
from interfaces.voice_service import VoiceService
from main import app

WAV = b"RIFF$\x00\x00\x00WAVEfmt "


def spoken(audio: bytes = WAV, words=None, mime: str = "audio/ogg") -> dict:
    """What the voice service returns for a synthesis: audio, timings, format."""
    import base64

    return {"audio": base64.b64encode(audio).decode(), "rate": 24000, "words": words or [], "mime": mime}


def service(handler, settings) -> VoiceService:
    transport = httpx.MockTransport(handler)
    return VoiceService(settings, client=httpx.AsyncClient(transport=transport, base_url=settings.voice_service_url))


@pytest.fixture
def client(s3):
    return TestClient(app)


# --- transcribe ----------------------------------------------------------


async def test_a_recording_comes_back_as_text(settings):
    asr = service(lambda request: httpx.Response(200, json={"text": "  What is the liability cap?  "}), settings)

    assert await asr.transcribe(WAV) == "What is the liability cap?"


async def test_the_audio_is_sent_as_a_file(settings):
    seen = {}

    def handler(request):
        seen["content_type"] = request.headers.get("content-type", "")
        seen["length"] = len(request.content)
        return httpx.Response(200, json={"text": "hello"})

    await service(handler, settings).transcribe(WAV)

    assert "multipart/form-data" in seen["content_type"]
    assert seen["length"] > len(WAV)


async def test_empty_audio_never_reaches_the_service(settings):
    called = []

    def handler(request):
        called.append(request)
        return httpx.Response(200, json={"text": ""})

    with pytest.raises(TranscriptionError):
        await service(handler, settings).transcribe(b"")

    assert called == []


async def test_silence_is_reported_rather_than_answered(settings):
    """An empty transcript would otherwise become an empty question."""
    asr = service(lambda request: httpx.Response(200, json={"text": "   "}), settings)

    with pytest.raises(TranscriptionError, match="nothing was said"):
        await asr.transcribe(WAV)


async def test_an_unexpected_reply_shape_is_an_error(settings):
    asr = service(lambda request: httpx.Response(200, json={"nope": 1}), settings)

    with pytest.raises(TranscriptionError, match="unexpected reply"):
        await asr.transcribe(WAV)


# --- speak ---------------------------------------------------------------


async def test_the_words_come_back_with_their_timings(settings):
    """This is what the page follows to highlight the word being read."""
    words = [{"word": "Twelve", "start": 0.0, "end": 0.4}]
    tts = service(lambda request: httpx.Response(200, json=spoken(words=words)), settings)

    audio, timed, media_type = await tts.speak_timed("Twelve months.")

    assert audio == WAV
    assert timed == words
    assert media_type == "audio/ogg"


async def test_an_engine_with_no_timings_still_speaks(settings):
    """Qwen3-TTS cannot say when it speaks each word; the audio is still fine."""
    tts = service(lambda request: httpx.Response(200, json=spoken()), settings)

    audio, timed, _ = await tts.speak_timed("Twelve months.")

    assert audio == WAV and timed == []


async def test_a_reply_that_is_not_the_expected_shape_is_an_error(settings):
    tts = service(lambda request: httpx.Response(200, json={"nope": 1}), settings)

    with pytest.raises(SynthesisError, match="unexpected reply"):
        await tts.speak_timed("Hello")


async def test_text_comes_back_as_audio(settings):
    tts = service(lambda request: httpx.Response(200, json=spoken()), settings)

    assert await tts.speak("Twelve months of fees.") == WAV


async def test_the_configured_voice_and_language_are_sent(settings):
    seen = {}

    def handler(request):
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, json=spoken())

    await service(handler, settings).speak("Hello")

    assert seen["voice"] == settings.tts_voice
    assert seen["language"] == settings.tts_language


async def test_nothing_to_say_is_refused(settings):
    with pytest.raises(SynthesisError):
        await service(lambda request: httpx.Response(200, json=spoken()), settings).speak("   ")


async def test_audio_that_never_arrives_is_an_error(settings):
    tts = service(lambda request: httpx.Response(200, json=spoken(audio=b"")), settings)

    with pytest.raises(SynthesisError, match="no audio"):
        await tts.speak("Hello")


# --- the service being down ----------------------------------------------


async def test_an_unreachable_service_says_so(settings):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(VoiceUnavailableError, match="could not reach"):
        await service(handler, settings).transcribe(WAV)


async def test_a_timeout_says_so(settings):
    def handler(request):
        raise httpx.ReadTimeout("too slow")

    with pytest.raises(VoiceUnavailableError, match="did not answer"):
        await service(handler, settings).speak("Hello")


async def test_a_service_whose_models_are_not_loaded_is_unavailable(settings):
    """503 means "not ready yet", which is worth distinguishing from a failure."""
    asr = service(lambda request: httpx.Response(503, text="models still loading"), settings)

    with pytest.raises(VoiceUnavailableError, match="not ready"):
        await asr.transcribe(WAV)


async def test_a_failure_inside_the_service_is_a_transcription_error(settings):
    asr = service(lambda request: httpx.Response(500, text="boom"), settings)

    with pytest.raises(TranscriptionError, match="500"):
        await asr.transcribe(WAV)


def test_no_service_url_is_a_configuration_error(settings, monkeypatch):
    monkeypatch.setattr(settings, "voice_service_url", "")

    with pytest.raises(VoiceConfigurationError, match="VOICE_SERVICE_URL"):
        VoiceService(settings)


# --- the factories -------------------------------------------------------


def test_the_factories_build_and_cache(settings):
    assert get_asr() is get_asr()
    assert get_tts() is get_tts()


def test_an_unknown_provider_is_a_configuration_error(settings):
    with pytest.raises(VoiceConfigurationError, match="unknown"):
        get_asr("carrier-pigeon")


def test_every_provider_has_an_implementation(settings):
    for provider in ASRProvider:
        assert get_asr(provider) is not None
    for provider in TTSProvider:
        assert get_tts(provider) is not None


# --- the routes ----------------------------------------------------------


def test_transcribe_returns_the_text(client, voice):
    response = client.post("/voice/transcribe", files={"audio": ("question.wav", WAV, "audio/wav")})

    assert response.json()["text"] == voice.transcript
    assert voice.calls[0]["kind"] == "transcribe"


def test_a_recording_with_nowhere_to_file_it_is_not_stored(client, voice, s3, settings):
    """Without a project, a review and a turn, audio is transcribed and dropped."""
    body = client.post("/voice/transcribe", files={"audio": ("question.wav", WAV, "audio/wav")}).json()

    assert body["audio_key"] == ""
    assert not [key for bucket, key in s3.objects if "audio" in key]


def test_a_recording_is_kept_beside_its_thread(client, voice, s3, settings):
    from utils.projects import create_project

    project = create_project("Acme", settings)

    body = client.post(
        "/voice/transcribe",
        files={"audio": ("question.wav", WAV, "audio/wav")},
        data={"project_id": project.id, "task_id": "abc123", "turn": "0"},
    ).json()

    assert body["audio_key"] == f"{project.id}/chats/abc123/audio/0-question.wav"
    assert s3.objects[(settings.s3_projects, body["audio_key"])] == WAV


def test_audio_is_not_kept_when_storing_it_is_turned_off(client, voice, s3, settings, monkeypatch):
    """STORE_AUDIO=false keeps the transcript and drops the recording."""
    from utils.projects import create_project

    monkeypatch.setattr(settings, "store_audio", False)
    project = create_project("Acme", settings)

    body = client.post(
        "/voice/transcribe",
        files={"audio": ("question.wav", WAV, "audio/wav")},
        data={"project_id": project.id, "task_id": "abc123", "turn": "0"},
    ).json()

    assert body["text"] == voice.transcript
    assert body["audio_key"] == ""
    assert not [key for bucket, key in s3.objects if "audio" in key]


def test_a_spoken_answer_is_kept_and_noted_on_its_turn(client, voice, s3, settings):
    from schemas.chat import ChatTurn
    from utils.chat_store import append_turn, read_thread
    from utils.projects import create_project

    project = create_project("Acme", settings)
    append_turn(project.id, "abc123", ChatTurn(question="What is the cap?", answer="Twelve months."), settings)

    client.post(
        "/voice/speak",
        json={"text": "Twelve months.", "project_id": project.id, "task_id": "abc123", "turn": 0},
    )

    [turn] = read_thread(project.id, "abc123", settings).turns
    assert turn.answer_audio == f"{project.id}/chats/abc123/audio/0-answer.ogg"
    assert s3.objects[(settings.s3_projects, turn.answer_audio)] == voice.audio


def test_an_empty_recording_is_rejected(client, voice):
    response = client.post("/voice/transcribe", files={"audio": ("question.wav", b"", "audio/wav")})

    assert response.status_code == 422
    assert voice.calls == []


def test_asking_for_json_returns_the_audio_and_its_timings(client, voice):
    import base64

    response = client.post("/voice/speak", json={"text": "Twelve months."}, headers={"Accept": "application/json"})

    body = response.json()
    assert base64.b64decode(body["audio"]) == voice.audio
    assert body["words"] == voice.words


def test_the_timings_are_stored_beside_the_answer(client, voice, s3, settings):
    from schemas.chat import ChatTurn
    from utils.chat_store import append_turn
    from utils.projects import create_project

    project = create_project("Acme", settings)
    append_turn(project.id, "abc123", ChatTurn(question="?", answer="Twelve months."), settings)

    client.post(
        "/voice/speak",
        json={"text": "Twelve months.", "project_id": project.id, "task_id": "abc123", "turn": 0},
    )

    stored = client.get(f"/voice/timings/{project.id}/abc123/0").json()
    assert stored["words"] == voice.words


def test_timings_that_were_never_kept_come_back_empty(client, voice, s3, settings):
    from utils.projects import create_project

    project = create_project("Acme", settings)

    assert client.get(f"/voice/timings/{project.id}/abc123/0").json() == {"words": []}


def test_speak_returns_the_audio_as_what_it_is(client, voice):
    """Opus by default, so the media type has to travel with the bytes."""
    response = client.post("/voice/speak", json={"text": "Twelve months of fees."})

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/ogg"
    assert response.content == voice.audio


def test_speaking_uses_the_configured_voice(client, voice, settings):
    client.post("/voice/speak", json={"text": "Hello"})

    assert voice.calls[0]["voice"] == settings.tts_voice


def test_too_much_text_is_refused(client, voice):
    response = client.post("/voice/speak", json={"text": "a" * 4001})

    assert response.status_code == 422
    assert voice.calls == []


def test_a_voice_service_that_is_down_is_a_503(client, voice):
    voice.error = VoiceUnavailableError("nothing listening on :8100")

    response = client.post("/voice/speak", json={"text": "Hello"})

    assert response.status_code == 503
    assert "voice service unavailable" in response.json()["detail"]
