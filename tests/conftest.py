import asyncio
import io
import json

import pymupdf
import pytest
from botocore.exceptions import ClientError

import interfaces.llm_factory
import interfaces.voice_factory
import utils.config
import utils.review_context
import utils.utility
from enums.ASRProvider import ASRProvider
from enums.LLMProvider import LLMProvider
from enums.PromptName import PromptName
from enums.TTSProvider import TTSProvider
from interfaces.asr_interface import ASRInterface
from interfaces.llm_interface import LLMInterface
from interfaces.tts_interface import TTSInterface


@pytest.fixture(autouse=True)
def settings(tmp_path, monkeypatch):
    """
    Points every test at a throwaway scratch directory and fake credentials,
    so the suite never needs a real .env or a real bucket.

    TEMP_PD_DIR is absolute here, which pathlib lets take over from the project
    root that Settings.temp_root would otherwise prepend.
    """
    env = {
        "AWS_ACCESS_KEY_ID": "test-key",
        "AWS_SECRET_ACCESS_KEY": "test-secret",
        "AWS_REGION": "us-east-1",
        "AWS_ENDPOINT_URL": "https://s3.example.com",
        "S3_PDF_BUCKET": "test-pdfs",
        # trailing space on purpose: the strip validator has to cope with it
        "S3_PARSED_MDS": "test-mds ",
        "TEMP_PD_DIR": str(tmp_path),
        "TEMP_PDF_FOLDER": "TEMP_PDF",
        "TEMP_MD_FOLDER": "TEMP_MD",
        "API_HOST": "127.0.0.1",
        "API_PORT": "9999",
        "LLM_PROVIDER": "openrouter",
        "OPENROUTER_API_KEY": "test-llm-key",
        "OPENROUTER_MODEL": "test/model",
        "S3_LEGAL_ADVICE": "test-advice",
        "S3_PROJECTS": "test-projects",
        "LEGAL_TASK_QUEUE": "test-legal-queue",
        "LEGAL_MAX_CONCURRENT_PDFS": "2",
        "LEGAL_PAGES_PER_BATCH": "2",
        "LEGAL_MAX_PDFS": "5",
        "HUMAN_INPUT_TIMEOUT_SECONDS": "5",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    # these modules cache singletons, so clear them between tests
    monkeypatch.setattr(utils.config, "_settings_instance", None)
    monkeypatch.setattr(utils.utility, "_s3_client", None)
    monkeypatch.setattr(interfaces.llm_factory, "_instances", {})
    monkeypatch.setattr(interfaces.voice_factory, "_asr", {})
    monkeypatch.setattr(interfaces.voice_factory, "_tts", {})
    utils.review_context.clear_cache()

    return utils.config.get_setting()


class FakeS3Client:
    """Stands in for the boto3 client, backed by a dict of {(bucket, key): bytes}."""

    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body):  # noqa: N803 - boto3 spells them this way
        self.objects[(Bucket, Key)] = Body

    def upload_file(self, filename, bucket, key):
        self.objects[(bucket, key)] = open(filename, "rb").read()

    def delete_object(self, Bucket, Key):  # noqa: N803
        self.objects.pop((Bucket, Key), None)

    def get_object(self, Bucket, Key):  # noqa: N803 - boto3 spells them this way
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "Not Found"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def list_objects_v2(self, Bucket, Prefix="", ContinuationToken=None):  # noqa: N803
        keys = sorted(key for bucket, key in self.objects if bucket == Bucket and key.startswith(Prefix))
        return {"Contents": [{"Key": key} for key in keys], "IsTruncated": False}

    def download_file(self, bucket, key, filename):
        if (bucket, key) not in self.objects:
            # mirrors what boto3 raises, so the error wrapping is exercised
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "GetObject")
        with open(filename, "wb") as handle:
            handle.write(self.objects[(bucket, key)])


@pytest.fixture
def s3(monkeypatch):
    """Replaces the real S3 client with the in-memory fake."""
    client = FakeS3Client()
    monkeypatch.setattr(utils.utility, "_s3_client", client)
    return client


@pytest.fixture
def pdf_bytes():
    """A tiny one-page PDF with known text in it."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Quarterly Report", fontsize=22)
    page.insert_text((72, 140), "Revenue grew 12 percent this quarter.", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


class FakeLLM(LLMInterface):
    """
    Stands in for a real model.

    Returns a scripted reply per prompt name, records what it was asked, and
    tracks how many calls are in flight at once so a test can assert the
    workflow's concurrency cap.
    """

    def __init__(self):
        self.replies = {}
        self.calls = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.delay = 0.0
        self.error = None

    def script(self, name: PromptName, reply):
        """`reply` is a dict (serialised to JSON) or a raw string."""
        self.replies[name] = reply

    async def complete(self, prompt, **variables) -> str:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            self.calls.append({"prompt": prompt.name, "variables": variables})

            if self.delay:
                await asyncio.sleep(self.delay)
            if self.error is not None:
                raise self.error

            reply = self.replies.get(prompt.name, DEFAULT_ADVICE)
            return reply if isinstance(reply, str) else json.dumps(reply)
        finally:
            self.in_flight -= 1

    def calls_for(self, name: PromptName) -> list:
        return [call for call in self.calls if call["prompt"] is name]


DEFAULT_ADVICE = {
    "summary": "A short services agreement.",
    "key_risks": [
        {"description": "Unlimited liability", "severity": "high", "location": "clause 9"},
    ],
    "needs_human": False,
    "question": "",
}


@pytest.fixture
def llm(monkeypatch):
    """Installs a FakeLLM for every provider the factory might be asked for."""
    fake = FakeLLM()
    monkeypatch.setattr(interfaces.llm_factory, "_instances", {provider: fake for provider in LLMProvider})
    return fake


class FakeVoice(ASRInterface, TTSInterface):
    """Stands in for the voice service: scripted text in, scripted audio out."""

    def __init__(self):
        self.transcript = "What is the liability cap?"
        self.audio = b"OggS....fake opus"
        self.media_type = "audio/ogg"
        self.words = [{"word": "Twelve", "start": 0.0, "end": 0.4}, {"word": "months", "start": 0.4, "end": 0.9}]
        self.calls = []
        self.error = None

    async def transcribe(self, audio: bytes, language: str = "") -> str:
        self.calls.append({"kind": "transcribe", "bytes": len(audio), "language": language})
        if self.error is not None:
            raise self.error
        return self.transcript

    async def speak(self, text: str, voice: str = "", language: str = "") -> bytes:
        audio, _ = await self.speak_timed(text, voice, language)

        return audio

    async def speak_timed(self, text: str, voice: str = "", language: str = "") -> tuple[bytes, list[dict], str]:
        self.calls.append({"kind": "speak", "text": text, "voice": voice, "language": language})
        if self.error is not None:
            raise self.error
        return self.audio, self.words, self.media_type


@pytest.fixture
def voice(monkeypatch):
    """Installs a FakeVoice for every ASR and TTS provider."""
    fake = FakeVoice()
    monkeypatch.setattr(interfaces.voice_factory, "_asr", {provider: fake for provider in ASRProvider})
    monkeypatch.setattr(interfaces.voice_factory, "_tts", {provider: fake for provider in TTSProvider})
    return fake


@pytest.fixture
def multi_page_pdf_bytes():
    """A six-page PDF, so LEGAL_PAGES_PER_BATCH=2 yields three batches."""
    doc = pymupdf.open()
    for page_number in range(1, 7):
        page = doc.new_page()
        page.insert_text((72, 100), f"Clause {page_number}", fontsize=20)
        page.insert_text((72, 140), f"Body text for clause {page_number}.", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data
