# ODS macOS Quickstart

> **Status: Supported**
>
> The macOS installer runs end-to-end on Apple Silicon. One command gives you a full local AI stack with Metal-accelerated inference.

---

## Prerequisites

- **Apple Silicon** Mac (M1, M2, M3, M4 or later)
- **Docker Desktop** 4.20+ installed and running
- **16 GB+ unified memory** recommended (8 GB minimum)
- **20 GB+ free disk space** (model + Docker images)

---

## Install

```bash
git clone https://github.com/Osmantic/ODS.git
cd ODS/ods
./install.sh
```

The installer will:

1. **Detect your chip** — identifies Apple Silicon variant and unified memory
2. **Pick the right model** — selects optimal model size for your RAM
3. **Download llama-server** — native macOS arm64 binary with Metal support
4. **Download your model** — GGUF file sized for your hardware
5. **Start Docker services** — chat UI, search, workflows, voice, and more
6. **Install OpenCode** — browser-based AI coding IDE on port 3003

**Estimated time:** 5–15 minutes depending on download speed.

---

## Open the UI

- **Chat UI:** http://localhost:3000
- **Dashboard:** http://localhost:3001
- **OpenCode (IDE):** http://localhost:3003

First user on the Chat UI becomes admin. Start chatting immediately.

---

## Architecture

```
macOS Host
  ├── llama-server (native, Metal GPU acceleration)
  ├── OpenCode web IDE (native, LaunchAgent)
  └── Docker Desktop
        ├── Open WebUI (port 3000)
        ├── Dashboard (port 3001)
        ├── LiteLLM API Gateway (port 4000)
        ├── n8n Workflows (port 5678)
        ├── Qdrant Vector DB (port 6333)
        ├── SearXNG Search (port 8888)
        ├── Perplexica Deep Research (port 3004)
        ├── Hermes Agent + auth proxy (port 9120)
        ├── OpenClaw Agents (port 7860, deprecated optional)
        ├── TEI Embeddings (port 8090)
        ├── Whisper STT (port 9000)
        ├── Kokoro TTS (port 8880)
        └── Privacy Shield (port 8085)
```

llama-server runs natively for full Metal GPU utilization. Docker containers reach it via `host.docker.internal:8080`.

---

## Managing Your Stack

```bash
./ods-macos.sh status          # Health checks for all services
./ods-macos.sh stop            # Stop everything
./ods-macos.sh start           # Start everything
./ods-macos.sh restart         # Restart everything
./ods-macos.sh logs llama-server   # Tail llama-server logs
```

---

## Hardware Tiers

The installer auto-selects the best model for your unified memory:

| Unified RAM | Tier | Model | Context |
|-------------|------|-------|---------|
| 8–24 GB | 1 | Qwen3.5 4B (Q4_K_M) | 16384 |
| 32 GB | 2 | Qwen3.5 9B (Q4_K_M) | 32768 |
| 48 GB | 3 | Qwen3 30B-A3B (MoE, Q4_K_M) | 32768 |
| 64+ GB | 4 | Qwen3 30B-A3B (MoE, Q4_K_M) | 131072 |

When Hermes is enabled, the macOS installer enforces a 64K minimum for the active
local context. If bootstrap mode is used, the first-run bootstrap model starts at
64K so the agent is usable while the full model downloads; after the swap, the
full model keeps the model selector's chosen context, which may be 128K on
larger tiers.

Override: `./install.sh --tier 3`

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| "Docker not running" | Start Docker Desktop, wait for whale icon in menu bar |
| "Not Apple Silicon" | Intel Macs are not supported — Apple Silicon (arm64) required |
| "Port in use" | Check for conflicting services: `lsof -i :8080` |
| llama-server crashes | Check memory — your model may be too large for available RAM |
| Docker services slow to start | First launch pulls images (~10 GB); subsequent starts are fast |
| TEI embeddings container restarts | Normal on arm64 — runs via Rosetta 2 emulation, may need a minute |

---

## Files & Locations

| What | Where |
|------|-------|
| Install directory | `~/ods/` |
| Config | `~/ods/.env` |
| Models | `~/ods/data/models/` |
| llama-server binary | `~/ods/llama-server/` |
| OpenCode | `~/.opencode/bin/opencode` |
| OpenCode config | `~/.config/opencode/opencode.json` |
| LaunchAgent (OpenCode) | `~/Library/LaunchAgents/com.ods.opencode-web.plist` |
| CLI tool | `~/ods/ods-macos.sh` |

---

## Known Limitations

- **ComfyUI (image generation)** is not available on macOS — requires NVIDIA GPU backend
- **Dashboard GPU info** shows "Unknown" — macOS Metal is not detected by the Linux-based dashboard container
- **TEI embeddings** runs under Rosetta 2 emulation (linux/amd64) — functional but slower than native

---

## Need Help?

- Support matrix: [SUPPORT-MATRIX.md](SUPPORT-MATRIX.md)
- General FAQ: [../FAQ.md](../FAQ.md)
- General troubleshooting: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

---

*Last updated: 2026-03-05*
