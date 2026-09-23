import asyncio

from fastapi import APIRouter, Body, HTTPException, Query, Response

from exceptions.storage import ObjectNotFoundError
from utils.advice_store import read_advice
from utils.compare import VERDICTS, compare_advice, pair_documents
from utils.config import get_setting
from utils.http_errors import http_errors
from utils.legal_responses import advice_body
from utils.logger import get_logger
from utils.projects import create_project, get_project, list_projects, list_reviews, read_review
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


@router.get("/{project_id}/compare")
async def compare(project_id: str, base: str = Query(...), against: str = Query(...)) -> dict:
    """
    What changed between two reviews in this project.

    `base` is the earlier round and `against` the later one, both task ids. The
    answer is computed from the stored advice by matching each risk's quote —
    no model call, so it costs nothing and says the same thing every time.

    Documents are paired by name with the upload suffix removed, or one-to-one
    when each round is a single document. A document in one round that nothing
    in the other answers is reported in `unpaired` rather than being read as a
    contract where every risk was fixed.
    """
    settings = get_setting()

    with http_errors(f"comparing {base} with {against}"):
        try:
            await asyncio.to_thread(get_project, project_id, settings)
        except ObjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"no such project: {project_id}") from exc

        records = {}
        for task_id in (base, against):
            record = await asyncio.to_thread(read_review, project_id, task_id, settings)
            if record is None:
                raise HTTPException(status_code=404, detail=f"no such review in {project_id}: {task_id}")
            records[task_id] = record

        pairs = pair_documents(records[base].pdf_keys, records[against].pdf_keys)
        comparisons, unreadable = [], []

        for base_key, against_key in pairs:
            try:
                before = await asyncio.to_thread(read_advice, base_key, settings, settings.s3_projects)
                after = await asyncio.to_thread(read_advice, against_key, settings, settings.s3_projects)
            except ObjectNotFoundError:
                # one of the two has no advice stored: still running, or it failed
                unreadable.append(against_key)
                continue

            comparisons.append(compare_advice(against_key, before, after, base_key).to_dict())

    paired = {key for pair in pairs for key in pair}

    return {
        "project_id": project_id,
        "base": base,
        "against": against,
        "documents": comparisons,
        "document_count": len(comparisons),
        "unpaired": sorted(key for key in records[base].pdf_keys + records[against].pdf_keys if key not in paired),
        "not_reviewed_yet": unreadable,
        "totals": _totals(comparisons),
    }


def _totals(comparisons: list[dict]) -> dict:
    """The whole comparison in one row, for a header line."""
    totals = {verdict: 0 for verdict in VERDICTS}
    net = 0

    for document in comparisons:
        for verdict, count in document["counts"].items():
            totals[verdict] += count
        net += document["net_severity_change"]

    return {**totals, "net_severity_change": net}


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
