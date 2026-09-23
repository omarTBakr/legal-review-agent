"""The endpoints, with both models replaced by stubs. No weights are loaded, so
this runs anywhere the dependencies are installed."""

import base64

import numpy as np
import pytest
from fastapi.testclient import TestClient

import main
from asr import Transcription
from audio import write_wav

WAV = write_wav(np.sin(np.linspace(0, 20, 1600)).astype(np.float32), 16000)


class StubTranscriber:
    loaded = True
    device = "cpu"

    def __init__(self):
        self.calls = []
        self.error = None

    def transcribe(self, data, language=""):
        self.calls.append({"bytes": len(data), "language": language})
        if self.error:
            raise self.error
        return Transcription("What is the liability cap?", "English")


class StubSpeaker:
    loaded = True
    device = "cpu"

    def __init__(self):
        self.calls = []
        self.error = None

    def speak(self, text, voice="", language=""):
        wav, rate, _ = self.speak_with_timings(text, voice, language)

        return wav, rate

    def speak_with_timings(self, text, voice="", language=""):
        self.calls.append({"text": text, "voice": voice, "language": language})
        if self.error:
            raise self.error
        words = [{"word": "Twelve", "start": 0.0, "end": 0.4}, {"word": "months", "start": 0.4, "end": 0.9}]

        return WAV, 16000, words, "audio/ogg"


@pytest.fixture
def asr(monkeypatch):
    stub = StubTranscriber()
    monkeypatch.setattr(main, "transcriber", stub)
    return stub


@pytest.fixture
def tts(monkeypatch):
    stub = StubSpeaker()
    monkeypatch.setattr(main, "speaker", stub)
    return stub


@pytest.fixture
def client(asr, tts):
    # no lifespan: loading real models is exactly what these tests avoid
    return TestClient(main.app)


def test_health_reports_both_models(client):
    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["asr"]["loaded"] and body["tts"]["loaded"]
    assert "Qwen3-ASR" in body["asr"]["model"]


def test_health_says_which_voice_is_speaking(client):
    """Two engines exist and they sound nothing alike; say which one is loaded."""
    tts = client.get("/health").json()["tts"]

    assert tts["engine"] == "kokoro"
    assert "Kokoro" in tts["model"]
    assert tts["voice"] == "af_msa"


def test_a_recording_is_transcribed(client, asr):
    response = client.post("/transcribe", files={"audio": ("question.wav", WAV, "audio/wav")})

    assert response.json()["text"] == "What is the liability cap?"
    assert asr.calls[0]["bytes"] == len(WAV)


def test_a_language_hint_is_passed_through(client, asr):
    client.post("/transcribe", files={"audio": ("q.wav", WAV, "audio/wav")}, data={"language": "German"})

    assert asr.calls[0]["language"] == "German"


def test_the_language_the_model_detected_comes_back(client):
    """Qwen3-ASR identifies the language itself; that is worth reporting."""
    body = client.post("/transcribe", files={"audio": ("q.wav", WAV, "audio/wav")}).json()

    assert body["language"] == "English"


def test_an_empty_recording_is_refused(client, asr):
    response = client.post("/transcribe", files={"audio": ("q.wav", b"", "audio/wav")})

    assert response.status_code == 422
    assert asr.calls == []


def test_text_is_spoken_as_audio(client, tts):
    response = client.post("/speak", json={"text": "Twelve months of fees."})

    assert response.status_code == 200
    assert response.headers["x-sample-rate"] == "16000"
    assert response.content == WAV


def test_the_voice_and_language_reach_the_model(client, tts):
    client.post("/speak", json={"text": "Hello", "voice": "Nora", "language": "English"})

    assert tts.calls[0]["voice"] == "Nora"


def test_nothing_to_say_is_refused(client, tts):
    response = client.post("/speak", json={"text": "   "})

    assert response.status_code == 422
    assert tts.calls == []


def test_the_json_format_carries_the_word_timings(client, tts):
    """This is what lets the page highlight the word being read."""
    body = client.post("/speak?format=json", json={"text": "Twelve months of fees."}).json()

    assert body["rate"] == 16000
    assert body["words"][0] == {"word": "Twelve", "start": 0.0, "end": 0.4}
    assert base64.b64decode(body["audio"]) == WAV


def test_the_audio_says_what_it_is(client, tts):
    """The body is Opus by default now, so the media type has to travel with it."""
    response = client.post("/speak", json={"text": "Twelve months of fees."})

    assert response.headers["content-type"] == "audio/ogg"
    assert response.content == WAV


def test_the_json_reply_names_the_media_type_too(client, tts):
    body = client.post("/speak?format=json", json={"text": "Twelve months of fees."}).json()

    assert body["mime"] == "audio/ogg"


def test_a_model_failure_is_a_500_not_a_crash(client, tts):
    tts.error = RuntimeError("CUDA out of memory")

    response = client.post("/speak", json={"text": "Hello"})

    assert response.status_code == 500
    assert "synthesis failed" in response.json()["detail"]


def test_audio_that_is_not_audio_is_a_422(client, asr):
    """read_wav raises AudioError, which is the caller's fault, not ours."""
    from exceptions import AudioError

    asr.error = AudioError("could not read the audio")

    assert client.post("/transcribe", files={"audio": ("q.wav", b"nope", "audio/wav")}).status_code == 422
