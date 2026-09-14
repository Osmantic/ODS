# tts

Text-to-speech service for ODS (powered by Kokoro)

## Overview

The TTS service provides high-quality neural text-to-speech synthesis using [Kokoro FastAPI](https://github.com/remsky/kokoro-fastapi), a fast and lightweight TTS server with an OpenAI-compatible API. It is used by Open WebUI to read AI responses aloud and can be called directly from any application that supports the OpenAI Audio Speech endpoint.

## Features

- **OpenAI-compatible API**: Drop-in replacement for `POST /v1/audio/speech`
- **Multiple voices**: Multiple voice presets available; default is `af_heart`
- **Concurrent requests**: 2 Uvicorn workers for parallel synthesis
- **Low latency**: CPU-based inference with fast Kokoro neural TTS model
- **OpenAI format**: Compatible with any client that uses `openai.audio.speech.create()`

## Configuration

Environment variables (set in `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `TTS_PORT` | `8880` | External port (maps to internal 8880) |
| `DEFAULT_VOICE` | `af_heart` | Default voice preset |
| `UVICORN_WORKERS` | `2` | Number of worker processes |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/v1/audio/speech` | Synthesize speech from text (OpenAI format) |
| `GET` | `/v1/models` | List available TTS models |
| `GET` | `/v1/voices` | List available voice presets |

### CLI speech files

The shared Linux/WSL CLI can list voices and create a WAV file without an SDK:

```bash
ods tts voices
ods tts speak 'Hello from ODS.' greeting.wav --voice af_heart
printf 'Read this local report.\n' | ods tts speak - report.wav
```

Enable the `tts` service first. The CLI reads quoted `TTS_PORT` from the installed
`.env`, sends text only to the loopback Kokoro endpoint, and preserves paths
relative to the caller's directory. It does not play audio or start the service.
`--voice` defaults to `af_heart` and accepts voice combinations supported by Kokoro.
Text is limited to 10,000 characters and responses to 64 MiB; the HTTP socket
timeout is five minutes. HTTP errors, redirects, invalid or truncated WAV data
fail visibly without publishing an output file.

The output must be a new path in an existing directory. Completed WAV files are
published atomically without replacing another writer's file, using a same-directory
hard link (the destination filesystem must support hard links). Files are private
to the invoking user by default. This command requires Python 3; the separately
generated native macOS installer CLI does not gain these subcommands.

### Example

```bash
curl http://localhost:8880/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model": "kokoro", "input": "Hello, welcome to ODS!", "voice": "af_heart"}' \
  --output speech.mp3
```

### Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8880/v1",
    api_key="not-needed"
)

response = client.audio.speech.create(
    model="kokoro",
    voice="af_heart",
    input="Hello from ODS!"
)
response.stream_to_file("output.mp3")
```

## Open WebUI Integration

Open WebUI is pre-configured to use this service:

```
AUDIO_TTS_ENGINE=openai
AUDIO_TTS_OPENAI_API_BASE_URL=http://tts:8880/v1
AUDIO_TTS_MODEL=kokoro
AUDIO_TTS_VOICE=af_heart
```

These values are set in `docker-compose.base.yml` and require no manual configuration.

## Files

- `compose.yaml` — Service definition
- `manifest.yaml` — Service metadata

## Troubleshooting

**Service not starting:**
```bash
docker compose ps tts
docker compose logs tts
```

**No audio output in Open WebUI:**
- Verify TTS is running: `curl http://localhost:8880/health`
- Check browser audio permissions and output device

**Slow synthesis:**
- The CPU image processes speech on CPU; synthesis takes 1–5 seconds depending on text length
- Ensure the container has sufficient CPU allocation (`TTS_CPU_LIMIT` in `.env`; the installer auto-caps it to Docker's visible CPU count)

**Wrong voice:**
- List available voices: `curl http://localhost:8880/v1/voices`
- Change `DEFAULT_VOICE` in `.env` or specify `voice` per-request

## License

Part of ODS — Local AI Infrastructure
