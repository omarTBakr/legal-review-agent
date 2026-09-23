# Voice service

Speech in and out for the [legal review agent](../README.md):
**Qwen3-ASR-0.6B** turns a spoken question into text, and
**oddadmix/Kokoro-7M-Distill** reads the answer back. Both are Apache 2.0.

The voice is a 7.5M-parameter distillation of Kokoro-82M: one English voice,
and on a GPU it produces speech about three hundred times faster than it takes
to listen to. Qwen3-TTS is still here behind `TTS_ENGINE=qwen` — it speaks ten
languages where Kokoro speaks one — but it is roughly four hundred times
slower, which is the whole story of this service's latency.

It is a project of its own, with its own lockfile and image, because torch and
transformers are gigabytes and nothing else here needs them. It knows nothing
about reviews, buckets or Temporal: the API calls it, the browser never does.

## Running

```bash
uv sync --project voice             # ~3 GB of wheels, once
uv run --directory voice main.py    # first start downloads ~2 GB of weights
```

```bash
curl localhost:8100/health
curl -F audio=@question.wav localhost:8100/transcribe
curl -X POST localhost:8100/speak -H 'Content-Type: application/json' \
  -d '{"text": "Liability is capped at twelve months of fees."}' -o answer.wav
```

`--directory`, not `--project`: both pick this project's environment, but only
`--directory` also changes the working directory, and without that `main.py`
resolves to the API's one in the repository root.

In Docker, with the GPU passed through:

```bash
docker compose -f voice/Docker/docker-compose.yml up -d --build
```

## Endpoints

| Endpoint | Does |
| --- | --- |
| `GET /health` | Which models are loaded, and on which device |
| `POST /transcribe` | `multipart/form-data` with `audio` (WAV) and an optional `language` → `{"text": ...}` |
| `POST /speak` | `{"text", "voice", "language"}` → `audio/wav` |

## Settings

| Variable | Default | Description |
| --- | --- | --- |
| `ASR_MODEL_ID` | `Qwen/Qwen3-ASR-0.6B-hf` | The recognition model |
| `TTS_ENGINE` | `kokoro` | `kokoro` (one English voice, very fast) or `qwen` (ten languages) |
| `TTS_MODEL_ID` | `oddadmix/Kokoro-7M-Distill` | The synthesis model |
| `TTS_DEVICE` | *(follows `VOICE_DEVICE`)* | Where the voice runs; it needs ~40 MB of VRAM |
| `VOICE_DEVICE` | `auto` | `cuda`, `cpu`, or `auto` |
| `VOICE_DTYPE` | `bfloat16` | Use `float16` on a GPU older than Ampere |
| `VOICE_QUANTIZATION` | `4bit` | `4bit`, `8bit` or `none` |
| `TTS_VOICE` | `af_msa` | Kokoro's style pack; the distilled model knows only this one |
| `TTS_LANGUAGE` | `English` | Language for both halves |
| `VOICE_HOST`, `VOICE_PORT` | `127.0.0.1`, `8100` | Where it listens |
| `VOICE_EAGER_LOAD` | `true` | Load both models at startup rather than on first use |
| `VOICE_WARM_UP` | `true` | Synthesise one short phrase at startup, to pay the compile cost there |
| `ASR_MAX_NEW_TOKENS` | `256` | Ceiling on the length of a transcription |

## Quantisation

`VOICE_QUANTIZATION` loads the weights through bitsandbytes: `4bit` (NF4 with
double quantisation and bfloat16 compute), `8bit`, or `none` for the weights as
they come.

What it does and does not buy, for models this size:

- **It saves VRAM, not time.** Both models unquantised need roughly 3 GB
  together, which already fits a modest card. 4-bit roughly halves that, which
  is worth having when something else shares the GPU — a local language model,
  or a desktop.
- **It costs some accuracy**, and that lands unevenly: a mistranscribed
  question gets a confidently wrong answer, while a slightly rougher voice is
  only a slightly rougher voice. If recognition starts mishearing, try `8bit`
  or `none` for the ASR before blaming the prompt.
