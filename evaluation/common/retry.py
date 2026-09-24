"""
Retrying a model call that failed for a reason that might not recur.

The product retries through Temporal, which the evaluation does not have: it is
plain asyncio, and until now a single 429 became a finding graded 0 with an
error on it. That is tolerable against a paid endpoint, which rarely throttles,
and useless against a **free** one, which throttles constantly — a whole run's
layer 2 would come back ungraded and every finding would escalate.

Only two failures are retried, and both because they say "not now" rather than
"no": rate limiting, and a timeout. A malformed reply is not retried here — the
judge records it as an error verdict, which escalates the finding to a human,
and that is the right outcome for output nobody could read.

The delay is exponential with jitter. Jitter because several findings are graded
concurrently and all of them get throttled at the same moment: without it they
would retry in lockstep and throttle each other again.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable

from exceptions.llm import LLMRateLimitError, LLMTimeoutError
from utils.logger import get_logger

logger = get_logger(__name__)

# what is worth waiting for: both mean "not now", neither means "no"
RETRYABLE = (LLMRateLimitError, LLMTimeoutError)

DEFAULT_ATTEMPTS = 5
DEFAULT_BASE_SECONDS = 4.0
# a free endpoint can be busy for minutes; without a ceiling the backoff walks
# off into hours
MAX_DELAY_SECONDS = 120.0


def backoff_delay(attempt: int, base: float = DEFAULT_BASE_SECONDS) -> float:
    """The wait before attempt N, exponential with jitter, capped."""
    delay = min(base * (2 ** (attempt - 1)), MAX_DELAY_SECONDS)

    # up to a quarter more, so concurrent callers do not retry in lockstep
    return delay + random.uniform(0, delay / 4)


async def with_backoff[T](
    call: Callable[[], Awaitable[T]],
    what: str = "the model",
    attempts: int = DEFAULT_ATTEMPTS,
    base: float = DEFAULT_BASE_SECONDS,
    sleep=asyncio.sleep,
) -> T:
    """
    Runs `call`, retrying while it is throttled or times out.

    Re-raises the last failure when the attempts run out, so the caller still
    sees a real exception rather than a silent None — the judge turns that into
    an error verdict, which escalates.
    """
    last: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except RETRYABLE as exc:
            last = exc
            if attempt == attempts:
                break

            delay = backoff_delay(attempt, base)
            logger.warning(
                "%s failed (%s); retrying in %.1fs (attempt %d of %d)",
                what,
                type(exc).__name__,
                delay,
                attempt + 1,
                attempts,
            )
            await sleep(delay)

    raise last
