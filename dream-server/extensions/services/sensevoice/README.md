# SenseVoice (STT)

OpenAI-compatible speech-to-text powered by [FunASR](https://github.com/modelscope/FunASR)'s
SenseVoiceSmall model. A lightweight, fast alternative to the Whisper service.

- **Endpoint:** `POST /v1/audio/transcriptions` (multipart `file`, optional `language` form field, default `auto`)
- **Health:** `GET /health`
- **In-container port:** 8000 · **Host port:** `SENSEVOICE_PORT` (default `9100`)
- **Model:** `iic/SenseVoiceSmall` (+ FSMN VAD), cached under `./data/sensevoice`
- **Upload limit:** `SENSEVOICE_MAX_AUDIO_BYTES` (default 25 MB); larger uploads get `413`
- **OpenAI compatibility:** the common `model`, `response_format`, and
  `temperature` form fields are accepted and ignored — SenseVoice always returns
  `{"text": ...}`

## Using it

Point any OpenAI-STT-compatible client at the host endpoint:

```bash
curl -s http://127.0.0.1:9100/v1/audio/transcriptions \
  -F file=@clip.wav -F language=auto
# -> {"text": "..."}
```

SenseVoice is **additive**: enabling it does not change Dream Server's default
voice wiring (Open WebUI / Hermes continue to use Whisper). To use SenseVoice as
the STT backend for an app, point that app's OpenAI STT base URL at
`http://sensevoice:8000/v1`.

> **Scope:** this service provides the STT *endpoint*. It does not add
> dashboard-level STT backend selection (choosing SenseVoice vs. Whisper from the
> UI) — that remains a separate, future change.

## First run / model readiness

The SenseVoiceSmall + VAD models (~900 MB) download on the **first transcription**,
not at startup. As a result:

- the container can start and `GET /health` can report healthy **before** the
  model is loaded;
- the **first** `POST /v1/audio/transcriptions` can take several minutes while the
  model downloads (watch `docker logs -f dream-sensevoice`);
- subsequent calls are fast — the model is cached under `./data/sensevoice` and
  persists across restarts.

So a healthy `/health` means "the service is up", not "the model is ready".

## Acceleration

The service builds a small custom image (FastAPI + FunASR `AutoModel`, no vllm),
so it runs on CPU everywhere and on NVIDIA GPUs via `SENSEVOICE_DEVICE=cuda`
(set automatically by `compose.nvidia.yaml`). AMD and Apple Silicon run CPU-mode
on the base compose.

Multi-GPU pinning (assigning SenseVoice to a specific GPU on multi-GPU hosts) is
not wired into the GPU-assignment pipeline yet and is left as a follow-up.
