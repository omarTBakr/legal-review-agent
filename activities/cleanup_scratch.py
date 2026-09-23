import asyncio
from pathlib import Path

from temporalio import activity

from schemas.cleanup_scratch import CleanupScratchInput, CleanupScratchOutput


@activity.defn
async def cleanup_scratch(payload: CleanupScratchInput) -> CleanupScratchOutput:
    """
    Removes the local copies a document is finished with.

    The worker's scratch space is a staging area, not storage: the PDF and its
    Markdown are in the bucket, and the copies on disk only exist because
    parsing needs a file. Left alone they accumulate until the volume fills,
    which is a slow, quiet way to take the pipeline down.

    Nothing here raises. A file that cannot be deleted is a warning; failing
    the activity would fail a review that has already produced its advice.
    """
    removed, failed = 0, 0

    for path in payload.paths:
        if not path:
            continue

        try:
            await asyncio.to_thread(Path(path).unlink, True)
            removed += 1
        except OSError:
            activity.logger.warning("[task %s] could not remove %s", payload.task_id, path, exc_info=True)
            failed += 1

    activity.logger.info("[task %s] cleaned up %d file(s) for %s", payload.task_id, removed, payload.pdf_key)

    return CleanupScratchOutput(removed=removed, failed=failed)
