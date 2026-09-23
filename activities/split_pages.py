import asyncio

from temporalio import activity

from exceptions.storage import StorageError
from parsers.pymupdf_parser import parse_pdf_pages
from schemas.split_pages import SplitPagesInput, SplitPagesOutput
from utils.advice_store import markdown_key
from utils.batching import split_pages_into_batches
from utils.config import get_setting
from utils.utility import upload_s3_file


@activity.defn
async def split_pages(payload: SplitPagesInput) -> SplitPagesOutput:
    """
    Parses a local PDF page by page and groups the pages into batches.

    Parsing is CPU-bound and runs in a thread: on the event loop it would stall
    the model calls of every other document in flight while it ran.
    """
    activity.logger.info("[task %s] splitting %s into batches of %d", payload.task_id, payload.pdf_key, payload.pages_per_batch)

    try:
        pages = await asyncio.to_thread(parse_pdf_pages, payload.local_pdf)
    except Exception:
        activity.logger.exception("[task %s] failed to split %s", payload.task_id, payload.pdf_key)
        raise

    batches = split_pages_into_batches(pages, payload.pages_per_batch)
    md_key = await _store_markdown(payload, batches)

    activity.logger.info("[task %s] %s: %d pages -> %d batches", payload.task_id, payload.pdf_key, len(pages), len(batches))

    return SplitPagesOutput(batches=batches, page_count=len(pages), md_key=md_key)


async def _store_markdown(payload: SplitPagesInput, batches) -> str:
    """
    Keeps the parsed text, so questions can be answered from the document
    itself once the review is over.

    Stored here rather than by a second activity because the text is already on
    this worker: handing it back to the workflow only to hand it on again would
    put another copy of the document in the workflow history.
    """
    settings = get_setting()
    bucket = payload.md_bucket or settings.s3_parsed_mds
    key = markdown_key(payload.pdf_key)
    body = "\n\n".join(batch.markdown for batch in batches).encode("utf-8")

    try:
        await asyncio.to_thread(upload_s3_file, body, bucket, key)
    except StorageError:
        # the review is the point; losing the text only costs chat its quotes
        activity.logger.warning("[task %s] could not store the text of %s", payload.task_id, payload.pdf_key, exc_info=True)
        return ""

    activity.logger.info("[task %s] stored the text of %s -> %s/%s", payload.task_id, payload.pdf_key, bucket, key)

    return key
