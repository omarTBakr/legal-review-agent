from dataclasses import dataclass


@dataclass
class DownloadPdfInput:
    """
    PDF to pull into the local PDF scratch folder.

    `bucket` is empty for the PDF pipeline, which uses S3_PDF_BUCKET; a review
    inside a project names that project's bucket instead.
    """

    task_id: str
    key: str
    bucket: str = ""


@dataclass
class DownloadPdfOutput:
    bucket: str
    local_path: str
