"""
The recordings, kept beside the review they belong to.

`<project_id>/chats/<task_id>/audio/<turn>-question.wav` and `-answer.wav` in
`S3_PROJECTS`, so a thread's audio sits with its text rather than in whichever
browser recorded it. S3 rather than a database or local disk: these are blobs,
the bucket is already where everything durable in this project lives, and a
local file dies with the container while a database row would only hold a
pointer to a bucket anyway.

Storing them is optional (`STORE_AUDIO`). They are a recording of someone
discussing a client's contract, which is worth being deliberate about: turn it
off and the transcript is still kept, the audio simply is not.
"""

import json

from exceptions.storage import ObjectNotFoundError, StorageError
from utils.chat_store import CHATS_FOLDER
from utils.config import Settings
from utils.logger import get_logger
from utils.projects import PROJECTS_ROOT, check_project_id
from utils.utility import download_s3_bytes, upload_s3_file

logger = get_logger(__name__)

AUDIO_FOLDER = "audio/"
QUESTION = "question"
ANSWER = "answer"


def audio_key(project_id: str, task_id: str, turn: int, kind: str) -> str:
    """Where one clip lives. `turn` is its index in the thread, from zero."""
    if kind not in (QUESTION, ANSWER):
        raise ValueError(f"audio is either a {QUESTION} or an {ANSWER}, not {kind!r}")

    return f"{PROJECTS_ROOT}{check_project_id(project_id)}/{CHATS_FOLDER}{task_id}/{AUDIO_FOLDER}{turn}-{kind}.wav"


def timings_key(project_id: str, task_id: str, turn: int) -> str:
    """Where the word timings for one spoken answer live, beside its audio."""
    return audio_key(project_id, task_id, turn, ANSWER).replace(".wav", ".words.json")


def store_timings(project_id: str, task_id: str, turn: int, words: list[dict], settings: Settings) -> str:
    """
    Keeps when each word was spoken, so a replay can highlight as well.

    A few hundred bytes beside a few hundred kilobytes of audio, and without
    them the second listen is the one that does not follow along.
    """
    if not settings.store_audio or not words:
        return ""

    key = timings_key(project_id, task_id, turn)

    try:
        upload_s3_file(json.dumps(words).encode("utf-8"), settings.s3_projects, key)
    except StorageError:
        logger.warning("[task %s] could not store the word timings", task_id, exc_info=True)
        return ""

    return key


def read_timings(project_id: str, task_id: str, turn: int, settings: Settings) -> list[dict]:
    """The stored timings, or none when they were never kept."""
    try:
        stored = json.loads(download_s3_bytes(settings.s3_projects, timings_key(project_id, task_id, turn)))
    except (ObjectNotFoundError, StorageError, ValueError):
        return []

    return [dict(word) for word in stored] if isinstance(stored, list) else []


def store_audio(project_id: str, task_id: str, turn: int, kind: str, wav: bytes, settings: Settings) -> str:
    """
    Keeps one clip and returns its key, or "" when audio is not being kept.

    A failure to store is logged and swallowed: the answer is already written
    and the transcript is already stored, and losing the recording is not worth
    failing the question over.
    """
    if not settings.store_audio or not wav:
        return ""

    key = audio_key(project_id, task_id, turn, kind)

    try:
        upload_s3_file(wav, settings.s3_projects, key)
    except StorageError:
        logger.warning("[task %s] could not store the %s audio", task_id, kind, exc_info=True)
        return ""

    logger.info("[task %s] stored %d bytes of %s audio at %s", task_id, len(wav), kind, key)

    return key


def read_audio(project_id: str, task_id: str, turn: int, kind: str, settings: Settings) -> bytes:
    """One stored clip. Raises ObjectNotFoundError when it was never kept."""
    return download_s3_bytes(settings.s3_projects, audio_key(project_id, task_id, turn, kind))


def has_audio(project_id: str, task_id: str, turn: int, kind: str, settings: Settings) -> bool:
    try:
        return bool(read_audio(project_id, task_id, turn, kind, settings))
    except (ObjectNotFoundError, StorageError):
        return False
