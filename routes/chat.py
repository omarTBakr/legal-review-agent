"""
Asking questions about a finished review.

Not a Temporal workflow: a chat turn is interactive, and its history has no
business in a workflow history. It reads the advice and the document text back
from the bucket, which is also why chat is offered on project reviews only —
a review outside a project has no durable home to read from.
"""

import asyncio
import json

from fastapi import APIRouter, Body, HTTPException, Response
from fastapi.responses import StreamingResponse

from enums.PromptName import PromptName
from exceptions.storage import ObjectNotFoundError
from exceptions.validation import ValidationError
from interfaces import get_llm
from prompts import get_prompt
from schemas.chat import ChatTurn
from utils.audio_store import ANSWER, QUESTION, read_audio
from utils.chat_store import append_turn, read_thread
from utils.config import get_setting
from utils.http_errors import http_errors
from utils.logger import get_logger
from utils.projects import get_project, read_review
from utils.retrieval import render_pages, select_pages
from utils.review_context import load_context

router = APIRouter(prefix="/projects", tags=["chat"])

logger = get_logger(__name__)

MAX_QUESTION_LENGTH = 2000


@router.get("/{project_id}/reviews/{task_id}/chat")
async def thread(project_id: str, task_id: str) -> dict:
    """Everything asked about this review so far."""
    settings = get_setting()

    with http_errors(f"task {task_id}"):
        await _load_review(project_id, task_id, settings)
        stored = await asyncio.to_thread(read_thread, project_id, task_id, settings)

    return stored.to_dict()


@router.post("/{project_id}/reviews/{task_id}/chat")
async def ask(
    project_id: str,
    task_id: str,
    question: str = Body(..., embed=True),
    spoken: bool = Body(False, embed=True),
    question_audio: str = Body("", embed=True),
) -> dict:
    """
    Answers one question about a review and appends it to the thread.

    The answer is grounded in the advice plus the pages of the documents that
    match the question; the model is told to say when they do not settle it.
    """
    settings = get_setting()

    with http_errors(f"task {task_id}"):
        question = _checked(question)
        review = await _load_review(project_id, task_id, settings)
        context = await load_context(project_id, task_id, review.pdf_keys, settings)
        advice, documents = context.advice, context.documents

        if not advice:
            raise HTTPException(status_code=409, detail=f"no advice is stored for {task_id} yet; the review may still be running")

        pages = select_pages(documents, question, settings.chat_context_characters)
        history = await asyncio.to_thread(read_thread, project_id, task_id, settings)

        answer = await get_llm().complete(
            get_prompt(PromptName.REVIEW_CHAT),
            document_count=len(advice),
            advice="\n\n".join(advice),
            pages=render_pages(pages),
            history=history.render(),
            question=question,
        )

        turn = ChatTurn(
            question=question,
            answer=answer.strip(),
            citations=[f"{page.pdf_key} p. {page.number}" for page in pages],
            spoken=spoken,
            question_audio=question_audio,
        )
        stored = await asyncio.to_thread(append_turn, project_id, task_id, turn, settings)

    logger.info("[task %s] answered a question in project %s", task_id, project_id)

    return {"task_id": task_id, "project_id": project_id, "turn": turn.to_dict(), "turn_count": len(stored.turns)}


@router.post("/{project_id}/reviews/{task_id}/chat/stream")
async def ask_streaming(
    project_id: str,
    task_id: str,
    question: str = Body(..., embed=True),
    spoken: bool = Body(False, embed=True),
    question_audio: str = Body("", embed=True),
) -> StreamingResponse:
    """
    The same answer as POST .../chat, sent as it is written.

    Most of the wait for an answer is the model writing it, so the words
    arriving as they come is the difference between an answer that feels
    immediate and one that feels broken.

    Server-sent events: `delta` carries text, `turn` closes with the stored
    turn — citations, timestamp, its index in the thread — and `error` carries
    a failure that happened after the stream had already started, when the
    status code has long since been sent.
    """
    settings = get_setting()

    # everything that can fail cleanly is done before the response starts, so
    # a 404 or a 409 is still a status code rather than an error event
    with http_errors(f"task {task_id}"):
        question = _checked(question)
        review = await _load_review(project_id, task_id, settings)
        context = await load_context(project_id, task_id, review.pdf_keys, settings)
        advice, documents = context.advice, context.documents

        if not advice:
            raise HTTPException(status_code=409, detail=f"no advice is stored for {task_id} yet; the review may still be running")

        pages = select_pages(documents, question, settings.chat_context_characters)
        history = await asyncio.to_thread(read_thread, project_id, task_id, settings)

    async def events():
        answer = []

        try:
            async for chunk in get_llm().stream(
                get_prompt(PromptName.REVIEW_CHAT),
                document_count=len(advice),
                advice="\n\n".join(advice),
                pages=render_pages(pages),
                history=history.render(),
                question=question,
            ):
                answer.append(chunk)
                yield _event("delta", {"text": chunk})
        except Exception as exc:
            logger.exception("[task %s] the answer failed midway", task_id)
            yield _event("error", {"detail": f"the answer stopped: {exc}"})
            return

        turn = ChatTurn(
            question=question,
            answer="".join(answer).strip(),
            citations=[f"{page.pdf_key} p. {page.number}" for page in pages],
            spoken=spoken,
            question_audio=question_audio,
        )
        stored = await asyncio.to_thread(append_turn, project_id, task_id, turn, settings)

        logger.info("[task %s] streamed an answer in project %s", task_id, project_id)

        yield _event("turn", {"turn": turn.to_dict(), "turn_count": len(stored.turns)})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        # a proxy that buffers this would undo the point of streaming it
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _event(name: str, payload: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n"


@router.get("/{project_id}/reviews/{task_id}/audio/{turn}/{kind}")
async def audio(project_id: str, task_id: str, turn: int, kind: str) -> Response:
    """
    Plays back a recording that was kept: a question as it was asked, or an
    answer as it was read out. Saves synthesising the same answer twice.
    """
    settings = get_setting()

    if kind not in (QUESTION, ANSWER):
        raise HTTPException(status_code=404, detail=f"audio is either a {QUESTION} or an {ANSWER}")

    with http_errors(f"task {task_id}"):
        try:
            wav = await asyncio.to_thread(read_audio, project_id, task_id, turn, kind, settings)
        except ObjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"no {kind} audio stored for turn {turn}") from exc

    return Response(content=wav, media_type="audio/wav")


def _checked(question: str) -> str:
    """A question worth sending to the model."""
    question = (question or "").strip()

    if not question:
        raise ValidationError("a question cannot be empty")
    if len(question) > MAX_QUESTION_LENGTH:
        raise ValidationError(f"a question is at most {MAX_QUESTION_LENGTH} characters")

    return question


async def _load_review(project_id: str, task_id: str, settings):
    """
    The review record, or a 404.

    Fetched by key rather than by listing the project's reviews and searching
    them: this runs on every question, and listing costs a request per review.
    """
    review = await asyncio.to_thread(read_review, project_id, task_id, settings)

    if review is not None:
        return review

    # no record: either the project does not exist, or the review does not
    try:
        await asyncio.to_thread(get_project, project_id, settings)
    except ObjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"no such project: {project_id}") from exc

    raise HTTPException(status_code=404, detail=f"no such review in {project_id}: {task_id}")
