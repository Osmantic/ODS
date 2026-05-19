# Aider

AI pair programming in your terminal. Edit code in your local git repository using natural language instructions, with support for multiple AI models.

## Requirements

- **GPU:** NVIDIA, AMD, or Apple Silicon
- **Dependencies:** None

## Enable / Disable

```bash
dream enable aider
dream disable aider
```

Your data is preserved when disabling. To re-enable later: `dream enable aider`

## Access

Aider is a CLI tool with no web interface. Run it via Docker:

```bash
# Start an interactive session
docker compose run --rm aider

# Edit specific files
docker compose run --rm aider src/main.py src/utils.py

# With a specific model
docker compose run --rm aider --model ollama/llama3 src/
```

## First-Time Setup

1. Enable the service: `dream enable aider`
2. Place your projects in `./data/aider/` to make them available
3. Run `docker compose run --rm aider` to start a session

### Using with Local Models

```bash
docker compose run --rm aider \
  --model openai/local-model \
  --openai-api-base http://host.docker.internal:8000/v1 \
  src/
```

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `OPENAI_API_KEY` | OpenAI API key | _(optional)_ |
| `ANTHROPIC_API_KEY` | Anthropic API key | _(optional)_ |
