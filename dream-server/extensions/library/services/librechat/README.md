# LibreChat

Enhanced ChatGPT clone with multi-LLM support, agents, RAG, and file uploads. Supports OpenAI, Anthropic, Google, Azure, Groq, Mistral, OpenRouter, and custom endpoints.

## Requirements

- **GPU:** NVIDIA, AMD, or Apple Silicon
- **Dependencies:** None

## Enable / Disable

```bash
dream enable librechat
dream disable librechat
```

Your data is preserved when disabling. To re-enable later: `dream enable librechat`

## Access

- **URL:** `http://localhost:3080`

## First-Time Setup

1. Enable the service: `dream enable librechat`
2. Open `http://localhost:3080`
3. Create an account on first launch
4. Optionally connect to Dream Server's LLM via Settings: add custom endpoint `http://llama-server:8080/v1`

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `JWT_SECRET` | JWT signing secret for sessions (auto-generated) | _(required)_ |
| `JWT_REFRESH_SECRET` | JWT refresh token secret (auto-generated) | _(required)_ |
| `LIBRECHAT_MONGO_PASSWORD` | MongoDB root password (auto-generated) | _(required)_ |
| `LIBRECHAT_MEILI_KEY` | Meilisearch master key (auto-generated) | _(required)_ |
| `CREDS_KEY` | AES-128 encryption key for stored credentials (auto-generated) | _(optional)_ |
| `CREDS_IV` | AES initialization vector for credential encryption (auto-generated) | _(optional)_ |
