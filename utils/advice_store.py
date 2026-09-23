"""
Where a document's advice lives in the bucket, and how to read it back.

The workflow is not the only way to a finished review: Temporal drops a closed
workflow's history once its retention period passes, while the advice JSON
stays in the bucket for good. Reading it back from here is what lets a task id
still answer for a review from last year.
"""

import json

from enums.ReviewDecision import ReviewDecision
from schemas.legal_advice import LegalAdvice
from utils.config import Settings
from utils.utility import download_s3_bytes

ADVICE_SUFFIX = ".advice.json"
MARKDOWN_SUFFIX = ".md"


def advice_key(pdf_key: str) -> str:
    """
    The advice object matching a PDF key.

    `contract-a1b2c3d4.pdf` becomes `contract-a1b2c3d4.advice.json`, and a key
    inside a project keeps its prefix.
    """
    return f"{_stem(pdf_key)}{ADVICE_SUFFIX}"


def _stem(pdf_key: str) -> str:
    """The key without its .pdf, keeping any project prefix."""
    return pdf_key[: -len(".pdf")] if pdf_key.lower().endswith(".pdf") else pdf_key


def markdown_key(pdf_key: str) -> str:
    """
    The parsed Markdown matching a PDF key, in S3_PARSED_MDS.

    Stored during the review so a question asked about the documents later can
    be answered from the text itself, not only from the advice.
    """
    return f"{_stem(pdf_key)}{MARKDOWN_SUFFIX}"


def read_markdown(pdf_key: str, settings: Settings, bucket: str = "") -> str:
    """The document's text. Raises ObjectNotFoundError when it was never stored."""
    return download_s3_bytes(bucket or settings.s3_parsed_mds, markdown_key(pdf_key)).decode("utf-8")


def read_advice(pdf_key: str, settings: Settings, bucket: str = "") -> LegalAdvice:
    """
    Reads stored advice back into a `LegalAdvice`.

    Raises ObjectNotFoundError when the document has not finished, or never
    did. The stored document is our own, but it is parsed through the same
    `from_model` validation as a model reply: a hand-edited file should fail
    loudly rather than produce advice with no severity.
    """
    bucket = bucket or settings.s3_legal_advice
    stored = json.loads(download_s3_bytes(bucket, advice_key(pdf_key)))

    advice = LegalAdvice.from_model(stored)
    advice.s3_path = f"s3://{bucket}/{advice_key(pdf_key)}"

    # from_model deliberately ignores these: the model may not set them, but
    # our own stored document is where they come from
    advice.review_decision = _decision(stored)
    advice.key_risks = _risks(advice, stored)

    return advice


def _decision(stored: dict) -> ReviewDecision:
    """The decision as stored; anything unrecognised falls back to the mildest."""
    try:
        return ReviewDecision(str(stored.get("review_decision", "")))
    except ValueError:
        return ReviewDecision.AUTO_APPROVED


def _risks(advice: LegalAdvice, stored: dict) -> list:
    """Restores the verification flag, which `from_model` refuses to trust."""
    flags = [bool(raw.get("quote_verified", False)) for raw in stored.get("key_risks", [])]

    for risk, verified in zip(advice.key_risks, flags, strict=False):
        risk.quote_verified = verified

    return advice.key_risks
