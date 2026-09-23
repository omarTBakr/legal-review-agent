"""
Taking a PDF off a request and putting it in the bucket.

The upload is written to the scratch file in chunks rather than read into a
`bytes` first: `LEGAL_MAX_PDFS` documents at once, each read whole into memory,
is how an API falls over on a big submission. The size is checked as it goes,
so a file past the limit is refused after a megabyte rather than after all of
it has been accepted.
"""

import asyncio
from pathlib import Path
from typing import NamedTuple

from exceptions.storage import StorageError
from exceptions.validation import EmptyFileError, UnsupportedFileTypeError, UploadTooLargeError
from utils.config import Settings
from utils.logger import get_logger
from utils.utility import build_run_artifacts, delete_s3_file, upload_s3_file

logger = get_logger(__name__)

# what a PDF starts with; the extension is a claim, this is evidence
PDF_MAGIC = b"%PDF-"

CHUNK = 1024 * 1024


def _readable(size: int) -> str:
    """A limit as the person who hit it would say it."""
    if size >= 1024 * 1024:
        return f"{size // (1024 * 1024)} MB"
    if size >= 1024:
        return f"{size // 1024} KB"

    return f"{size} bytes"


class StoredUpload(NamedTuple):
    """
    A PDF that is now in the bucket and ready for a workflow to pick up.

    `local_pdf` is where it was staged on the way there, and is empty once
    that copy has been removed — which is the normal case. The worker fetches
    its own copy from the bucket; it does not share a filesystem with the API.
    """

    task_id: str
    pdf_key: str
    md_key: str
    local_pdf: str
    # what it cost the request's allowance, counted as it was written
    size: int = 0


def validate_upload(filename: str | None, pdf: bytes) -> None:
    """
    Rejects anything the pipeline cannot process.

    Raises ValidationError subclasses, which the HTTP layer turns into 400s.
    `pdf` need only be the first few bytes: everything checked here is at the
    front of the file.
    """
    if not (filename or "").lower().endswith(".pdf"):
        raise UnsupportedFileTypeError("only .pdf files are accepted")

    if not pdf:
        raise EmptyFileError("uploaded file is empty")

    if not pdf.startswith(PDF_MAGIC):
        # a .pdf that is not a PDF is either a mistake or someone trying it on;
        # either way pymupdf would fail on it three activities later
        raise UnsupportedFileTypeError(f"{filename} is named .pdf but does not contain a PDF")


async def write_upload(upload, destination: Path, settings: Settings, budget: int | None = None) -> int:
    """
    Streams one upload to `destination`, checking as it goes.

    Returns the bytes written. `budget` is what is left of the request's total
    allowance; the file's own limit is `MAX_UPLOAD_BYTES`. A file that runs past
    either is refused and its partial copy removed, rather than being accepted
    and rejected once it is all in memory.
    """
    limit = settings.max_upload_bytes
    written = 0
    first = b""

    try:
        with destination.open("wb") as handle:
            while chunk := await upload.read(CHUNK):
                if not first:
                    first = chunk[: len(PDF_MAGIC)]
                    validate_upload(upload.filename, chunk)

                written += len(chunk)

                if written > limit:
                    raise UploadTooLargeError(f"{upload.filename} is larger than {_readable(limit)}")
                if budget is not None and written > budget:
                    raise UploadTooLargeError(f"the upload is larger than {_readable(settings.max_request_bytes)} in total")

                await asyncio.to_thread(handle.write, chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    if not written:
        destination.unlink(missing_ok=True)
        raise EmptyFileError(f"{upload.filename} is empty")

    return written


async def store_upload(upload, settings: Settings, prefix: str = "", bucket: str = "", budget: int | None = None) -> StoredUpload:
    """
    Writes the PDF to the local scratch folder and uploads it to the PDF bucket.

    This happens before the workflow starts so the document never travels
    through the workflow history; the workflow is given keys, not bytes.

    `prefix` files the document inside a project's folder, and `bucket` is that
    project's bucket; without them it is the pipeline's own PDF bucket.
    """
    task_id, pdf_key, md_key, local_pdf = build_run_artifacts(upload.filename, settings, prefix)
    bucket = bucket or settings.s3_pdf_bucket

    written = await write_upload(upload, local_pdf, settings, budget)

    # blocking work, kept off the event loop
    await asyncio.to_thread(upload_s3_file, local_pdf, bucket, pdf_key)

    # the staged copy has done its job: it existed so the upload could be
    # streamed to disk rather than held in memory. Keeping it would fill the
    # API's disk with documents the worker reads from the bucket anyway.
    local_pdf.unlink(missing_ok=True)

    logger.info("[task %s] stored %s/%s (%d KB)", task_id, bucket, pdf_key, written // 1024)

    return StoredUpload(task_id=task_id, pdf_key=pdf_key, md_key=md_key, local_pdf="", size=written)


async def store_uploads(uploads: list, settings: Settings, prefix: str = "", bucket: str = "") -> tuple[str, list[str]]:
    """
    Stores several PDFs under one shared task id.

    Every document in a batch belongs to the same review, so they share the id
    that names the workflow, and each gets its own key derived from its
    filename. The request's total allowance is spent across them, so twenty
    files just under the per-file limit cannot add up to a gigabyte.

    A request is all or nothing. What can be checked before storing anything —
    the name and the first bytes — is checked for every file first; a failure
    that can only appear while streaming, like the size, takes the documents
    already stored back out of the bucket. A half-stored review would leave
    documents nobody ever reviews and nobody knows about.

    Returns (task_id, pdf_keys).
    """
    if not uploads:
        raise EmptyFileError("no files were uploaded")

    for upload in uploads:
        validate_upload(upload.filename, await _peek(upload))

    task_id = ""
    pdf_keys: list[str] = []
    stored_keys: list[str] = []
    remaining = settings.max_request_bytes

    try:
        for upload in uploads:
            stored = await store_upload(upload, settings, prefix, bucket, budget=remaining)
            # the first document's id names the whole review
            task_id = task_id or stored.task_id
            pdf_keys.append(stored.pdf_key)
            stored_keys.append(stored.pdf_key)
            remaining -= stored.size
    except Exception:
        await _undo(stored_keys, bucket or settings.s3_pdf_bucket)
        raise

    logger.info("[task %s] stored %d document(s)", task_id, len(pdf_keys))

    return task_id, pdf_keys


async def _peek(upload, size: int = 16) -> bytes:
    """The first bytes of an upload, with the stream put back where it was."""
    first = await upload.read(size)
    await upload.seek(0)

    return first


async def _undo(keys: list[str], bucket: str) -> None:
    """
    Takes back what this request had already stored. Never raises.

    Only the bucket copies: each document's staged file was removed as soon as
    it was uploaded, and there is nothing else of this request on disk.
    """
    for key in keys:
        try:
            await asyncio.to_thread(delete_s3_file, bucket, key)
        except StorageError:
            logger.warning("could not remove %s/%s after a failed upload", bucket, key, exc_info=True)

    if keys:
        logger.info("removed %d document(s) after a failed upload", len(keys))
