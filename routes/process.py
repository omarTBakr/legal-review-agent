import asyncio

from fastapi import APIRouter, File, Header, HTTPException, Response, UploadFile
from temporalio.service import RPCError, RPCStatusCode

from enums.TaskStatus import TaskStatus
from schemas.process_pdf import ProcessPdfInput
from utils.config import get_setting
from utils.http_errors import http_errors
from utils.idempotency import IdempotencyConflict, fingerprint_uploads, get_store
from utils.idempotency_guard import released_on_failure
from utils.logger import get_logger
from utils.responses import accepted_response, completed_response, status_response
from utils.store_upload import store_upload
from utils.temporal_client import get_temporal_client
from utils.workflow_ids import workflow_id_for
from workflows.workflow_process_pdf import ProcessPdfWorkflow

router = APIRouter()

logger = get_logger(__name__)

ACCEPTED = 202


@router.post("/process")
async def process(
    response: Response,
    file: UploadFile = File(...),
    wait: bool = False,
    idempotency_key: str = Header("", alias="Idempotency-Key"),
) -> dict:
    """
    Accepts a PDF upload, stores it and starts the Temporal workflow.

    Returns 202 and a task id straight away; poll GET /process/{task_id} for
    the outcome. Pass `?wait=true` to hold the request open until the pipeline
    finishes and get the full result in one call instead.
    """
    settings = get_setting()

    with http_errors(file.filename or "<no filename>"):
        claim_store = get_store(settings.idempotency_path) if idempotency_key else None
        claim = None
        if claim_store:
            fingerprint = await fingerprint_uploads([file], {"wait": str(wait)})
            try:
                claim = await asyncio.to_thread(claim_store.claim, idempotency_key, fingerprint)
            except IdempotencyConflict as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if not claim.is_new:
                response.status_code = ACCEPTED
                return status_response(claim.task_id, TaskStatus.PROCESSING)

        # released if anything below fails, or the client's retry is answered
        # with a task id whose workflow was never started
        async with released_on_failure(claim_store, idempotency_key):
            stored = await store_upload(file, settings, task_id=claim.task_id if claim else None)

            client = await get_temporal_client()
            handle = await client.start_workflow(
                ProcessPdfWorkflow.run,
                ProcessPdfInput(task_id=stored.task_id, pdf_key=stored.pdf_key, md_key=stored.md_key),
                id=workflow_id_for(stored.task_id),
                task_queue=settings.temporal_task_queue,
            )

            if claim_store:
                # after the workflow starts: a completed claim promises there is
                # something to poll
                await asyncio.to_thread(claim_store.complete, idempotency_key, [stored.pdf_key], settings.s3_pdf_bucket, "")

        logger.info("[task %s] started %s", stored.task_id, handle.id)

        if wait:
            return completed_response(await handle.result())

    response.status_code = ACCEPTED
    return accepted_response(stored, settings.s3_pdf_bucket)


@router.get("/process/{task_id}")
async def process_status(task_id: str) -> dict:
    """
    Reports where a task got to.

    Temporal holds the state, so nothing is stored here.
    """
    with http_errors(f"task {task_id}"):
        client = await get_temporal_client()

        # the typed handle knows the workflow's return type, so result() decodes
        # into a ProcessPdfResult instead of a plain dict
        handle = client.get_workflow_handle_for(ProcessPdfWorkflow.run, workflow_id_for(task_id))

        try:
            description = await handle.describe()
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                raise HTTPException(status_code=404, detail=f"no such task: {task_id}") from exc
            raise

        status = TaskStatus.from_temporal(description.status)

        if status is TaskStatus.COMPLETED:
            return completed_response(await handle.result())

        return status_response(task_id, status)


@router.post("/process/{task_id}/cancel")
async def process_cancel(task_id: str) -> dict:
    """Requests graceful cancellation of a running PDF processing workflow."""
    with http_errors(f"canceling task {task_id}"):
        client = await get_temporal_client()
        handle = client.get_workflow_handle_for(ProcessPdfWorkflow.run, workflow_id_for(task_id))

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
