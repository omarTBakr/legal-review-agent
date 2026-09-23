"""
Projects, stored as folders in the PDF bucket.

A project is a folder in `S3_PROJECTS` — `<project_id>/` — holding a
`project.json` manifest, the documents uploaded into it, their parsed text and
advice, and one record per review under `reviews/`. Everything a project owns
is in one place, so one lifecycle rule covers it and removing a client is one
prefix to delete.

Keeping each review in its own object means two submissions at the same time
cannot overwrite each other's record, which a single manifest listing every
review could not promise.

The bucket, not Temporal, is what remembers a project: a task id looked up here
still works after Temporal has dropped the workflow's history.

Everything here is blocking boto3 work. Callers on the event loop wrap these in
`asyncio.to_thread`, as the upload path already does.
"""

import json
import re
import unicodedata
import uuid
from datetime import UTC, datetime

from exceptions.storage import ObjectNotFoundError
from exceptions.validation import ValidationError
from schemas.project import Project, ProjectReview
from utils.config import Settings
from utils.logger import get_logger
from utils.utility import download_s3_bytes, list_s3_keys, upload_s3_file

logger = get_logger(__name__)

# the bucket is the projects/ folder now, so a key starts with the project id
PROJECTS_ROOT = ""
MANIFEST_NAME = "project.json"
REVIEWS_FOLDER = "reviews/"

MAX_NAME_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 2000
MAX_SLUG_LENGTH = 40

# what a project id may look like; it becomes part of an S3 key, so anything
# that could climb out of the prefix is rejected rather than escaped
PROJECT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def slugify(name: str) -> str:
    """
    Turns a project name into something safe to use as a folder name.

    Accents are folded rather than dropped, so "Café" stays "cafe" instead of
    becoming an empty slug.
    """
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")

    return slug[:MAX_SLUG_LENGTH].strip("-") or "project"


def validate_email(email: str) -> str:
    """Rejects an address the report could never be delivered to."""
    email = email.strip()

    if email and not EMAIL_PATTERN.match(email):
        raise ValidationError(f"{email!r} is not an email address")

    return email


def manifest_key(project_id: str) -> str:
    return f"{PROJECTS_ROOT}{project_id}/{MANIFEST_NAME}"


def review_key(project_id: str, task_id: str) -> str:
    return f"{PROJECTS_ROOT}{project_id}/{REVIEWS_FOLDER}{task_id}.json"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def check_project_id(project_id: str) -> str:
    """A project id from the outside world, before it is pasted into a key."""
    project_id = (project_id or "").strip()

    if not PROJECT_ID_PATTERN.match(project_id):
        raise ValidationError(f"{project_id!r} is not a project id")

    return project_id


def create_project(name: str, settings: Settings, description: str = "", email: str = "") -> Project:
    """
    Writes a new project's manifest, which is what creates its folder.

    S3 has no directories: the prefix exists because objects use it. The
    manifest is that first object.
    """
    name = (name or "").strip()

    if not name:
        raise ValidationError("a project needs a name")
    if len(name) > MAX_NAME_LENGTH:
        raise ValidationError(f"a project name is at most {MAX_NAME_LENGTH} characters")

    description = (description or "").strip()
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise ValidationError(f"a project description is at most {MAX_DESCRIPTION_LENGTH} characters")

    project = Project(
        id=f"{slugify(name)}-{uuid.uuid4().hex[:8]}",
        name=name,
        description=description,
        email=validate_email(email),
        created_at=_now(),
    )

    body = json.dumps(project.to_dict(), indent=2).encode("utf-8")
    upload_s3_file(body, settings.s3_projects, manifest_key(project.id))

    logger.info("created project %s (%r)", project.id, project.name)

    return project


def get_project(project_id: str, settings: Settings) -> Project:
    """Reads one project's manifest. Raises ObjectNotFoundError if there is none."""
    project_id = check_project_id(project_id)
    body = download_s3_bytes(settings.s3_projects, manifest_key(project_id))

    return Project.from_dict(json.loads(body))


def list_projects(settings: Settings) -> list[Project]:
    """
    Every project, newest first.

    One request to list the manifests and one per project to read it. That is
    fine for the tens of projects a firm has; if it ever becomes thousands,
    this is the function to put an index in front of.
    """
    keys = [key for key in list_s3_keys(settings.s3_projects, PROJECTS_ROOT) if key.endswith(f"/{MANIFEST_NAME}")]

    projects = []
    for key in keys:
        try:
            projects.append(Project.from_dict(json.loads(download_s3_bytes(settings.s3_projects, key))))
        except (ObjectNotFoundError, ValueError):
            # a half-written or hand-edited manifest should not hide the rest
            logger.warning("skipping unreadable project manifest %s", key)

    return sorted(projects, key=lambda project: project.created_at, reverse=True)


def record_review(project_id: str, task_id: str, workflow_id: str, pdf_keys: list[str], settings: Settings) -> ProjectReview:
    """Remembers that a review was submitted into this project."""
    project_id = check_project_id(project_id)

    review = ProjectReview(task_id=task_id, workflow_id=workflow_id, pdf_keys=list(pdf_keys), submitted_at=_now())
    body = json.dumps(review.to_dict(), indent=2).encode("utf-8")

    upload_s3_file(body, settings.s3_projects, review_key(project_id, task_id))

    logger.info("[task %s] recorded review in project %s", task_id, project_id)

    return review


def read_review(project_id: str, task_id: str, settings: Settings) -> ProjectReview | None:
    """
    One review record, fetched by key.

    `list_reviews` reads every record in the project to find one, which costs a
    request per review and grows with the project. A chat turn does this on
    every question, so it is worth going straight to the object.
    """
    project_id = check_project_id(project_id)

    try:
        stored = json.loads(download_s3_bytes(settings.s3_projects, review_key(project_id, task_id)))
    except ObjectNotFoundError:
        return None
    except ValueError:
        logger.warning("review record for %s is unreadable", task_id)
        return None

    return ProjectReview.from_dict(stored)


def list_reviews(project_id: str, settings: Settings) -> list[ProjectReview]:
    """Every review submitted into a project, newest first."""
    project_id = check_project_id(project_id)
    prefix = f"{PROJECTS_ROOT}{project_id}/{REVIEWS_FOLDER}"

    reviews = []
    for key in list_s3_keys(settings.s3_projects, prefix):
        try:
            reviews.append(ProjectReview.from_dict(json.loads(download_s3_bytes(settings.s3_projects, key))))
        except (ObjectNotFoundError, ValueError):
            logger.warning("skipping unreadable review record %s", key)

    return sorted(reviews, key=lambda review: review.submitted_at, reverse=True)
