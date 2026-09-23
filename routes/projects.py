import asyncio

from fastapi import APIRouter, Body, HTTPException, Response

from exceptions.storage import ObjectNotFoundError
from utils.advice_store import read_advice
from utils.config import get_setting
from utils.http_errors import http_errors
from utils.legal_responses import advice_body
from utils.logger import get_logger
from utils.projects import create_project, get_project, list_projects, list_reviews
from utils.workflow_ids import legal_workflow_id_for

router = APIRouter(prefix="/projects", tags=["projects"])

logger = get_logger(__name__)

CREATED = 201


@router.post("")
async def create(
    response: Response,
    name: str = Body(..., embed=True),
    description: str = Body("", embed=True),
    email: str = Body("", embed=True),
) -> dict:
    """
    Creates a project: a folder in the bucket that reviews are filed under.

    `description` and `email` are optional. When an email is given, the report
    for every review in this project is sent there as it finishes.
    """
    settings = get_setting()

    with http_errors(f"project {name!r}"):
        # boto3 blocks; keep the event loop free
        project = await asyncio.to_thread(create_project, name, settings, description, email)

    response.status_code = CREATED
    return project.to_dict()


@router.get("")
async def index() -> dict:
    """Every project, newest first."""
    settings = get_setting()

    with http_errors("projects"):
        projects = await asyncio.to_thread(list_projects, settings)

    return {"projects": [project.to_dict() for project in projects], "project_count": len(projects)}


@router.get("/{project_id}")
async def detail(project_id: str) -> dict:
    """One project and the reviews submitted into it."""
    settings = get_setting()

    with http_errors(f"project {project_id}"):
        try:
            project = await asyncio.to_thread(get_project, project_id, settings)
        except ObjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"no such project: {project_id}") from exc

        reviews = await asyncio.to_thread(list_reviews, project_id, settings)

    return {**project.to_dict(), "reviews": [review.to_dict() for review in reviews], "review_count": len(reviews)}


@router.get("/{project_id}/reviews/{task_id}")
async def review(project_id: str, task_id: str) -> dict:
    """
    A review read back from the bucket rather than from Temporal.

    `GET /legal/{task_id}` is the live view, and the one to use while a review
    is running. This one answers from the stored advice, so it still works
    after Temporal has dropped the workflow's history — and it reports which
    documents have no advice stored yet.
    """
    settings = get_setting()

    with http_errors(f"task {task_id}"):
        try:
            record = await asyncio.to_thread(_find_review, project_id, task_id, settings)
        except ObjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"no such project: {project_id}") from exc

        if record is None:
            raise HTTPException(status_code=404, detail=f"no such review in {project_id}: {task_id}")

        documents, pending = [], []

        for pdf_key in record.pdf_keys:
            try:
                advice = await asyncio.to_thread(read_advice, pdf_key, settings, settings.s3_projects)
            except ObjectNotFoundError:
                # the document is still being reviewed, or its review failed
                pending.append(pdf_key)
                continue

            documents.append(advice_body(pdf_key, advice))

    return {
        "task_id": task_id,
        "project_id": project_id,
        "workflow_id": legal_workflow_id_for(task_id),
        "submitted_at": record.submitted_at,
        "documents": documents,
        "document_count": len(documents),
        "pending": pending,
    }


def _find_review(project_id: str, task_id: str, settings):
    """The review record for a task id, or None. Runs in a thread."""
    get_project(project_id, settings)

    return next((review for review in list_reviews(project_id, settings) if review.task_id == task_id), None)
