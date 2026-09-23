from exceptions.base import AIAgentError


class ValidationError(AIAgentError):
    """The caller sent something the API will not accept."""


class UnsupportedFileTypeError(ValidationError):
    """The upload is not a PDF."""


class EmptyFileError(ValidationError):
    """The upload contains no bytes."""


class TooManyFilesError(ValidationError):
    """More documents than the pipeline accepts in one request."""


class UploadTooLargeError(ValidationError):
    """A document, or a request, past the size the service accepts."""
