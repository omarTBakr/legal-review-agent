"""
What this service raises when it cannot do what it was asked.

Mirrors the parent project's `exceptions/` package, deliberately as a copy
rather than an import: `voice/` is its own uv project holding torch and little
else, and reaching into the parent would drag boto3, pydantic-settings and the
rest into a process that wants none of them.

`AudioError` is a ValueError because that is what it is — the bytes handed to us
are not audio we can use — and the HTTP layer turns it into a 422, the caller's
mistake rather than ours.
"""


class AudioError(ValueError):
    """The bytes we were handed are not audio we can use."""
