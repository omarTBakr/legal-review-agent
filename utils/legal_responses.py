"""Response bodies for the legal endpoints, in one place so the submit, status
and respond routes describe the same task the same way."""

from enums.TaskStatus import TaskStatus
from schemas.legal_review import LegalReviewResult
from utils.workflow_ids import legal_workflow_id_for


def accepted_response(task_id: str, pdf_keys: list[str], pdf_bucket: str, project_id: str = "") -> dict:
    """The 202 body: what was stored, and the id to poll with."""
    return {
        "status": TaskStatus.PROCESSING.value,
        "task_id": task_id,
        "workflow_id": legal_workflow_id_for(task_id),
        "pdf_bucket": pdf_bucket,
        "pdf_keys": pdf_keys,
        "pdf_count": len(pdf_keys),
        "project_id": project_id,
    }


def advice_body(pdf_key: str, advice) -> dict:
    """One document's advice, as the API reports it."""
    return {
        "pdf_key": pdf_key,
        "s3_path": advice.s3_path,
        "summary": advice.summary,
        "key_risks": [risk.to_dict() for risk in advice.key_risks],
        "review_decision": advice.review_decision.value,
        "needs_attention": advice.review_decision.needs_attention,
    }


def completed_response(result: LegalReviewResult) -> dict:
    """The finished review, every document included."""
    return {
        "status": TaskStatus.COMPLETED.value,
        "task_id": result.task_id,
        "workflow_id": legal_workflow_id_for(result.task_id),
        "document_count": result.document_count,
        "documents": [advice_body(doc.pdf_key, doc.advice) for doc in result.documents],
    }


def running_response(task_id: str, progress: dict, questions: list[dict], finished: list | None = None) -> dict:
    """
    A review still in progress.

    Reports AWAITING_HUMAN when anything is blocked on a person, because that
    is the state a caller has to act on rather than wait out. `results` holds
    the advice for documents already done, so they can be read before the
    rest of the review finishes.
    """
    status = TaskStatus.AWAITING_HUMAN if questions else TaskStatus.PROCESSING

    return {
        "status": status.value,
        "task_id": task_id,
        "workflow_id": legal_workflow_id_for(task_id),
        "documents": progress,
        "pending_questions": questions,
        "results": [advice_body(doc.pdf_key, doc.advice) for doc in finished or []],
    }


def status_response(task_id: str, status: TaskStatus) -> dict:
    """A review that ended without a result."""
    return {
        "status": status.value,
        "task_id": task_id,
        "workflow_id": legal_workflow_id_for(task_id),
    }


def answer_accepted_response(task_id: str, pdf_key: str) -> dict:
    return {
        "status": "accepted",
        "task_id": task_id,
        "pdf_key": pdf_key,
        "workflow_id": legal_workflow_id_for(task_id),
    }
