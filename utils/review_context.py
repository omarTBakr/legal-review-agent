"""
What a chat answer is grounded in: the advice, and the documents' text.

Both are fetched from the bucket, and both are immutable once written — a
document's advice does not change after it is stored, and neither does its
parsed text. That makes them worth holding on to: a conversation asks many
questions about the same review, and re-reading the same objects for every one
of them was costing more than the model did.

Two things make this fast enough to sit in front of a chat turn:

- the documents are read **concurrently**, because a review has several and
  they do not depend on each other;
- a completed read is **cached** for a short while, so the second question
  about a review pays nothing.

Only complete reads are cached. A review whose documents are still being
written would otherwise be remembered as half-finished.
"""

import asyncio
import time
from dataclasses import dataclass, field

from exceptions.storage import ObjectNotFoundError
from schemas.legal_advice import LegalAdvice
from utils.advice_store import read_advice, read_markdown
from utils.config import Settings
from utils.logger import get_logger

logger = get_logger(__name__)

# long enough for a conversation, short enough that a review finishing while
# someone is chatting about it is picked up without a restart
CACHE_SECONDS = 120


@dataclass
class ReviewContext:
    """One review's advice and text, ready to be put in a prompt."""

    advice: list[str] = field(default_factory=list)
    documents: dict[str, str] = field(default_factory=dict)
    # documents with no stored advice yet, which is why a partial read is not cached
    missing: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return bool(self.advice) and not self.missing


_cache: dict[tuple[str, str], tuple[float, ReviewContext]] = {}


def clear_cache() -> None:
    _cache.clear()


async def load_context(project_id: str, task_id: str, pdf_keys: list[str], settings: Settings) -> ReviewContext:
    """The advice and text for a review, from the cache when it is still warm."""
    key = (project_id, task_id)
    cached = _cache.get(key)

    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]

    context = await _read(pdf_keys, settings)

    if context.complete:
        _cache[key] = (time.monotonic(), context)

    return context


async def _read(pdf_keys: list[str], settings: Settings) -> ReviewContext:
    """Reads every document at once, rather than one after another."""
    documents = await asyncio.gather(*(asyncio.to_thread(_one, key, settings) for key in pdf_keys))

    context = ReviewContext()

    for pdf_key, advice, markdown in documents:
        if advice is None:
            # still being reviewed, or its review failed
            context.missing.append(pdf_key)
            continue

        context.advice.append(_render(pdf_key, advice))

        if markdown is None:
            logger.info("no stored text for %s; answering from the advice alone", pdf_key)
        else:
            context.documents[pdf_key] = markdown

    return context


def _one(pdf_key: str, settings: Settings) -> tuple[str, LegalAdvice | None, str | None]:
    """One document's advice and text. Blocking, and run in a thread."""
    try:
        advice = read_advice(pdf_key, settings, settings.s3_projects)
    except ObjectNotFoundError:
        return pdf_key, None, None

    try:
        markdown = read_markdown(pdf_key, settings, settings.s3_projects)
    except ObjectNotFoundError:
        markdown = None

    return pdf_key, advice, markdown


def _render(pdf_key: str, advice: LegalAdvice) -> str:
    """One document's review, as the prompt shows it."""
    risks = "\n".join(
        f"- [{risk.severity.value}] {risk.description}"
        + (f' — "{risk.quote}"' if risk.quote else "")
        + (f" (p. {risk.page})" if risk.page else "")
        for risk in advice.key_risks
    )

    return f"{pdf_key}\nSummary: {advice.summary}\nKey risks:\n{risks or '- none flagged'}"
