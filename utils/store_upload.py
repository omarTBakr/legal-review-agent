import asyncio
from typing import NamedTuple

from exceptions.validation import EmptyFileError, UnsupportedFileTypeError
from utils.config import Settings
from utils.logger import get_logger
from utils.utility import build_run_artifacts, upload_s3_file

logger = get_logger(__name__)


class StoredUpload(NamedTuple):
    """A PDF that is now in the bucket and ready for a workflow to pick up."""

    task_id: str
    pdf_key: str
    md_key: str
    local_pdf: str


def validate_upload(filename: str | None, pdf: bytes) -> None:
    """
    Rejects anything the pipeline cannot process.

    Raises ValidationError subclasses, which the HTTP layer turns into 400s.
    """
    if not (filename or "").lower().endswith(".pdf"):
        raise UnsupportedFileTypeError("only .pdf files are accepted")

    if not pdf:
        raise EmptyFileError("uploaded file is empty")


async def store_upload(pdf: bytes, filename: str, settings: Settings, prefix: str = "", bucket: str = "") -> StoredUpload:
    """
    Writes the PDF to the local scratch folder and uploads it to the PDF bucket.

    This happens before the workflow starts so the document never travels
    through the workflow history; the workflow is given keys, not bytes.

    `prefix` files the document inside a project's folder, and `bucket` is that
    project's bucket; without them it is the pipeline's own PDF bucket.
    """
    task_id, pdf_key, md_key, local_pdf = build_run_artifacts(filename, settings, prefix)
    bucket = bucket or settings.s3_pdf_bucket

    # blocking work, kept off the event loop
    await asyncio.to_thread(local_pdf.write_bytes, pdf)
    await asyncio.to_thread(upload_s3_file, local_pdf, bucket, pdf_key)

    logger.info("[task %s] stored %s/%s", task_id, bucket, pdf_key)

    return StoredUpload(task_id=task_id, pdf_key=pdf_key, md_key=md_key, local_pdf=str(local_pdf))


async def store_uploads(
    files: list[tuple[str, bytes]], settings: Settings, prefix: str = "", bucket: str = ""
) -> tuple[str, list[str]]:
    """
    Stores several PDFs under one shared task id.

    Every document in a batch belongs to the same review, so they share the id
    that names the workflow, and each gets its own key derived from its
    filename. `prefix` files them inside a project. Returns (task_id, pdf_keys).
    """
    if not files:
        raise EmptyFileError("no files were uploaded")

    task_id = ""
    pdf_keys = []

    for filename, pdf in files:
        stored = await store_upload(pdf, filename, settings, prefix, bucket)
        # the first document's id names the whole review
        task_id = task_id or stored.task_id
        pdf_keys.append(stored.pdf_key)

    logger.info("[task %s] stored %d document(s)", task_id, len(pdf_keys))

    return task_id, pdf_keys
