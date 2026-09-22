# Aider

AI pair programming in your terminal. Edit code in your local git repository using natural language instructions, with support for multiple AI models.

## Requirements

- **GPU:** NVIDIA, AMD, or Apple Silicon
- **Dependencies:** None

## Enable / Disable

```bash
ods enable aider
ods disable aider
```

Your data is preserved when disabling. To re-enable later: `ods enable aider`

## Access

Aider is a CLI tool with no web interface. From the ODS install directory,
use the installed launcher below. It selects the extension's Compose file and
the image's actual Aider executable. The normal enable/install container only
prints a help message and exits successfully.

Project paths are relative to `data/aider`, mounted at `/app` inside Aider.
The launcher reads the ODS `.env`; quote filenames containing spaces as usual.
For example, `bash data/user-extensions/aider/run.sh "my project/main.py"`.
`--version` checks the CLI without contacting a model provider.

```bash
# Start an interactive session
bash data/user-extensions/aider/run.sh

# Edit specific files
bash data/user-extensions/aider/run.sh src/main.py src/utils.py

# With a specific model
bash data/user-extensions/aider/run.sh --model ollama/llama3 src/
```

## First-Time Setup

1. Enable the service: `ods enable aider`
2. Place your projects in `./data/aider/` to make them available
3. Run `bash data/user-extensions/aider/run.sh` to start a session

### Using with the Local llama-server

Use your loaded model name and configured API key where required. For a different
backend, use its OpenAI-compatible API base in place of the example below.

```bash
bash data/user-extensions/aider/run.sh \
  --model openai/local-model \
  --openai-api-base http://llama-server:8080/v1 \
  src/
```

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `OPENAI_API_KEY` | OpenAI API key | _(optional)_ |
| `ANTHROPIC_API_KEY` | Anthropic API key | _(optional)_ |
