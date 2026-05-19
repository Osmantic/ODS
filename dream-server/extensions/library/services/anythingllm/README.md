# AnythingLLM

All-in-one AI productivity tool for RAG chat with documents. Built-in vector database, supports multiple LLM providers, and runs entirely on-device for privacy.

## Requirements

- **GPU:** NVIDIA or AMD
- **Dependencies:** Ollama

## Enable / Disable

```bash
dream enable anythingllm
dream disable anythingllm
```

Your data is preserved when disabling. To re-enable later: `dream enable anythingllm`

## Access

- **URL:** `http://localhost:7800`

## First-Time Setup

1. Enable the service: `dream enable anythingllm`
2. Open `http://localhost:7800`
3. Create an admin account on first launch
4. Create a workspace and upload documents to start chatting

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `ANYTHINGLLM_JWT_SECRET` | JWT secret for session tokens (auto-generated) | _(required)_ |
| `ANYTHINGLLM_AUTH_TOKEN` | API authentication token (auto-generated) | _(required)_ |

### LLM Providers

By default AnythingLLM uses Dream Server's Ollama extension. You can also configure OpenAI, Anthropic, Azure, or LocalAI as the LLM backend through the UI settings.
