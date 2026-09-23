"""Exception hierarchy for the project.

    AIAgentError
    |
    +-- ConfigurationError
    |     +-- MissingSettingError
    |     +-- InvalidSettingError
    |
    +-- ValidationError
    |     +-- UnsupportedFileTypeError
    |     +-- EmptyFileError
    |     +-- TooManyFilesError
    |
    +-- StorageError
    |     +-- StorageConnectionError
    |     +-- UploadError
    |     +-- DownloadError
    |     |     +-- ObjectNotFoundError
    |     +-- LocalFileNotFoundError      (also a FileNotFoundError)
    |
    +-- ParsingError
    |     +-- PdfNotFoundError            (also a FileNotFoundError)
    |     +-- InvalidPdfError
    |
    +-- LLMError
    |     +-- LLMConfigurationError
    |     +-- LLMTimeoutError
    |     +-- LLMRateLimitError
    |     +-- LLMResponseError
    |
    +-- WorkflowError
    |     +-- ActivityFailedError
    |     +-- TemporalConnectionError
    |     +-- WorkflowExecutionError
    |
    +-- NotificationError
    |     +-- EmailNotConfiguredError
    |     +-- EmailSendError
    |
    +-- VoiceError
          +-- VoiceConfigurationError
          +-- VoiceUnavailableError
          +-- TranscriptionError
          +-- SynthesisError

Catch `AIAgentError` for anything the project raised on purpose; catch a
subtree (`StorageError`) when the handling is the same across a domain.
"""

from exceptions.base import AIAgentError
from exceptions.config import ConfigurationError, InvalidSettingError, MissingSettingError
from exceptions.llm import (
    LLMConfigurationError,
    LLMError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
)
from exceptions.notification import EmailNotConfiguredError, EmailSendError, NotificationError
from exceptions.parsing import InvalidPdfError, ParsingError, PdfNotFoundError
from exceptions.storage import (
    DownloadError,
    LocalFileNotFoundError,
    ObjectNotFoundError,
    StorageConnectionError,
    StorageError,
    UploadError,
)
from exceptions.validation import EmptyFileError, TooManyFilesError, UnsupportedFileTypeError, ValidationError
from exceptions.voice import (
    SynthesisError,
    TranscriptionError,
    VoiceConfigurationError,
    VoiceError,
    VoiceUnavailableError,
)
from exceptions.workflow import ActivityFailedError, TemporalConnectionError, WorkflowError, WorkflowExecutionError

__all__ = [
    "AIAgentError",
    "ActivityFailedError",
    "ConfigurationError",
    "DownloadError",
    "EmailNotConfiguredError",
    "EmailSendError",
    "EmptyFileError",
    "InvalidPdfError",
    "InvalidSettingError",
    "LLMConfigurationError",
    "LLMError",
    "LLMRateLimitError",
    "LLMResponseError",
    "LLMTimeoutError",
    "LocalFileNotFoundError",
    "MissingSettingError",
    "NotificationError",
    "ObjectNotFoundError",
    "ParsingError",
    "PdfNotFoundError",
    "StorageConnectionError",
    "SynthesisError",
    "StorageError",
    "TooManyFilesError",
    "TranscriptionError",
    "TemporalConnectionError",
    "UnsupportedFileTypeError",
    "UploadError",
    "ValidationError",
    "VoiceConfigurationError",
    "VoiceError",
    "VoiceUnavailableError",
    "WorkflowError",
    "WorkflowExecutionError",
]
