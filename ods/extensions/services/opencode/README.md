# OpenCode

AI-powered coding assistant accessible via browser in ODS

## Overview

OpenCode provides a browser-based AI coding environment that integrates with your local LLM. It gives you code completion, explanation, refactoring, and generation capabilities through a web interface — no IDE plugin required. Unlike other ODS services, OpenCode runs directly on the host system (not in Docker) as a systemd-managed process.

## Features

- **Browser-based IDE**: Access your AI coding assistant from any browser on your local network
- **Local LLM backend**: All code and queries stay on your machine — connects to llama-server for inference when available
- **Code completion**: Context-aware completions for multiple languages
- **Explanation and refactoring**: Ask the LLM to explain, refactor, or debug your code
- **No IDE installation required**: Works entirely in the browser

## Deployment Type

OpenCode runs as a **host-level systemd service** (`type: host-systemd`), not as a Docker container. This means:

- It is managed by systemd on the host, not by Docker Compose
- There is no `container_name` — use systemd commands to manage the process
- LLM features require either OpenCode's own backend or llama-server to be running (either satisfies the requirement)

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENCODE_PORT` | 3003 | Port for the OpenCode web interface |

> **Note:** LLM-powered features (completion, explanation) need either OpenCode's own backend or `llama-server` running. OpenCode itself starts independently.

## Accessing OpenCode

Once running, open your browser and navigate to:

```
http://localhost:3003
```

## Requirements

| Requirement | Details |
|-------------|---------|
| GPU VRAM | 8 GB minimum (for LLM inference via llama-server) |
| LLM service | `llama-server` must be running |
| GPU backends | NVIDIA, AMD |

## Architecture

```
┌──────────┐  HTTP :3003   ┌──────────────┐
│ Browser  │──────────────▶│  OpenCode    │
│          │◀──────────────│ (host daemon)│
└──────────┘               └──────┬───────┘
                                  │ LLM inference
                                  ▼
                           ┌──────────────┐
                           │ llama-server │
                           │   (Docker)   │
                           └──────────────┘
```

## Files

- `manifest.yaml` — Service metadata (port, health endpoint, GPU backends, feature definition)

## Troubleshooting

**OpenCode not accessible on port 3003:**

Since OpenCode is a host systemd service, check its status with systemd:
```bash
# Check service status
systemctl status opencode

# View logs
journalctl -u opencode --follow

# Restart the service
systemctl restart opencode
```

**LLM inference not working:**
- Verify llama-server is running: `docker compose -f ods/docker-compose.base.yml ps llama-server`
- Ensure sufficient VRAM (minimum 8 GB) is available

**Port 3003 already in use:**
- Check what is using the port: `lsof -i :3003`
- Update the port in your ODS configuration and restart the service
