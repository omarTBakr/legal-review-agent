from dataclasses import dataclass

from schemas.page_batch import PageBatch


@dataclass
class SplitPagesInput:
    """A parsed document to break into LLM-sized pieces."""

    task_id: str
    pdf_key: str
    local_pdf: str
    pages_per_batch: int
    # where the parsed text is kept; empty means S3_PARSED_MDS
    md_bucket: str = ""


@dataclass
class SplitPagesOutput:
    """
    The batches to analyse, and where the whole text was stored.

    `md_key` is empty when the Markdown could not be stored; that is not worth
    failing a review over, it only means chat cannot quote this document later.
    """

    batches: list[PageBatch]
    page_count: int
    md_key: str = ""
