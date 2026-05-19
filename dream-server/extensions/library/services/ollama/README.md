# Ollama

Simple way to run open-source LLMs locally with GPU acceleration. Provides a dedicated Ollama instance integrated with Dream Server's stack for use by other extensions.

## Requirements

- **GPU:** NVIDIA or AMD (min 8 GB VRAM)
- **Dependencies:** None

## Enable / Disable

```bash
dream enable ollama
dream disable ollama
```

Your data is preserved when disabling. To re-enable later: `dream enable ollama`

## Access

- **API:** `http://localhost:7804`

## First-Time Setup

1. Enable the service: `dream enable ollama`
2. Pull a model: access the API or use a connected UI (Open WebUI, AnythingLLM, etc.)

### API Endpoints

```bash
# Generate text
curl http://localhost:7804/api/generate -d '{"model": "llama3", "prompt": "Hello!"}'

# Chat
curl http://localhost:7804/api/chat -d '{"model": "llama3", "messages": [{"role": "user", "content": "Hi"}]}'

# List models
curl http://localhost:7804/api/tags
```

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `OLLAMA_MODEL` | Default model to load on startup | _(optional)_ |
