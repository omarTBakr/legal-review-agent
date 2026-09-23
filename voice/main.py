"""
The voice service: Qwen3-ASR and Qwen3-TTS behind two endpoints.

It knows nothing about reviews, buckets or Temporal — the API calls it, the
browser never does. Both models are loaded once at startup and held for the
life of the process; loading per request would cost seconds every time.

    uv run --directory voice main.py

One GPU means one generation at a time, so each model sits behind a lock and
the blocking call runs in a thread, leaving the event loop free to accept the
next request rather than refusing it.
"""

import asyncio
import base64
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import JSONResponse

from asr import Transcriber
from audio import AudioError
from config import get_settings
from logger import get_logger, setup_logging
from tts import build_speaker

logger = get_logger(__name__)

settings = get_settings()
transcriber = Transcriber(settings)
speaker = build_speaker(settings)

# one generation at a time per model: the GPU is not shared well, and a second
# concurrent generate mostly buys an out-of-memory error
asr_lock = asyncio.Lock()
tts_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Loads both models before the first request, unless asked not to.

    A failure here is logged rather than fatal: the service still answers
    /health, saying which model is missing, instead of crash-looping in a
    container while the API reports it as merely unreachable.
    """
    if settings.eager_load:
        for name, load in (("ASR", transcriber.load), ("TTS", speaker.load)):
            try:
                await asyncio.to_thread(load)
            except Exception:
                logger.exception("could not load the %s model; it will be retried on the first request", name)

    if settings.warm_up and speaker.loaded:
        # the first generation compiles kernels and takes tens of seconds; far
        # better that it happens now than under the first person to ask
        try:
            started = time.monotonic()
            await asyncio.to_thread(speaker.speak, "Ready.")
            logger.info("warmed up in %.1fs", time.monotonic() - started)
        except Exception:
            logger.exception("the warm-up failed; the first real request will pay for it instead")

    yield


app = FastAPI(
    title="Legal Review Voice", description="Speech recognition and synthesis for the legal review agent", lifespan=lifespan
)


@app.get("/health")
async def health() -> dict:
    """What is loaded, and where. The API's readiness check reads this."""
    return {
        "status": "ok" if transcriber.loaded and speaker.loaded else "loading",
        "asr": {"model": settings.asr_model_id, "loaded": transcriber.loaded, "device": transcriber.device},
        "tts": {
            "engine": settings.tts_engine,
            "format": settings.audio_format,
            "model": settings.tts_model_id,
            "voice": settings.voice,
            "loaded": speaker.loaded,
            "device": speaker.device,
        },
    }


@app.post("/transcribe")
async def transcribe(audio: UploadFile = File(...), language: str = Form("")) -> dict:
    """One recording in, its text out."""
    data = await audio.read()

    if not data:
        raise HTTPException(status_code=422, detail="the recording is empty")

    try:
        async with asr_lock:
            result = await asyncio.to_thread(transcriber.transcribe, data, language)
    except AudioError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("transcription failed")
        raise HTTPException(status_code=500, detail=f"transcription failed: {exc}") from exc

    logger.info("transcribed %d bytes into %r", len(data), result.text[:80])

    return {"text": result.text, "language": result.language or language or settings.language}


@app.post("/speak")
async def speak(
    text: str = Body(..., embed=True),
    voice: str = Body("", embed=True),
    language: str = Body("", embed=True),
    format: str = Query("wav", pattern="^(wav|json)$"),
) -> Response:
    """
    Text in, a WAV out.

    `format=json` returns the audio base64-encoded alongside the time each word
    is spoken, which is what the page needs to follow the voice. A header would
    have been neater, but a long answer's timings run to kilobytes and headers
    are not the place for that.
    """
    if not text.strip():
        raise HTTPException(status_code=422, detail="there is nothing to say")

    try:
        async with tts_lock:
            audio, rate, words, media_type = await asyncio.to_thread(speaker.speak_with_timings, text, voice, language)
    except Exception as exc:
        logger.exception("synthesis failed")
        raise HTTPException(status_code=500, detail=f"synthesis failed: {exc}") from exc

    if format == "json":
        return JSONResponse({"audio": base64.b64encode(audio).decode(), "rate": rate, "words": words, "mime": media_type})

    return Response(content=audio, media_type=media_type, headers={"X-Sample-Rate": str(rate), "Cache-Control": "no-store"})


def main():
    setup_logging()
    logger.info("voice service on %s:%d", settings.host, settings.port)
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
