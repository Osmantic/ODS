# LocalAI

OpenAI-compatible local inference API. Run LLMs, generate images, audio, video, and clone voices — all through the same API format as OpenAI, entirely on your own hardware.

## Requirements

- **GPU:** NVIDIA or AMD (min 4 GB VRAM; CPU fallback available)
- **Dependencies:** llama-server

## Enable / Disable

```bash
dream enable localai
dream disable localai
```

Your data is preserved when disabling. To re-enable later: `dream enable localai`

## Access

- **URL:** `http://127.0.0.1:7803`

## First-Time Setup

1. Enable the service: `dream enable localai`
2. Configure your first model — see below. LocalAI ships with no models pre-configured.
3. Open `http://127.0.0.1:7803` to access the web interface
4. Use the OpenAI-compatible API for integration with existing applications

### Configure your first model

LocalAI ships with **no models pre-configured**. This is intentional — DreamServer favors explicit consent over silently downloading multi-gigabyte weights on first boot. Until you add at least one model, the API will report an empty model list and chat/image/audio endpoints will return errors.

You have two ways to add a model:

- **Use the built-in gallery browser.** Open `http://127.0.0.1:7803` and browse the model gallery from the web UI. Pick a model and LocalAI will download and register it for you.
- **Drop a YAML model config into `data/localai/builds/`.** This directory is mounted at `/builds` inside the container and is where LocalAI loads model definitions from. See LocalAI's model-config docs for the YAML schema: <https://localai.io/docs/getting-started/customize-model/>.

You can browse the upstream LocalAI model gallery at <https://localai.io/gallery/> for ready-made model entries you can copy into `data/localai/builds/`.

### API Usage

```bash
# Chat completion (OpenAI-compatible)
curl -X POST http://127.0.0.1:7803/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4", "messages": [{"role": "user", "content": "Hello!"}]}'

# List available models
curl http://127.0.0.1:7803/v1/models
```
