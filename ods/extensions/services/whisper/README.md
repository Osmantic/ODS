# whisper

Speech-to-text service for ODS

## Overview

The Whisper service provides real-time audio transcription using [speaches](https://github.com/speaches-ai/speaches), a high-performance Whisper server with an OpenAI-compatible API. It is used by Open WebUI for voice input and can be called directly from any application that supports the OpenAI Audio Transcriptions endpoint.

The service includes a custom VAD (Voice Activity Detection) patch that is applied at container startup to tune silence detection for conversational AI use cases.

## Features

- **OpenAI-compatible API**: Drop-in replacement for `POST /v1/audio/transcriptions`
- **Multiple Whisper models**: Supports tiny through large-v3-turbo via HuggingFace model IDs
- **Voice Activity Detection (VAD)**: Patched at startup with tuned parameters for conversation
- **Model caching**: Models are downloaded once and cached in `data/whisper/`
- **TTL-based model eviction**: Unused models unloaded after 24 hours (`WHISPER__TTL=86400`)
- **CPU and GPU backends**: CPU image by default; GPU overlay available for NVIDIA

## Configuration

Environment variables (set in `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `WHISPER_PORT` | `9000` | External port (maps to internal 8000) |
| `WHISPER__TTL` | `86400` | Model time-to-live in seconds (unload after inactivity) |

The Whisper model is selected per-request using the `model` field in the API call. Open WebUI uses:
- AMD/CPU: `Systran/faster-whisper-base`
- NVIDIA: `deepdml/faster-whisper-large-v3-turbo-ct2`

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/v1/audio/transcriptions` | Transcribe audio (OpenAI format) |
| `GET` | `/v1/models` | List available/cached models |

### Example

```bash
curl http://localhost:9000/v1/audio/transcriptions \
  -F "file=@audio.wav" \
  -F "model=Systran/faster-whisper-base"
```

## VAD Patch

The `docker-entrypoint.sh` script applies a VAD tuning patch to the upstream speaches STT router at container startup. The patch is idempotent — it detects an existing `ODS_PATCHED` marker and skips re-application on container restart.

VAD parameters injected:

| Parameter | Value | Effect |
|-----------|-------|--------|
| `threshold` | `0.3` | Speech probability threshold |
| `min_silence_duration_ms` | `400` | Silence gap before segment end |
| `min_speech_duration_ms` | `50` | Minimum speech segment length |
| `speech_pad_ms` | `200` | Padding added around speech segments |

## Data Persistence

Downloaded models are cached in `data/whisper/` (mounted at `/home/ubuntu/.cache/huggingface/hub` inside the container).

## Model Downloads

**Important:** Speaches does NOT auto-download models on transcription requests — it returns `404 Model ... is not installed locally` if the model isn't already cached.

The installer pre-downloads the STT model on all platforms:
- **Linux:** Phase 12 (`installers/phases/12-health.sh`)
- **macOS:** `installers/macos/install-macos.sh` (post-health check)
- **Windows:** `installers/windows/install-windows.ps1` (post-health check)

The model to download is controlled by the `AUDIO_STT_MODEL` variable in `.env`:
- **NVIDIA default:** `deepdml/faster-whisper-large-v3-turbo-ct2` (~1.5GB)
- **macOS / AMD / CPU default:** `Systran/faster-whisper-base` (~130MB)

Edit `AUDIO_STT_MODEL` in `.env` to use a different model, then reinstall or manually run the recovery command below. Open WebUI automatically picks up the same `AUDIO_STT_MODEL` value so transcription requests always match the cached model.

### Recovery

If the pre-download fails (network issue, models API not ready in time), run the recovery command manually:

```bash
# Linux / macOS — the model ID from your .env:
curl --max-time 3600 -X POST http://localhost:9000/v1/models/Systran%2Ffaster-whisper-base

# For NVIDIA turbo:
curl --max-time 3600 -X POST http://localhost:9000/v1/models/deepdml%2Ffaster-whisper-large-v3-turbo-ct2

# Windows (PowerShell):
Invoke-WebRequest -Method POST -Uri 'http://localhost:9000/v1/models/Systran%2Ffaster-whisper-base' -TimeoutSec 3600

# Wait 2-5 minutes (depending on model size + network). Verify it cached:
curl http://localhost:9000/v1/models
```

## Files

- `compose.yaml` — Service definition
- `manifest.yaml` — Service metadata and feature requirements
- `docker-entrypoint.sh` — VAD patch + server startup script

## Troubleshooting

**Service not starting:**
```bash
docker compose ps whisper
docker compose logs whisper
```

**Transcription errors or poor quality:**
- Try a larger model: set `model=Systran/faster-whisper-small` or `model=deepdml/faster-whisper-large-v3-turbo-ct2` in the request
- Check available disk space for model download

**VAD patch not applying:**
- The patch targets a specific line in the speaches source; if the upstream image changes, the patch may be skipped silently
- Check logs for `[ods-whisper] WARNING: Target pattern not found`

**Model download failed during install / STT returns 404:**
- Speaches does not auto-download on transcription — run the recovery `curl -X POST ...` command from the **Model Downloads** section above
- Downloads come from HuggingFace; allow 2-5 minutes for the ~130MB base model (or ~1.5GB for NVIDIA turbo)
- Check container logs: `docker compose logs -f whisper`

**Open WebUI not using Whisper:**
- Verify `AUDIO_STT_ENGINE=openai` and `AUDIO_STT_OPENAI_API_BASE_URL=http://whisper:8000/v1` in the open-webui environment

## License

Part of ODS — Local AI Infrastructure
