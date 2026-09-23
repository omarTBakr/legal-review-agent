import asyncio

from fastapi import APIRouter, Body, File, Form, HTTPException, Response, UploadFile
from temporalio.service import RPCError, RPCStatusCode

from enums.TaskStatus import TaskStatus
from exceptions.storage import ObjectNotFoundError
from exceptions.validation import TooManyFilesError
from schemas.legal_review import LegalReviewInput
from schemas.project import Project
from utils.config import get_setting
from utils.http_errors import http_errors
from utils.legal_responses import (
    accepted_response,
    answer_accepted_response,
    completed_response,
    running_response,
    status_response,
)
from utils.logger import get_logger
from utils.projects import get_project, record_review, validate_email
from utils.store_upload import store_uploads
from utils.temporal_client import get_temporal_client
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
) -> dict:
    """
    Accepts several PDFs, stores them and starts the legal review workflow.

    Returns 202 and a task id straight away; poll GET /legal/{task_id}, which
    reports `awaiting_human` when the model has a question for you.

    With `project_id`, the documents are filed inside that project's folder and
    the review is recorded against it. `email` overrides the project's own
    address for this review; either way, an address means the finished report
    is emailed there.
    """
    settings = get_setting()

    with http_errors(f"{len(files)} document(s)"):
        if len(files) > settings.legal_max_pdfs:
            raise TooManyFilesError(f"at most {settings.legal_max_pdfs} documents per request, got {len(files)}")

        project = await _load_project(project_id, settings) if project_id else None
        report_email = validate_email(email) or (project.email if project else "")

        bucket = settings.s3_projects if project else ""
        # the uploads are streamed to disk one at a time, not read into memory:
        # twenty documents at once is how an API falls over on a big submission
        task_id, pdf_keys = await store_uploads(files, settings, project.prefix if project else "", bucket)

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
            await asyncio.to_thread(record_review, project.id, task_id, handle.id, pdf_keys, settings)

    response.status_code = ACCEPTED
    return accepted_response(task_id, pdf_keys, bucket or settings.s3_pdf_bucket, project.id if project else "")


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
