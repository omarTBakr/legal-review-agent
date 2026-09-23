from contextlib import contextmanager

from fastapi import HTTPException
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError

from exceptions import AIAgentError, ParsingError, StorageError, ValidationError
from exceptions.voice import VoiceError, VoiceUnavailableError
from exceptions.workflow import TemporalConnectionError, WorkflowExecutionError
from utils.logger import get_logger

logger = get_logger(__name__)


@contextmanager
def http_errors(context: str):
    """
    Maps the project's exceptions onto HTTP status codes.

    Shared by every endpoint so the mapping cannot drift between them, and so
    a route reads as what it does rather than as a wall of except clauses.

        with http_errors(f"task {task_id}"):
            ...
    """
    try:
        yield
    except ValidationError as exc:
        # the caller sent something unusable
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ParsingError as exc:
        # a real PDF was sent but could not be read: unprocessable, not a server fault
        logger.warning("could not parse %s: %s", context, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except StorageError as exc:
        logger.error("storage failed for %s: %s", context, exc)
        raise HTTPException(status_code=502, detail=f"storage error: {exc}") from exc
    except VoiceUnavailableError as exc:
        # the voice service is a separate process; it being down is not a bug here
        logger.error("voice service unavailable for %s: %s", context, exc)
        raise HTTPException(status_code=503, detail=f"voice service unavailable: {exc}") from exc
    except VoiceError as exc:
        logger.warning("voice failed for %s: %s", context, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (TemporalConnectionError, RPCError) as exc:
        logger.error("temporal unreachable for %s: %s", context, exc)
        raise HTTPException(status_code=503, detail=f"temporal unavailable: {exc}") from exc
    except (WorkflowExecutionError, WorkflowFailureError, ApplicationError) as exc:
        logger.exception("workflow failed for %s", context)
        raise HTTPException(status_code=500, detail=f"workflow failed: {exc}") from exc
    except AIAgentError as exc:
        logger.exception("pipeline failed for %s", context)
        raise HTTPException(status_code=500, detail=f"processing failed: {exc}") from exc
    except HTTPException:
        # already mapped: a 404 raised inside the block must stay a 404
        raise
    except Exception as exc:
        # anything unplanned is a bug; log it with a traceback
        logger.exception("unexpected failure for %s", context)
        raise HTTPException(status_code=500, detail=f"processing failed: {exc}") from exc
