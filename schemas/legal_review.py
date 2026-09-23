from dataclasses import dataclass, field

from schemas.legal_advice import LegalAdvice


@dataclass
class LegalReviewInput:
    """PDFs already in the PDF bucket, to be reviewed together."""

    task_id: str
    pdf_keys: list[str]
    pages_per_batch: int = 30
    max_concurrent_pdfs: int = 10
    human_input_timeout_seconds: float = 3600
    # where the finished report goes; empty means no email is sent
    report_email: str = ""
    project_id: str = ""
    project_name: str = ""
    # the bucket holding this review's documents, their text and their advice:
    # a project's own, or empty for the pipeline's default buckets
    bucket: str = ""


@dataclass
class DocumentAdvice:
    """One document's advice, paired with the key it came from."""

    pdf_key: str
    advice: LegalAdvice


@dataclass
class LegalReviewResult:
    task_id: str
    documents: list[DocumentAdvice] = field(default_factory=list)

    @property
    def document_count(self) -> int:
        return len(self.documents)
