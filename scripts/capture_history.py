"""
Saves a finished workflow's history for the replay tests.

    uv run python scripts/capture_history.py legal-review-d0c391d7
    uv run python scripts/capture_history.py legal-review-d0c391d7 --name two-documents

The replay tests run the current code against these files, so a change that
would break a workflow already in flight fails in CI rather than in production.
A history is worth keeping when it covers a path the others do not: several
documents at once, a document that waited on a human, one that timed out, one
that failed.

**Capture from test documents only.** A history carries the activity inputs,
and the legal review's inputs include the text of the documents. Anything
captured from a real client's review would put that text in the repository.
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

from utils.config import get_setting
from utils.logger import get_logger, setup_logging
from utils.temporal_client import get_temporal_client

logger = get_logger(__name__)

HISTORIES = Path(__file__).resolve().parent.parent / "tests" / "histories"


async def capture(workflow_id: str, name: str = "") -> Path:
    """Fetches one workflow's history and writes it as JSON."""
    client = await get_temporal_client()
    handle = client.get_workflow_handle(workflow_id)

    history = await handle.fetch_history()
    events = len(history.events)

    slug = name or re.sub(r"[^a-z0-9]+", "-", workflow_id.lower()).strip("-")
    path = HISTORIES / f"{slug}.json"
    HISTORIES.mkdir(parents=True, exist_ok=True)
    path.write_text(history.to_json())

    logger.info("wrote %s (%d events, %d KB)", path.name, events, path.stat().st_size // 1024)

    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("workflow_id", help="the workflow to capture, e.g. legal-review-a1b2c3d4")
    parser.add_argument("--name", default="", help="file name to write, without .json")
    arguments = parser.parse_args()

    setup_logging()
    settings = get_setting()
    logger.info("reading %s from %s", arguments.workflow_id, settings.temporal_host)

    try:
        asyncio.run(capture(arguments.workflow_id, arguments.name))
    except Exception as exc:
        logger.error("could not capture %s: %s", arguments.workflow_id, exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
