from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from routes.chat import router as chat_router
from routes.legal import router as legal_router
from routes.process import router as process_router
from routes.projects import router as projects_router
from routes.voice import router as voice_router
from utils.config import get_setting
from utils.logger import get_logger, setup_logging
from workers.process_pdf_worker import create_process_pdf_worker

logger = get_logger(__name__)

# the browser UI: static files, no build step
UI_DIR = Path(__file__).parent / "ui"


class FreshStaticFiles(StaticFiles):
    """
    Serves the UI with `Cache-Control: no-cache`.

    Not "do not cache": the browser keeps the file and asks whether it changed,
    which is a 304 and costs nothing. Without it a changed module can sit in
    the memory cache behind an unchanged `index.html`, and the page runs half
    the old code — a confusing way to lose an afternoon, and the files here are
    a few kilobytes each.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"

        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Optionally runs the Temporal worker alongside the API.

    With RUN_WORKER_IN_API set, one process serves requests and executes
    workflows, which is convenient locally. In production leave it off and run
    workers/process_pdf_worker.py separately: a slow parse then cannot starve
    request handling, and in-flight work survives an API restart.
    """
    settings = get_setting()

    if not settings.run_worker_in_api:
        logger.info("worker not started in-process; run worker.py separately")
        yield
        return

    # Failing here is deliberate: an API that was asked to host the worker but
    # has none would accept uploads that nothing ever picks up.
    worker = await create_process_pdf_worker()

    logger.info("worker running inside the API on %r", worker.task_queue)

    async with worker:
        yield


app = FastAPI(title="Legal Review Agent", description="PDF to Markdown and LLM legal review pipelines", lifespan=lifespan)
app.include_router(process_router)
app.include_router(legal_router)
app.include_router(projects_router)
app.include_router(chat_router)
app.include_router(voice_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def index() -> RedirectResponse:
    """The browser UI lives under /ui."""
    return RedirectResponse(url="/ui/")


app.mount("/ui", FreshStaticFiles(directory=UI_DIR, html=True), name="ui")


def main():
    setup_logging()
    settings = get_setting()
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
