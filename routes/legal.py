import asyncio
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, Header, HTTPException, Query, Response, UploadFile
from temporalio.service import RPCError, RPCStatusCode

from enums.TaskStatus import TaskStatus
from exceptions.storage import ObjectNotFoundError
from exceptions.validation import TooManyFilesError
from schemas.legal_review import LegalReviewInput
from schemas.project import Project
from utils.advice_store import read_advice
from utils.annotate import annotate
from utils.config import get_setting
from utils.http_errors import http_errors
from utils.idempotency import IdempotencyConflict, fingerprint_uploads, get_store
from utils.idempotency_guard import released_on_failure
from utils.legal_responses import (
    accepted_response,
    answer_accepted_response,
    completed_response,
    running_response,
    status_response,
)
from utils.logger import get_logger
from utils.projects import check_project_id, get_project, record_review, validate_email
from utils.store_upload import store_uploads
from utils.temporal_client import get_temporal_client
from utils.utility import download_s3_bytes
from utils.workflow_ids import legal_workflow_id_for
from workflows.workflow_legal_review import LegalReviewWorkflow

router = APIRouter(prefix="/legal", tags=["legal"])

logger = get_logger(__name__)

ACCEPTED = 202


@router.post("")
async def submit(
    response: Response,
    files: list[UploadFile] = File(...),
    project_id: str = Form(""),
    email: str = Form(""),
    supersedes: str = Form(""),
    idempotency_key: str = Header("", alias="Idempotency-Key"),
) -> dict:
    """
    Accepts several PDFs, stores them and starts the legal review workflow.

    Returns 202 and a task id straight away; poll GET /legal/{task_id}, which
    reports `awaiting_human` when the model has a question for you.

    With `project_id`, the documents are filed inside that project's folder and
    the review is recorded against it. `email` overrides the project's own
    address for this review; either way, an address means the finished report
    is emailed there.

    `supersedes` is the task id of an earlier review in the same project that
    this one is a new round of, which is what
    `GET /projects/{id}/compare` follows to say what the counterparty fixed.
    """
    settings = get_setting()

    with http_errors(f"{len(files)} document(s)"):
        if len(files) > settings.legal_max_pdfs:
            raise TooManyFilesError(f"at most {settings.legal_max_pdfs} documents per request, got {len(files)}")

        project = await _load_project(project_id, settings) if project_id else None
        report_email = validate_email(email) or (project.email if project else "")

        claim_store = get_store(settings.idempotency_path) if idempotency_key else None
        claim = None
        if claim_store:
            fingerprint = await fingerprint_uploads(
                files,
                {"project_id": project_id, "email": report_email, "supersedes": supersedes},
            )
            try:
                claim = await asyncio.to_thread(claim_store.claim, idempotency_key, fingerprint)
            except IdempotencyConflict as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if not claim.is_new:
                response.status_code = ACCEPTED
                return accepted_response(claim.task_id, claim.pdf_keys, claim.pdf_bucket, claim.project_id)

        # everything from here is undone if it fails: a claim left behind would
        # answer the client's retry with a task id whose workflow never started,
        # and they would poll it for ever
        async with released_on_failure(claim_store, idempotency_key):
            bucket = settings.s3_projects if project else ""
            # the uploads are streamed to disk one at a time, not read into memory:
            # twenty documents at once is how an API falls over on a big submission
            task_id, pdf_keys = await store_uploads(
                files, settings, project.prefix if project else "", bucket, task_id=claim.task_id if claim else None
            )

            client = await get_temporal_client()
            handle = await client.start_workflow(
                LegalReviewWorkflow.run,
                LegalReviewInput(
                    task_id=task_id,
                    pdf_keys=pdf_keys,
                    pages_per_batch=settings.legal_pages_per_batch,
                    max_concurrent_pdfs=settings.legal_max_concurrent_pdfs,
                    human_input_timeout_seconds=settings.human_input_timeout_seconds,
                    report_email=report_email,
                    project_id=project.id if project else "",
                    project_name=project.name if project else "",
                    bucket=bucket,
                ),
                id=legal_workflow_id_for(task_id),
                task_queue=settings.legal_task_queue,
            )

            logger.info("[task %s] started %s for %d document(s)", task_id, handle.id, len(pdf_keys))

            if project:
                # recorded after the workflow starts, so a record never points at a
                # review that was never begun
                await asyncio.to_thread(record_review, project.id, task_id, handle.id, pdf_keys, settings, supersedes)

            if claim_store:
                # after the workflow starts, not before: a completed claim is a
                # promise that there is something to poll
                await asyncio.to_thread(
                    claim_store.complete,
                    idempotency_key,
                    pdf_keys,
                    bucket or settings.s3_pdf_bucket,
                    project.id if project else "",
                )

    response.status_code = ACCEPTED
    return accepted_response(task_id, pdf_keys, bucket or settings.s3_pdf_bucket, project.id if project else "")


