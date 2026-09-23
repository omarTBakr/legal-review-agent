"""
One shared key on a header, checked before any route runs.

Not a user model. There is nothing here about who you are, only that you were
given the key: it is the difference between "anyone who can reach the port owns
every client's contracts" and "you need the secret", which is the gap worth
closing first. Per-user accounts with projects belonging to someone is a
different and much larger piece of work, and this does not stand in its way.

**An empty `API_KEY` leaves the API open**, which is what you want on a laptop
and is never what you want anywhere else. `warn_if_open` says so at startup,
once, at WARNING, because a deployment that quietly serves an open API is the
failure this module exists to prevent and a silent default would hide it.

The comparison is `hmac.compare_digest`, not `==`. String equality returns as
soon as two bytes differ, and the time it took says how much of the key was
right; a few thousand requests turn that into the key itself.
"""

import hmac

from fastapi import Header, HTTPException

from utils.config import Settings, get_setting
from utils.logger import get_logger

logger = get_logger(__name__)

HEADER_NAME = "X-API-Key"

UNAUTHORIZED = 401


def is_open(settings: Settings | None = None) -> bool:
    """Whether the API is serving without a key."""
    return not (settings or get_setting()).api_key


def warn_if_open(settings: Settings | None = None) -> bool:
    """
    Logs, once at startup, when there is no key set. Returns whether it warned.

    Deliberately loud and deliberately not fatal: refusing to start would break
    every local run, and saying nothing is how an open API reaches a network.
    """
    if not is_open(settings):
        return False

    logger.warning(
        "API_KEY is not set: every route is open to anyone who can reach %s. "
        "Set API_KEY in .env before this is reachable from anywhere but this machine.",
        "this port",
    )

    return True


def key_matches(offered: str, expected: str) -> bool:
    """Constant-time comparison of the offered key against the configured one."""
    return hmac.compare_digest(offered.encode("utf-8"), expected.encode("utf-8"))


async def require_api_key(x_api_key: str = Header("", alias=HEADER_NAME)) -> None:
    """
    FastAPI dependency: rejects a request without the right key.

    Attached to each router in `main.py` rather than to every route, so a route
    added later is covered by having been added at all — the alternative is a
    decorator someone forgets on the one endpoint that uploads documents.

    `/health` and the static UI are mounted outside those routers and stay open:
    a monitor should not need the secret to see the process is alive, and the
    page has to load in order to ask for the key.
    """
    settings = get_setting()

    if is_open(settings):
        return

    if not key_matches(x_api_key or "", settings.api_key):
        # no detail about what was wrong with it; "wrong length" is a hint
        raise HTTPException(status_code=UNAUTHORIZED, detail=f"a valid {HEADER_NAME} header is required")
