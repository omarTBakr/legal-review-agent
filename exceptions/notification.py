from exceptions.base import AIAgentError


class NotificationError(AIAgentError):
    """Something that should have been sent was not."""


class EmailNotConfiguredError(NotificationError):
    """An email was asked for but no SMTP server is configured."""


class EmailSendError(NotificationError):
    """The SMTP server refused the message or could not be reached."""
