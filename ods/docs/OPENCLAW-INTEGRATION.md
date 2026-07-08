# OpenClaw Integration

> ⚠️ **DEPRECATED as of 2026-05-12.** OpenClaw is replaced by [Hermes Agent](HERMES.md) as ODS's default agent. New installs no longer enable OpenClaw by default. This extension stays installable via `--openclaw` / `ods enable openclaw` for one release cycle, then is removed.
>
> **Migrating an existing install:** see [MIGRATION-OPENCLAW-TO-HERMES.md](MIGRATION-OPENCLAW-TO-HERMES.md). The two agents do not share storage; the migration is a clean break — your OpenClaw data stays on disk untouched but Hermes starts fresh.

Run OpenClaw with your ODS for AI agent capabilities.

## What OpenClaw Adds

- **Tool use** — File operations, shell commands, web browsing
- **Sub-agents** — Spawn parallel workers on your local GPU
- **Channels** — Connect to Discord, Telegram, Signal, etc.
- **Memory** — Persistent context across sessions
- **Cron** — Scheduled tasks and reminders

## Quick Setup

### Option 1: Add to Docker Compose

OpenClaw is already included in `docker-compose.base.yml`. To add it manually:

```yaml
  openclaw:
    image: ghcr.io/openclaw/openclaw:latest
    container_name: ods-openclaw
    restart: unless-stopped
    environment:
      - OPENCLAW_CONFIG=/config/openclaw.json
    volumes:
      - ./config/openclaw:/config
      - ./data/openclaw:/data
    ports:
      - "7860:18789"
    depends_on:
      llama-server:
        condition: service_healthy
    profiles:
      - openclaw
```

### Option 2: Run Standalone

```bash
# Install OpenClaw
npm install -g @openclaw/openclaw

# Copy config
cp config/openclaw/openclaw.json.example ~/.openclaw/openclaw.json

# Edit config to point to your llama-server
# Change baseUrl if llama-server is on different host
vim ~/.openclaw/openclaw.json

# Start
openclaw gateway start
```

## Configuration

Key settings in `openclaw.json`:

```json
{
  "agent": {
    "model": "local-llama/qwen2.5-32b-instruct"
  },
  "providers": {
    "local-llama": {
      "type": "openai-compatible",
      "baseUrl": "http://llama-server:8080/v1",  // or http://localhost:8080/v1
      "apiKey": "not-needed"
    }
  },
  "subagent": {
    "enabled": true,
    "maxConcurrent": 10  // Adjust based on your GPU
  }
}
```

## Using OpenClaw

### CLI Mode

```bash
# Interactive chat
openclaw chat

# One-shot query
openclaw ask "Summarize the files in ./docs"

# With specific model
openclaw ask --model local-llama/qwen2.5-32b-instruct "Hello"
```

### Gateway Mode (For Channels)

```bash
# Start gateway daemon
openclaw gateway start

# Check status
openclaw gateway status

# View logs
openclaw gateway logs
```

### Web UI

When gateway is running, visit: **http://localhost:7860**

## Sub-Agent Scaling

Your ODS can run multiple parallel sub-agents:

| VRAM | Max Concurrent | Notes |
|------|----------------|-------|
| 16GB | 5-8 | Shared context |
| 24GB | 10-15 | Good parallelism |
| 48GB+ | 20-40 | Heavy workloads |

Configure in `openclaw.json`:
```json
{
  "subagent": {
    "maxConcurrent": 10,
    "timeoutSeconds": 300
  }
}
```

## Connecting Channels

### Discord

```json
{
  "channels": {
    "discord": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "guilds": ["GUILD_ID"]
    }
  }
}
```

### Telegram

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN"
    }
  }
}
```

## Memory & Persistence

OpenClaw stores:
- **Workspace** — `./data/openclaw/workspace/`
- **Memory** — `./data/openclaw/memory/`
- **Sessions** — `./data/openclaw/sessions/`

Mount these volumes for persistence:
```yaml
volumes:
  - ./data/openclaw:/data
```

## Troubleshooting

### "Model not found"

Verify llama-server is running and model name matches:
```bash
curl http://localhost:8080/v1/models
```

### Sub-agents timing out

Increase timeout or reduce concurrent limit:
```json
{
  "subagent": {
    "timeoutSeconds": 600,
    "maxConcurrent": 5
  }
}
```

### Out of VRAM

Reduce sub-agent concurrency or context window.

---

*For more, see [OpenClaw docs](https://docs.openclaw.ai)*
