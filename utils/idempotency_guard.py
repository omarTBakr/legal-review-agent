"""
Giving an idempotency claim back when the work it reserved does not happen.

A claim is written *before* the upload, so two concurrent requests carrying one
key cannot both proceed. That ordering is what makes the guarantee work, and it
is also what makes this necessary: if anything after the claim fails, the claim
has to go. Otherwise the client's retry is answered with the task id it
reserved, for a workflow that was never started, and they poll it for ever.

Only failures release it. A claim that survives is completed by the caller once
the workflow is genuinely running — so a completed claim is a promise that there
is something on the other end of that task id.
"""

import asyncio
from contextlib import asynccontextmanager


@asynccontextmanager
async def released_on_failure(store, key: str):
    """Removes `key` from `store` if the wrapped block raises."""
    try:
        yield
    except Exception:
        if store and key:
            await asyncio.to_thread(store.remove, key)
        raise
