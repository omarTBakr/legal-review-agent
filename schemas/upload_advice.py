from dataclasses import dataclass

from schemas.legal_advice import LegalAdvice


@dataclass
class UploadAdviceInput:
    """Finished advice to store as JSON."""

    task_id: str
    pdf_key: str
    advice: LegalAdvice
    # where the advice is stored; empty means S3_LEGAL_ADVICE
    bucket: str = ""


@dataclass
class UploadAdviceOutput:
    bucket: str
    key: str
    s3_path: str
