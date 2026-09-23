"""Temporal activities, one per file.

Each activity is a thin wrapper: it takes a dataclass from `schemas`, does one
side-effecting step, and returns a dataclass. The real work stays in `utils`
and `parsers` so it can be tested without a Temporal server.
"""

from activities.analyze_batch import analyze_batch
from activities.cleanup_scratch import cleanup_scratch
from activities.download_md import download_md
from activities.download_pdf import download_pdf
from activities.human_followup import human_followup
from activities.merge_advice import merge_advice
from activities.parse_pdf import parse_pdf
from activities.send_report import send_report
from activities.split_pages import split_pages
from activities.upload_advice import upload_advice
from activities.upload_md import upload_md
from activities.upload_pdf import upload_pdf

# the PDF -> markdown pipeline
PDF_ACTIVITIES = [upload_pdf, download_pdf, parse_pdf, upload_md, download_md]

# the legal review pipeline; it reuses download_pdf to fetch the document
LEGAL_ACTIVITIES = [
    download_pdf,
    split_pages,
    analyze_batch,
    merge_advice,
    human_followup,
    upload_advice,
    send_report,
    cleanup_scratch,
]

# every activity, for a worker that serves both queues
ALL_ACTIVITIES = PDF_ACTIVITIES + [
    split_pages,
    analyze_batch,
    merge_advice,
    human_followup,
    upload_advice,
    send_report,
    cleanup_scratch,
]

__all__ = [
    "ALL_ACTIVITIES",
    "LEGAL_ACTIVITIES",
    "PDF_ACTIVITIES",
    "analyze_batch",
    "cleanup_scratch",
    "download_md",
    "download_pdf",
    "human_followup",
    "merge_advice",
    "parse_pdf",
    "send_report",
    "split_pages",
    "upload_advice",
    "upload_md",
    "upload_pdf",
]
