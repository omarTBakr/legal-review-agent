import asyncio

from temporalio import activity

from exceptions.notification import EmailNotConfiguredError
from schemas.send_report import SendReportInput, SendReportOutput
from utils.config import get_setting
from utils.mailer import send_email
from utils.report import render_html, render_json, subject_for


@activity.defn
async def send_report(payload: SendReportInput) -> SendReportOutput:
    """
    Emails the finished review.

    A missing SMTP configuration is reported, not raised: the review itself
    succeeded, and retrying cannot conjure a mail server. A server that is
    configured but refuses the message does raise, so Temporal retries it.
    """
    settings = get_setting()

    if not payload.recipient:
        return SendReportOutput(sent=False, reason="no recipient")

    activity.logger.info("[task %s] emailing the report to %s", payload.task_id, payload.recipient)

    subject = subject_for(payload.documents, payload.project_name)
    html = render_html(payload.task_id, payload.documents, payload.project_name)
    attachment = (f"legal-review-{payload.task_id}.json", render_json(payload.task_id, payload.documents), "json")

    try:
        # smtplib blocks, and the worker has other documents in flight
        await asyncio.to_thread(send_email, payload.recipient, subject, html, settings, [attachment])
    except EmailNotConfiguredError as exc:
        activity.logger.warning("[task %s] not emailing the report: %s", payload.task_id, exc)
        return SendReportOutput(sent=False, recipient=payload.recipient, reason=str(exc))
    except Exception:
        activity.logger.exception("[task %s] could not email the report", payload.task_id)
        raise

    return SendReportOutput(sent=True, recipient=payload.recipient)