@router.get("/{task_id}/annotated")
async def annotated(task_id: str, pdf_key: str = Query(...), project_id: str = Query("")) -> Response:
    """
    The original PDF with every risk highlighted where it was found.

    `project_id` says which buckets to read: a project's own, or the pipeline's
    default ones. The risks whose quotes could not be located in the page text
    are listed on an appendix page rather than left out.
    """
    settings = get_setting()

    with http_errors(f"annotating {pdf_key}"):
        if project_id:
            project_id = check_project_id(project_id)
            # a key is pasted straight into a bucket read, so it has to be inside
            # the project it claims to be in — otherwise any project's documents
            # are one crafted query string away
            if not pdf_key.startswith(f"{project_id}/"):
                raise HTTPException(status_code=400, detail=f"{pdf_key} is not in project {project_id}")

        bucket = settings.s3_projects if project_id else settings.s3_pdf_bucket
        advice_bucket = settings.s3_projects if project_id else settings.s3_legal_advice

        try:
            pdf = await asyncio.to_thread(download_s3_bytes, bucket, pdf_key)
            advice = await asyncio.to_thread(read_advice, pdf_key, settings, advice_bucket)
        except ObjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"no finished review for {pdf_key}") from exc

        marked, report = await asyncio.to_thread(annotate, pdf, advice.key_risks, Path(pdf_key).name)

    logger.info("[task %s] annotated %s: %s", task_id, pdf_key, report)

    return Response(
        content=marked,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{Path(pdf_key).stem}-reviewed.pdf"',
            "X-Risks-Highlighted": str(report["highlighted"]),
            "X-Risks-Listed-Only": str(report["listed_only"]),
            "Cache-Control": "no-store",
        },
    )


async def _load_project(project_id: str, settings) -> Project:
    """The project a review is being filed into, or a 404."""
    try:
        return await asyncio.to_thread(get_project, project_id, settings)
    except ObjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"no such project: {project_id}") from exc


@router.get("/{task_id}")
async def review_status(task_id: str) -> dict:
    """
    Reports where a review got to, and any question waiting on a human.

    Temporal holds the state, so nothing is stored here.
    """
    with http_errors(f"task {task_id}"):
        client = await get_temporal_client()

        # the typed handle knows the workflow's return type, so result() decodes
        # into a LegalReviewResult instead of a plain dict
        handle = client.get_workflow_handle_for(LegalReviewWorkflow.run, legal_workflow_id_for(task_id))

        try:
            description = await handle.describe()
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                raise HTTPException(status_code=404, detail=f"no such task: {task_id}") from exc
            raise

        status = TaskStatus.from_temporal(description.status)

        if status is TaskStatus.COMPLETED:
            return completed_response(await handle.result())

        if status is TaskStatus.PROCESSING:
            # queries reach the running workflow, so the questions are live
            questions = await handle.query(LegalReviewWorkflow.pending_questions)
            progress = await handle.query(LegalReviewWorkflow.progress)
            finished = await handle.query(LegalReviewWorkflow.finished_documents)
            return running_response(task_id, progress, questions, finished)

        return status_response(task_id, status)


@router.post("/{task_id}/respond")
async def respond(task_id: str, pdf_key: str = Body(..., embed=True), answer: str = Body(..., embed=True)) -> dict:
    """
    Answers the question the model raised about one document.

    The workflow is waiting on this signal; sending it releases that document
    and the advice is revised in light of the answer.
    """
    with http_errors(f"task {task_id}"):
        client = await get_temporal_client()
        handle = client.get_workflow_handle_for(LegalReviewWorkflow.run, legal_workflow_id_for(task_id))

        try:
            await handle.signal(LegalReviewWorkflow.human_response, args=[pdf_key, answer])
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                raise HTTPException(status_code=404, detail=f"no such task: {task_id}") from exc
            raise

        logger.info("[task %s] answer delivered for %s", task_id, pdf_key)

    return answer_accepted_response(task_id, pdf_key)


@router.post("/{task_id}/cancel")
async def cancel(task_id: str) -> dict:
    """Requests graceful cancellation of a running legal review."""
    with http_errors(f"canceling task {task_id}"):
        client = await get_temporal_client()
        handle = client.get_workflow_handle_for(LegalReviewWorkflow.run, legal_workflow_id_for(task_id))

        try:
            description = await handle.describe()
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                raise HTTPException(status_code=404, detail=f"no such task: {task_id}") from exc
            raise

        status = TaskStatus.from_temporal(description.status)
        if status is TaskStatus.COMPLETED:
            raise HTTPException(status_code=409, detail=f"task {task_id} is already completed")
        if status in (TaskStatus.CANCELED, TaskStatus.TERMINATED, TaskStatus.FAILED, TaskStatus.TIMED_OUT):
            return status_response(task_id, status)

        await handle.cancel()

    return status_response(task_id, TaskStatus.CANCELED)
