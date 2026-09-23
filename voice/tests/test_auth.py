"""
The voice service's copy of the shared-key gate.

The service holds no documents, but it will run two models on a GPU for anyone
who can reach the port. These tests check the same two things as the API's:
empty means open, and the two endpoints that cost GPU time are closed while
/health stays open for the readiness check.
"""

import base64

import numpy as np
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import auth
import main
from audio import write_wav

KEY = "s3cret-key-value"

WAV = write_wav(np.sin(np.linspace(0, 20, 1600)).astype(np.float32), 16000)


class StubTranscriber:
    loaded = True
    device = "cpu"

    def transcribe(self, data, language=""):
        from asr import Transcription

        return Transcription("What is the liability cap?", "English")


class StubSpeaker:
    loaded = True
    device = "cpu"

    def speak(self, text, voice="", language=""):
        return WAV, 16000

    def speak_with_timings(self, text, voice="", language=""):
        return WAV, 16000, [], "audio/ogg"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "transcriber", StubTranscriber())
    monkeypatch.setattr(main, "speaker", StubSpeaker())

    return TestClient(main.app)


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setenv("API_KEY", KEY)

    return KEY


def test_no_key_leaves_the_service_open(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)

    assert auth.is_open() is True


def test_a_configured_key_closes_it(keyed):
    assert auth.is_open() is False


def test_warn_if_open_only_warns_when_it_is(keyed, monkeypatch):
    assert auth.warn_if_open() is False

    monkeypatch.delenv("API_KEY")
    assert auth.warn_if_open() is True


@pytest.mark.parametrize(
    ("offered", "matches"),
    [(KEY, True), ("", False), (KEY[:-1], False), (KEY + "x", False)],
)
async def test_key_matches(offered, matches):
    assert auth.key_matches(offered, KEY) is matches


async def test_the_dependency_rejects_a_wrong_key(keyed):
    with pytest.raises(HTTPException) as caught:
        await auth.require_api_key("nope")

    assert caught.value.status_code == 401


async def test_the_dependency_accepts_the_right_key(keyed):
    assert await auth.require_api_key(KEY) is None


def test_health_stays_open(client, keyed):
    """The API's readiness check reads this and should not need the secret."""
    assert client.get("/health").status_code == 200


def test_transcribe_needs_the_key(client, keyed):
    response = client.post("/transcribe", files={"audio": ("q.wav", WAV, "audio/wav")})

    assert response.status_code == 401


def test_speak_needs_the_key(client, keyed):
    response = client.post("/speak", json={"text": "hello"})

    assert response.status_code == 401


def test_transcribe_works_with_the_key(client, keyed):
    response = client.post(
        "/transcribe",
        files={"audio": ("q.wav", WAV, "audio/wav")},
        headers={auth.HEADER_NAME: KEY},
    )

    assert response.status_code == 200
    assert "liability cap" in response.json()["text"]


def test_speak_works_with_the_key(client, keyed):
    """The default reply is the audio itself, not JSON — hence no `format=json`."""
    response = client.post("/speak", json={"text": "hello"}, headers={auth.HEADER_NAME: KEY})

    assert response.status_code == 200
    assert response.content == WAV


def test_speak_returns_timings_with_the_key(client, keyed):
    response = client.post("/speak?format=json", json={"text": "hello"}, headers={auth.HEADER_NAME: KEY})

    assert response.status_code == 200
    assert base64.b64decode(response.json()["audio"]) == WAV


def test_both_endpoints_work_when_no_key_is_set(client, monkeypatch):
    """The laptop case: nothing configured, nothing required."""
    monkeypatch.delenv("API_KEY", raising=False)

    assert client.post("/speak", json={"text": "hello"}).status_code == 200
    assert client.post("/transcribe", files={"audio": ("q.wav", WAV, "audio/wav")}).status_code == 200