- **A model that refuses to quantise is loaded as it is**, with a warning, and
  this is not hypothetical: Qwen3-TTS fails to quantise (`cannot pickle
  'dict_keys' object`) because it is a codec beside a language model, and comes
  up on full weights. The ASR model quantises fine. So in practice `4bit` today
  means a 4-bit recogniser at about 1.2 GB and a full-weight voice, and the
  service starts either way.

GGUF builds of both models exist and quantise further, but they need a
different runtime (llama.cpp rather than transformers), which is a bigger change
than this setting.

With `VOICE_DEVICE=cpu` everything works and synthesis is slow.

## Speed

Measured on an RTX 4060 laptop (8 GB), the same sentence each time, warm:

| Engine | Where | Time for 8.9s of speech | Against real time |
| --- | --- | --- | --- |
| Kokoro-7M | GPU | **31 ms** | 286× |
| Kokoro-7M | CPU | 220 ms | 41× |
| Qwen3-TTS-0.6B | GPU | ~7.2 s (11s of audio) | 1.5× |

And the recogniser, for completeness: about 2.3s for a 4-second clip, 4-bit on
the GPU.

Three things follow.

**The GPU is worth it for the voice, and nearly free.** Kokoro needs about
40 MB of VRAM, so it sits beside the recogniser without competing with it.
`TTS_DEVICE` picks; left alone it takes the GPU when there is one and the CPU
otherwise, where it is still forty times faster than real time.

**Streaming the audio is no longer worth building.** At 31 ms for a nine-second
answer, there is nothing to stream — the clip is ready before the first word
would have played. The pipeline does yield a chunk per sentence, so it could be
done, but the wait it would shave is now measured in milliseconds. Streaming
the *text* was the win, and that is already in place: the answer appears in the
browser as the model writes it.

**The first call is still slower than the rest** — torch compiles kernels on the
way through — which is what `VOICE_WARM_UP` is for: the service synthesises one
short phrase at startup so nobody's question pays for it.

`flash-attn` remains the next win for the *recogniser*, which still runs a
slower pure-PyTorch attention without it. It is not a dependency here because
it compiles against your CUDA toolkit and takes a long time to build:
`uv pip install flash-attn --no-build-isolation` inside `voice/.venv`.

## Notes

- **Why the `kokoro` package needs a patch.** This checkpoint sets the decoder
  widths that upstream hardcodes, and it stores weight normalisation in
  PyTorch's newer parametrized form. Its repository ships a patched copy of the
  whole package; `voice/kokoro_patch.py` applies the same two changes from code
  we can read instead, because a service that imports Python from a model
  repository at startup is a supply chain nobody reviewed. Getting the second
  change wrong does not raise — it produces confident noise — so the round trip
  below is part of the deal.
- **The round trip is the test.** Synthesise a sentence, transcribe it back
  with the recogniser, compare. That is what caught the silently-skipped
  weights: the audio was the right length and the right loudness, and the
  transcript was empty.
- **`af_msa`, not `af_heart`.** Both ship in the model's repository, but the
  student was distilled against `af_msa` and sounds worse on the other.
- **Qwen3-TTS uses CustomVoice, not Base.** CustomVoice has fixed voices built
  in. The Base model clones a voice from a few seconds of audio, which is a
  different feature with a consent question attached, and not one this service
  offers.
- **One generation at a time.** Each model sits behind a lock and runs in a
  thread: a single GPU does not share well, and a second concurrent `generate`
  mostly buys an out-of-memory error.
- **No authentication.** Do not publish port 8100. The API in front of it is
  what the browser talks to.
- **Audio is normalised here**, to 16 kHz mono, so callers can send whatever
  WAV they have. The browser already sends the right shape.

## Tests

```bash
uv run --directory voice pytest
```

They stub both models, so they need no GPU and no weights — but they do need
this project's dependencies installed, which is why the main suite does not
run them.
