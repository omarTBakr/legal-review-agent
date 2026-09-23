# Runs the voice service: Qwen3-ASR and Qwen3-TTS behind HTTP.
#
# The build context is this directory's parent (voice/), not the project root:
# this service imports nothing from the rest of the project, which is the whole
# point of it having its own pyproject.
#
#   docker build -f voice/Docker/Dockerfile -t legal-review-agent-voice voice
#   docker run --rm --gpus all -p 8100:8100 -v huggingface:/home/voice/.cache/huggingface legal-review-agent-voice
#
# The default voice (Kokoro, 7.5M parameters) needs about 40 MB of VRAM and is
# faster than real time on a CPU, so --gpus is worth having but not required.
#
# The CUDA runtime image is what makes a GPU usable inside the container; on a
# machine with no GPU, set VOICE_DEVICE=cpu and expect synthesis to be slow.

FROM nvidia/cuda:12.6.2-cudnn-runtime-ubuntu24.04 AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app \
    # the models land here; mount a volume so they are downloaded once
    HF_HOME=/home/voice/.cache/huggingface

# libsndfile is what soundfile reads and writes WAVs with; espeak-ng is the
# fallback the Kokoro text frontend reaches for on words its dictionary does
# not have; python3 is the interpreter uv builds the environment against
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv libsndfile1 espeak-ng \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 voice \
    && mkdir -p /app \
    && chown -R voice:voice /app /home/voice

WORKDIR /app
USER voice

# dependencies first: this layer is gigabytes and changes only when the lock does
COPY --chown=voice:voice pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=voice:voice *.py ./

ENV VOICE_HOST=0.0.0.0 \
    VOICE_PORT=8100

EXPOSE 8100

CMD ["python", "main.py"]
