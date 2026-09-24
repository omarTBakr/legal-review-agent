"""Running the real review outside Temporal.

The workflow's activities each do one model call and one piece of validation;
what surrounds them is scheduling, retries and S3. This calls the same prompts,
the same LegalAdvice.from_model, the same verify_risks and the same
carry_verification, in the same order, without a worker. The scores therefore
measure the review, and a change to prompts/legal_advice.py moves them.

Two things the workflow does are deliberately not reproduced here: retries, so
that a malformed reply shows up as a failure rather than being quietly papered
over, and the human follow-up, which has no human in a batch run.

CUAD ships plain text, not PDFs, so `paginate` stands in for the parser when the
corpus is CUAD. That is the one stage of the pipeline the CUAD numbers do not
exercise; the fixture suite runs the real parser over real PDFs and covers it.
"""

import asyncio
import json
from dataclasses import dataclass, field

from enums.PromptName import PromptName
from interfaces.llm.interface import LLMInterface
from parsers.pymupdf_parser import parse_pdf_pages
from prompts import get_prompt
from schemas.key_risk import KeyRisk
from schemas.legal_advice import LegalAdvice
from schemas.page_batch import PageBatch
from utils.batching import split_pages_into_batches
from utils.evidence import carry_verification, verify_risks
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ReviewResult:
    """
    One document's review, with enough of the run kept to score it again for free.

    `raw_replies` is what each model call returned, verbatim. Scoring reads the
    advice; a re-score after a change to the metrics reads the replies and costs
    nothing.
    """

    document: str
    advice: LegalAdvice
    pages: list[str] = field(default_factory=list)
    batch_markdown: list[str] = field(default_factory=list)
    raw_replies: list[dict] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "document": self.document,
            "error": self.error,
            "summary": self.advice.summary,
            "needs_human": self.advice.needs_human,
            "question": self.advice.question,
            "key_risks": [risk.to_dict() for risk in self.advice.key_risks],
            "page_count": len(self.pages),
            "batch_count": len(self.batch_markdown),
            "raw_replies": self.raw_replies,
        }


def paginate(text: str, page_characters: int) -> list[str]:
    """
    Cuts plain text into page-sized pieces at paragraph boundaries.

    A stand-in for the PDF parser, for a corpus that has no PDFs. Breaking on
    blank lines rather than at a fixed offset keeps clauses whole, which matters
    because a clause split across two batches is a risk the review is being
    asked to find twice from half the evidence each time.
    """
    paragraphs = [block for block in text.split("\n\n") if block.strip()]

    pages: list[str] = []
    current: list[str] = []
    used = 0

    for block in paragraphs:
        if used and used + len(block) > page_characters:
            pages.append("\n\n".join(current))
            current, used = [], 0
        current.append(block)
        used += len(block) + 2

    if current:
        pages.append("\n\n".join(current))

    return pages or [text]


async def _analyze(llm: LLMInterface, document: str, batch: PageBatch, batch_count: int) -> tuple[LegalAdvice, dict]:
    """One batch, as activities/analyze_batch.py does it."""
    raw = await llm.complete_json(
        get_prompt(PromptName.LEGAL_ADVICE),
        pdf_key=document,
        batch_label=batch.label,
        batch_number=batch.index + 1,
        batch_count=batch_count,
        markdown=batch.markdown,
    )
    advice = LegalAdvice.from_model(raw)
    advice.key_risks = verify_risks(advice.key_risks, batch.markdown)

    return advice, raw


async def _merge(llm: LLMInterface, document: str, parts: list[LegalAdvice]) -> tuple[LegalAdvice, dict | None]:
    """The merge, as activities/merge_advice.py does it — skipped for a single batch."""
    if len(parts) == 1:
        return parts[0], None

    # the prompt is fed the same JSON rendering the activity feeds it
    rendered = "\n\n".join(
        json.dumps(
            {
                "summary": part.summary,
                "key_risks": [risk.to_prompt_dict() for risk in part.key_risks],
                "needs_human": part.needs_human,
                "question": part.question,
            },
            indent=2,
        )
        for part in parts
    )

    raw = await llm.complete_json(get_prompt(PromptName.MERGE_ADVICE), pdf_key=document, parts=rendered)
    advice = LegalAdvice.from_model(raw)
    advice.key_risks = carry_verification(advice.key_risks, [risk for part in parts for risk in part.key_risks])

    return advice, raw


