"""
Retrying a model call that said "not now".

The product retries through Temporal; the evaluation is plain asyncio and had
no retry at all, so one 429 from a free endpoint became a finding graded 0 with
an error on it. Against a free model — which throttles constantly — that would
have made every layer 2 number meaningless.
"""

import pytest

from evaluation.common.retry import (
    DEFAULT_BASE_SECONDS,
    MAX_DELAY_SECONDS,
    backoff_delay,
    with_backoff,
)
from exceptions.llm import LLMError, LLMRateLimitError, LLMResponseError, LLMTimeoutError


class Caller:
    """Fails a given number of times, then succeeds."""

    def __init__(self, failures=0, error=None, result="ok"):
        self.remaining = failures
        self.error = error or LLMRateLimitError("throttled")
        self.result = result
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise self.error
        return self.result


def no_sleep():
    """Records the delays instead of waiting them out."""
    waited = []

    async def sleep(seconds):
        waited.append(seconds)

    return waited, sleep


async def test_a_call_that_works_is_not_retried():
    call = Caller()

    assert await with_backoff(call, sleep=no_sleep()[1]) == "ok"
    assert call.calls == 1


async def test_a_throttled_call_is_retried_until_it_works():
    call = Caller(failures=3)
    waited, sleep = no_sleep()

    assert await with_backoff(call, sleep=sleep) == "ok"
    assert call.calls == 4
    assert len(waited) == 3


async def test_a_timeout_is_retried_too():
    call = Caller(failures=1, error=LLMTimeoutError("too slow"))
    _, sleep = no_sleep()

    assert await with_backoff(call, sleep=sleep) == "ok"
    assert call.calls == 2


@pytest.mark.parametrize("error", [LLMResponseError("garbage"), LLMError("boom"), ValueError("nope")])
async def test_anything_that_means_no_is_not_retried(error):
    """
    A reply nobody could read will not read better the second time. The judge
    records it as an error verdict, which escalates the finding — the right
    outcome for output nobody can grade.
    """
    call = Caller(failures=1, error=error)
    _, sleep = no_sleep()

    with pytest.raises(type(error)):
        await with_backoff(call, sleep=sleep)

    assert call.calls == 1


async def test_the_last_failure_is_raised_when_the_attempts_run_out():
    """Not a silent None: the caller has to see a real exception."""
    call = Caller(failures=99)
    _, sleep = no_sleep()

    with pytest.raises(LLMRateLimitError):
        await with_backoff(call, attempts=3, sleep=sleep)

    assert call.calls == 3


async def test_the_delay_grows_between_attempts():
    call = Caller(failures=4)
    waited, sleep = no_sleep()

    await with_backoff(call, base=1.0, sleep=sleep)

    assert waited == sorted(waited)
    assert waited[-1] > waited[0]


def test_the_delay_is_jittered():
    """Concurrent callers are throttled together; without jitter they retry together."""
    delays = {backoff_delay(2) for _ in range(20)}

    assert len(delays) > 1


def test_the_delay_is_capped():
    """Without a ceiling the backoff walks off into hours."""
    assert backoff_delay(30) <= MAX_DELAY_SECONDS * 1.25


def test_the_first_delay_is_the_base():
    delay = backoff_delay(1)

    assert DEFAULT_BASE_SECONDS <= delay <= DEFAULT_BASE_SECONDS * 1.25
