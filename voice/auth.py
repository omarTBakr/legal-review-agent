"""
The same shared key the API checks, checked here too.

This process holds no documents, but it will transcribe and synthesise anything
asked of it on a GPU, and an open port that runs a model for free is worth
closing. Only the API calls it, so the key it presents is the same `API_KEY`:
one secret to set is more likely to be set than two.

A deliberate copy of `utils/auth.py` rather than an import of it — `voice/` is
its own uv project with its own dependencies, and reaching into the parent would
drag `pydantic-settings`, `boto3` and the rest into a process that wants torch
and nothing else. The comparison is `hmac.compare_digest` for the reason it is
there: `==` leaks how much of the key was right in the time it takes to fail.
"""

import hmac

from fastapi import Header, HTTPException

from config import VoiceSettings, get_settings
from logger import get_logger

logger = get_logger(__name__)

HEADER_NAME = "X-API-Key"

UNAUTHORIZED = 401


def is_open(settings: VoiceSettings | None = None) -> bool:
    """Whether the service is answering without a key."""
    return not (settings or get_settings()).api_key


def warn_if_open(settings: VoiceSettings | None = None) -> bool:
    """Logs, at startup, when there is no key set. Returns whether it warned."""
    if not is_open(settings):
        return False

    logger.warning(
        "API_KEY is not set: anyone who can reach this port can run the models. "
        "That is fine on 127.0.0.1 and not fine anywhere the port is published."
    )

    return True


def key_matches(offered: str, expected: str) -> bool:
    """Constant-time comparison of the offered key against the configured one."""
    return hmac.compare_digest(offered.encode("utf-8"), expected.encode("utf-8"))


async def require_api_key(x_api_key: str = Header("", alias=HEADER_NAME)) -> None:
    """
    Rejects a request without the right key.

    `/health` stays open: the API's readiness check reads it to decide whether
    the models have finished loading, and that should not depend on a secret.
    """
    settings = get_settings()

    if is_open(settings):
        return

    if not key_matches(x_api_key or "", settings.api_key):
        raise HTTPException(status_code=UNAUTHORIZED, detail=f"a valid {HEADER_NAME} header is required")
