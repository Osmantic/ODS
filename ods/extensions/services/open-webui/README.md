# open-webui

Primary chat interface for ODS

## Overview

Open WebUI is the main user-facing web application bundled with ODS. It provides a full-featured chat UI backed by the local llama-server LLM, with integrated web search via SearXNG, image generation via ComfyUI, and voice input/output via Whisper (STT) and Kokoro (TTS).

Open WebUI is served at `http://localhost:3000` and communicates with llama-server through the OpenAI-compatible API.

## Features

- **Chat interface**: Multi-turn conversations with the local LLM
- **Web search**: Integrated SearXNG metasearch for grounded answers
- **Image generation**: ComfyUI backend using SDXL Lightning 4-step (1024×1024)
- **Voice input**: Speech-to-text via Whisper (`/v1/audio/transcriptions`)
- **Voice output**: Text-to-speech via Kokoro (`/v1/audio/speech`)
- **User authentication**: Optional login system (enabled by default)
- **Document Q&A**: Upload files and chat with their contents (requires Qdrant)
- **Model selection**: Switch between models at runtime

## Configuration

Environment variables (set in `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBUI_SECRET` | *(required)* | Session signing key — generate with `openssl rand -hex 32` |
| `WEBUI_PORT` | `3000` | External port (maps to internal 8080) |
| `WEBUI_AUTH` | `true` | Enable user authentication (`true`/`false`) |
| `TIMEZONE` | `UTC` | System timezone for timestamps |
| `LLM_API_URL` | `http://llama-server:8080` | LLM backend URL (internal Docker hostname) |

### Voice configuration

Open WebUI connects to Whisper and Kokoro automatically using internal Docker hostnames. To change the default models:

| Variable (in `docker-compose.nvidia.yml`) | Default | Description |
|-------------------------------------------|---------|-------------|
| `AUDIO_STT_MODEL` (NVIDIA overlay) | `deepdml/faster-whisper-large-v3-turbo-ct2` | Whisper model on NVIDIA |
| `AUDIO_STT_MODEL` (base) | `Systran/faster-whisper-base` | Whisper model on AMD/CPU |
| `AUDIO_TTS_VOICE` | `af_heart` | Kokoro voice name |

## Architecture

```
Browser
  │
  ▼
Open WebUI (:3000)
  ├── LLM Chat ──────────────▶ llama-server:8080 (OpenAI API)
  ├── Web Search ─────────────▶ SearXNG:8080
  ├── Image Generation ───────▶ ComfyUI:8188
  ├── Speech-to-Text ─────────▶ Whisper:8000
  └── Text-to-Speech ─────────▶ Kokoro TTS:8880
```

## Data Persistence

User accounts, chat history, and uploaded documents are stored in `data/open-webui/`. This volume is mounted at `/app/backend/data` inside the container.

## First Use

1. Open `http://localhost:3000` in your browser
2. Create an admin account (first user automatically becomes admin)
3. Start chatting — the LLM is ready when llama-server reports healthy

## Troubleshooting

**Open WebUI not loading:**
```bash
docker compose ps open-webui
docker compose logs open-webui
```

**"Connection refused" to LLM:**
- Verify llama-server is healthy: `curl http://localhost:8080/health`
- Check `LLM_API_URL` in `.env`

**Voice input not working:**
- Confirm Whisper is running: `curl http://localhost:9000/health`
- Browser must have microphone permission

**Image generation not available:**
- Requires ComfyUI service to be running
- Enable via `ods enable comfyui`

**Authentication issues:**
- To disable auth (local use only): set `WEBUI_AUTH=false` in `.env` and restart
- To reset admin password: remove `data/open-webui/` (loses all chat history)

## Files

- `manifest.yaml` — Service metadata and feature definitions

## License

Part of ODS — Local AI Infrastructure
