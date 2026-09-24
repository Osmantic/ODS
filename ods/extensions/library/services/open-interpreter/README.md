# Open Interpreter

Let LLMs run code locally (Python, JavaScript, Shell). Provides a ChatGPT-like interface that can control Chrome, create/edit files, analyze datasets, and more — fully local, no API costs.

## Requirements

- **GPU:** CPU only — no GPU required
- **Dependencies:** None

## Apple Silicon (M1/M2/M3) note

This extension is configured `platform: linux/amd64` because some of its Python dependencies don't have native ARM64 wheels. On Apple Silicon, Docker Desktop runs it under QEMU x86_64 emulation — expect noticeably slower builds (typically 5–10x) and reduced runtime CPU performance (typically 2–5x) compared to native ARM64 hosts. Functional but not recommended for active iterative work on Apple Silicon.

## Enable / Disable

```bash
ods enable open-interpreter
ods disable open-interpreter
```

Your data is preserved when disabling. To re-enable later: `ods enable open-interpreter`

## Access

- **API:** `http://localhost:7805`

## First-Time Setup

1. Enable the service: `ods enable open-interpreter`
2. Use the REST API or run interactively via CLI

### API Usage

```bash
# Health check
curl http://localhost:7805/health

# Chat (non-streaming)
curl -X POST http://localhost:7805/chat \
  -H "Authorization: Bearer $OPEN_INTERPRETER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"message": "What OS are we running?", "stream": false}'
```

Set `OPEN_INTERPRETER_API_KEY` in this terminal to the extension's configured
HTTP key. This key authenticates callers of the wrapper; it is separate from
the model provider's credential.

### Model connection

ODS supplies the runtime origin through `LLM_API_URL` and its API path through
`LLM_API_BASE_PATH` (`/v1` for llama-server, `/api/v1` for a direct Lemonade
runtime). The wrapper combines them when the URL has no path. If the URL already
contains an API path, that explicit path wins; trailing slashes are normalized.

Set `OPEN_INTERPRETER_MODEL` to the provider's model identifier, including the
`openai/` prefix for OpenAI-compatible servers. Set
`OPEN_INTERPRETER_LLM_API_KEY` to that provider's credential when authentication
is required. For an authenticated ODS LiteLLM gateway, use the configured
gateway key and a model alias it actually exposes; the placeholder defaults
`openai/x` and `fake_key` only suit a direct local server that accepts them.
Recreate the extension after changing these values. Both HTTP chat routes pass
the same connection settings to Open Interpreter, with auto-run still disabled
unless explicitly enabled.

### CLI Usage

The Compose service starts the HTTP wrapper. For the upstream interactive CLI,
run the image's `interpreter` executable explicitly. From the ODS install directory:

```bash
# Interactive session
docker compose --project-directory . -f data/user-extensions/open-interpreter/compose.yaml \
  run --rm --entrypoint interpreter open-interpreter

# Single command
docker compose --project-directory . -f data/user-extensions/open-interpreter/compose.yaml \
  run --rm --entrypoint interpreter open-interpreter "Describe the files in this project"
```

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `OPEN_INTERPRETER_API_KEY` | API key for authentication | _(required)_ |
| `OPEN_INTERPRETER_MODEL` | Model identifier passed to Open Interpreter | `openai/x` |
| `OPEN_INTERPRETER_LLM_API_KEY` | Outbound model provider credential | `fake_key` |

The upstream CLI has its own model/configuration options; the wrapper-specific
connection settings above apply to `/chat` and `/chat/stream`.
