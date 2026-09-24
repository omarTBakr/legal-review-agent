"""
Speech in and out.

Thin proxies to the voice service, which runs as its own process with its own
dependencies and a GPU. Going through the API means the browser never has to
reach the GPU box itself, and the models can move without the UI knowing.
"""

import asyncio
import base64

from fastapi import APIRouter, Body, File, Form, Request, Response, UploadFile
from fastapi.responses import JSONResponse

from exceptions.voice import SynthesisError, TranscriptionError
from interfaces.voice.factory import get_asr, get_tts
from utils.audio_store import ANSWER, QUESTION, read_timings, store_audio, store_timings
from utils.chat_store import read_thread, save_thread
from utils.config import get_setting
from utils.http_errors import http_errors
from utils.logger import get_logger

router = APIRouter(prefix="/voice", tags=["voice"])

logger = get_logger(__name__)

# a spoken question is short; this is a generous ceiling on a 16 kHz mono WAV
MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_SPOKEN_CHARACTERS = 4000


@router.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    language: str = Form(""),
    project_id: str = Form(""),
    task_id: str = Form(""),
    turn: int = Form(-1),
) -> dict:
    """
    Turns a recorded question into text. The browser sends 16 kHz mono WAV.

    With a project, a review and a turn number, the recording is kept in the
    bucket beside that thread; without them it is transcribed and dropped.
    """
    settings = get_setting()

    with http_errors("a transcription"):
        data = await audio.read()

        if not data:
            raise TranscriptionError("the recording is empty")
        if len(data) > MAX_AUDIO_BYTES:
            raise TranscriptionError(f"the recording is larger than {MAX_AUDIO_BYTES // (1024 * 1024)} MB")

        text = await get_asr().transcribe(data, language)

        key = ""
        if project_id and task_id and turn >= 0:
            key = await asyncio.to_thread(store_audio, project_id, task_id, turn, QUESTION, data, settings)

    logger.info("transcribed %d bytes into %d characters", len(data), len(text))

    return {"text": text, "audio_key": key}


@router.post("/speak")
async def speak(
    request: Request,
    text: str = Body(..., embed=True),
    voice: str = Body("", embed=True),
    language: str = Body("", embed=True),
    project_id: str = Body("", embed=True),
    task_id: str = Body("", embed=True),
    turn: int = Body(-1, embed=True),
) -> Response:
    """
    Reads an answer aloud, as a WAV the browser can play.

    Ask for `application/json` and the audio comes back base64-encoded with the
    time each word is spoken, which is what the page follows to highlight the
    word being read. Everything else gets the WAV alone.

    With a project, a review and a turn number, the audio and its timings are
    kept, so playing it again later costs nothing and still follows along.
    """
    settings = get_setting()

    with http_errors("a synthesis"):
        if len(text) > MAX_SPOKEN_CHARACTERS:
            raise SynthesisError(f"at most {MAX_SPOKEN_CHARACTERS} characters can be read aloud at once")

        audio, words, media_type = await get_tts().speak_timed(
            text, voice or settings.tts_voice, language or settings.tts_language
        )

        if project_id and task_id and turn >= 0:
            await asyncio.to_thread(_remember_answer, project_id, task_id, turn, audio, words, media_type, settings)

    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"audio": base64.b64encode(audio).decode(), "words": words, "mime": media_type})

    return Response(content=audio, media_type=media_type, headers={"Cache-Control": "no-store"})


@router.get("/timings/{project_id}/{task_id}/{turn}")
async def timings(project_id: str, task_id: str, turn: int) -> dict:
    """The stored word timings for one answer, so a replay can follow along."""
    settings = get_setting()

    with http_errors(f"task {task_id}"):
        return {"words": await asyncio.to_thread(read_timings, project_id, task_id, turn, settings)}


def _remember_answer(project_id: str, task_id: str, turn: int, audio: bytes, words: list, media_type: str, settings) -> None:
    """Stores the spoken answer and notes it on its turn. Runs in a thread."""
    key = store_audio(project_id, task_id, turn, ANSWER, audio, settings, media_type)
    if not key:
        return

    store_timings(project_id, task_id, turn, words, settings)

    thread = read_thread(project_id, task_id, settings)
    if turn < len(thread.turns):
        thread.turns[turn].answer_audio = key
        save_thread(thread, settings)
