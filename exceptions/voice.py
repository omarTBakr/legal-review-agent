from exceptions.base import AIAgentError


class VoiceError(AIAgentError):
    """Speech in or out did not work."""


class VoiceConfigurationError(VoiceError):
    """No voice service is configured, or the provider is unknown."""


class VoiceUnavailableError(VoiceError):
    """The voice service could not be reached; it is a separate process."""


class TranscriptionError(VoiceError):
    """The audio could not be turned into text."""


class SynthesisError(VoiceError):
    """The text could not be turned into audio."""
