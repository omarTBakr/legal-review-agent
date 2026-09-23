"""
The chat thread for one review, kept beside it in the bucket.

`<project_id>/chats/<task_id>.json` in `S3_PROJECTS`, so the questions asked
about a review live with the review rather than in whichever browser asked
them, and survive both a reload and Temporal's retention window.

One object per thread, read and rewritten on each turn: last write wins. Two
people asking into the same review in the same second would lose a turn, which
is not a way this tool is used — and an object per turn would cost a request
per turn just to read the thread back.

Blocking boto3 work, like `utils/projects.py`: callers wrap it in a thread.
"""

import json
from datetime import UTC, datetime

from exceptions.storage import ObjectNotFoundError
from schemas.chat import ChatThread, ChatTurn
from utils.config import Settings
from utils.logger import get_logger
from utils.projects import PROJECTS_ROOT, check_project_id
from utils.utility import download_s3_bytes, upload_s3_file

logger = get_logger(__name__)

CHATS_FOLDER = "chats/"


def thread_key(project_id: str, task_id: str) -> str:
    return f"{PROJECTS_ROOT}{project_id}/{CHATS_FOLDER}{task_id}.json"


def read_thread(project_id: str, task_id: str, settings: Settings) -> ChatThread:
    """The thread so far. A review nobody has asked about yet reads as empty."""
    project_id = check_project_id(project_id)

    try:
        stored = json.loads(download_s3_bytes(settings.s3_projects, thread_key(project_id, task_id)))
    except ObjectNotFoundError:
        return ChatThread(task_id=task_id, project_id=project_id)
    except ValueError:
        logger.warning("chat thread for %s is unreadable; starting a new one", task_id)
        return ChatThread(task_id=task_id, project_id=project_id)

    return ChatThread.from_dict(stored)


def save_thread(thread: ChatThread, settings: Settings) -> None:
    """Writes a thread back, for a turn that gained its audio after the fact."""
    body = json.dumps(thread.to_dict(), indent=2).encode("utf-8")
    upload_s3_file(body, settings.s3_projects, thread_key(thread.project_id, thread.task_id))


def append_turn(project_id: str, task_id: str, turn: ChatTurn, settings: Settings) -> ChatThread:
    """Adds one turn and stores the thread. Returns the thread as it now stands."""
    project_id = check_project_id(project_id)

    thread = read_thread(project_id, task_id, settings)
    turn.asked_at = turn.asked_at or datetime.now(UTC).isoformat(timespec="seconds")
    thread.turns.append(turn)

    body = json.dumps(thread.to_dict(), indent=2).encode("utf-8")
    upload_s3_file(body, settings.s3_projects, thread_key(project_id, task_id))

    logger.info("[task %s] chat turn %d stored in project %s", task_id, len(thread.turns), project_id)

    return thread
