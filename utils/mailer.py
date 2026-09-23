"""
Sending one email over SMTP.

Deliberately small: no templates, no queue, no provider SDK. A report is one
HTML message with the advice JSON attached, and Temporal already provides the
retries that an email queue would otherwise be there for.

`smtplib` blocks, so callers run `send_email` in a thread.
"""

import smtplib
from email.message import EmailMessage

from exceptions.notification import EmailNotConfiguredError, EmailSendError
from utils.config import Settings
from utils.logger import get_logger

logger = get_logger(__name__)


def is_configured(settings: Settings) -> bool:
    """Whether there is an SMTP server to send through at all."""
    return bool(settings.smtp_host and settings.smtp_from)


def build_message(recipient: str, subject: str, html: str, settings: Settings, attachments=()) -> EmailMessage:
    """
    Builds the message, with a plain-text part for clients that want one.

    Kept separate from sending so a test can read what would go out without a
    server anywhere in sight.
    """
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = recipient
    message["Subject"] = subject

    message.set_content("This report is formatted as HTML. The full advice is attached as JSON.")
    message.add_alternative(html, subtype="html")

    for filename, content, subtype in attachments:
        message.add_attachment(content, maintype="application", subtype=subtype, filename=filename)

    return message


def send_email(recipient: str, subject: str, html: str, settings: Settings, attachments=()) -> None:
    """
    Sends one message. Raises rather than returning a flag, so a caller that
    cares (the activity) can retry and one that does not can ignore it.
    """
    if not is_configured(settings):
        raise EmailNotConfiguredError("no SMTP host or sender configured; set SMTP_HOST and SMTP_FROM")

    message = build_message(recipient, subject, html, settings, attachments)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)

            server.send_message(message)
    except smtplib.SMTPException as exc:
        raise EmailSendError(f"could not send the report to {recipient}: {exc}") from exc
    except OSError as exc:
        # connection refused, DNS failure, timeout
        raise EmailSendError(f"could not reach the mail server {settings.smtp_host}: {exc}") from exc

    logger.info("sent %r to %s", subject, recipient)
