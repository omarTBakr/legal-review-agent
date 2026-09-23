from dataclasses import dataclass, field

from schemas.legal_review import DocumentAdvice


@dataclass
class SendReportInput:
    """A finished review, and where to email it."""

    task_id: str
    recipient: str
    documents: list[DocumentAdvice] = field(default_factory=list)
    project_name: str = ""


@dataclass
class SendReportOutput:
    """Whether the report went out; `reason` says why not when it did not."""

    sent: bool
    recipient: str = ""
    reason: str = ""