async def review_pages(llm: LLMInterface, document: str, pages: list[str], pages_per_batch: int) -> ReviewResult:
    """
    Reviews an already-paginated document: batch, analyse each batch, merge.

    Batches run concurrently, as the workflow runs them, because a batch that is
    slow behind another batch would make a 40-page contract take minutes for no
    reason. A batch that fails takes the document with it and the error is
    recorded, so a run reports "3 of 25 contracts failed" instead of silently
    scoring 22.
    """
    batches = split_pages_into_batches(pages, pages_per_batch)

    try:
        outcomes = await asyncio.gather(*(_analyze(llm, document, batch, len(batches)) for batch in batches))
    except Exception as exc:
        logger.warning("%s: review failed during analysis (%s)", document, exc)
        return ReviewResult(
            document=document,
            advice=LegalAdvice(summary=""),
            pages=pages,
            batch_markdown=[batch.markdown for batch in batches],
            error=f"{type(exc).__name__}: {exc}",
        )

    parts = [advice for advice, _ in outcomes]
    replies = [raw for _, raw in outcomes]

    try:
        advice, merge_reply = await _merge(llm, document, parts)
    except Exception as exc:
        logger.warning("%s: review failed during merge (%s)", document, exc)
        return ReviewResult(
            document=document,
            advice=LegalAdvice(summary="", key_risks=[risk for part in parts for risk in part.key_risks]),
            pages=pages,
            batch_markdown=[batch.markdown for batch in batches],
            raw_replies=replies,
            error=f"{type(exc).__name__}: {exc}",
        )

    if merge_reply is not None:
        replies.append(merge_reply)

    return ReviewResult(
        document=document,
        advice=advice,
        pages=pages,
        batch_markdown=[batch.markdown for batch in batches],
        raw_replies=replies,
    )


async def review_text(llm: LLMInterface, document: str, text: str, pages_per_batch: int, page_characters: int) -> ReviewResult:
    """Reviews plain text, paginating it first."""
    return await review_pages(llm, document, paginate(text, page_characters), pages_per_batch)


async def review_pdf(llm: LLMInterface, document: str, path, pages_per_batch: int) -> ReviewResult:
    """
    Reviews a PDF through the real parser.

    parse_pdf_pages holds a lock and is CPU-bound, so it goes to a thread, which
    is also how activities/parse_pdf.py calls it.
    """
    pages = await asyncio.to_thread(parse_pdf_pages, path)

    return await review_pages(llm, document, pages, pages_per_batch)


def risks_from_dicts(records: list[dict]) -> list[KeyRisk]:
    """
    Rebuilds risks from a recorded run so scoring can be repeated without paying.

    quote_verified is restored from the record rather than recomputed: the
    verification happened against the batch the risk came from, and re-deriving
    it here would be checking a different thing.
    """
    risks = []

    for record in records:
        risk = KeyRisk.from_model(record)
        risk.quote_verified = bool(record.get("quote_verified"))
        risks.append(risk)

    return risks


def result_from_dict(record: dict) -> ReviewResult:
    """A ReviewResult as it was written to results/<timestamp>/reviews.jsonl."""
    advice = LegalAdvice(
        summary=record.get("summary", ""),
        key_risks=risks_from_dicts(record.get("key_risks") or []),
        needs_human=bool(record.get("needs_human")),
        question=record.get("question", ""),
    )

    return ReviewResult(
        document=record["document"],
        advice=advice,
        raw_replies=record.get("raw_replies") or [],
        error=record.get("error", ""),
    )
