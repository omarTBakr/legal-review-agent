from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from temporalio.service import RPCError, RPCStatusCode

from enums.TaskStatus import TaskStatus
from schemas.process_pdf import ProcessPdfInput
from utils.config import get_setting
from utils.http_errors import http_errors
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
async def process(response: Response, file: UploadFile = File(...), wait: bool = False) -> dict:
    """
    Accepts a PDF upload, stores it and starts the Temporal workflow.

    Returns 202 and a task id straight away; poll GET /process/{task_id} for
    the outcome. Pass `?wait=true` to hold the request open until the pipeline
    finishes and get the full result in one call instead.
    """
    settings = get_setting()

    with http_errors(file.filename or "<no filename>"):
        stored = await store_upload(file, settings)

        client = await get_temporal_client()
        handle = await client.start_workflow(
            ProcessPdfWorkflow.run,
            ProcessPdfInput(task_id=stored.task_id, pdf_key=stored.pdf_key, md_key=stored.md_key),
            id=workflow_id_for(stored.task_id),
            task_queue=settings.temporal_task_queue,
        )

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
