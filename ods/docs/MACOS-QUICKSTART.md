# ODS macOS Quickstart

> **Release channel:** the install commands on this page fetch development `main`, which is not signed. A signed-source path is staged in [Verified Install Preview](VERIFIED_INSTALL_PREVIEW.md); it is not active until the first eligible immutable release is published, and historical `v3.0.0` is not eligible.

> **Status: Supported**
>
> The macOS installer runs end-to-end on Apple Silicon. A fresh install starts with Core chat, Portal, the LiteLLM gateway, and Metal-accelerated inference; optional applications are selected separately.

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

For the public beta, add `--single-branch --branch public-beta` to the clone
command. The hosted installer and an unqualified clone select `main`, not the
beta. Back up existing configuration and data before updating. Existing native
Pixel installations use the managed native update/migration path; the base
installer intentionally stops instead of overwriting protected runtime state.

The installer will:

1. **Detect your chip** — identifies Apple Silicon variant and unified memory
2. **Pick the right model** — selects optimal model size for your RAM
3. **Download llama-server** — native macOS arm64 binary with Metal support
4. **Download your model** — GGUF file sized for your hardware
5. **Start Core Docker services** — Dashboard, LiteLLM gateway, and Portal support; Open WebUI, search, workflows, voice, and other extras are opt-in
6. **Activate native Pixel** — use the public bundled source, with the gateway and managed helpers on macOS and ingress/sandbox services in Docker
7. **Install OpenCode if selected** — browser-based AI coding IDE on port 3003

OpenCode is omitted on a fresh Core Only or noninteractive install. Select
Full Stack, pass `--opencode`, or pass `--all` to add it. An existing loaded ODS
OpenCode LaunchAgent stays selected on a normal rerun. `--no-opencode` disables
future login starts while keeping the binary, config, and current session.

Fresh interactive Enter and unattended installs select Core. With native Portal,
Pixel uses its keyless `parallel-free` search provider, so Core does not start
Token Spy or SearXNG. LiteLLM remains available to Portal and, when selected, Open WebUI.
Select Full Stack or pass `--recommended` to add the optional support bundle;
Perplexica and other selected search consumers also bring in SearXNG. Existing
installations keep their previous default posture, and the native Pixel guard
requires the managed update path for an already installed Portal.

**Estimated time:** 5–15 minutes depending on download speed.

---

## Open the UI

- **Portal chat (fresh Core default):** http://localhost:3001/pixel
- **Dashboard:** http://localhost:3001
- **Open WebUI (when selected or retained):** http://localhost:3000
- **OpenCode (IDE, when selected):** http://localhost:3003

Fresh Core installs do not start Open WebUI. An existing install retains its
selection; `--with-webui` adds it explicitly. ODS proxy requires Open WebUI.
The Dashboard and Portal use the installer-selected authentication and binding.

---

## Architecture

```
macOS Host
  ├── llama-server (native, Metal GPU acceleration)
  ├── Pixel gateway + managed host helpers (native)
  ├── OpenCode web IDE (optional native LaunchAgent)
  └── Docker Desktop
        ├── Open WebUI (optional, port 3000)
        ├── Dashboard (port 3001)
        ├── LiteLLM API Gateway (port 4000)
        ├── n8n Workflows (optional, port 5678)
        ├── Qdrant Vector DB (optional, port 6333)
        ├── SearXNG Search (optional, port 8888)
        ├── Perplexica Deep Research (optional, port 3004)
        ├── Pixel edge, ingress, sandbox and workspace preview
        ├── Hermes Agent + auth proxy (optional alternative)
        ├── OpenClaw Agents (port 7860, deprecated optional)
        ├── TEI Embeddings (optional, port 8090)
        ├── Whisper STT (optional, port 9000)
        ├── Kokoro TTS (optional, port 8880)
        └── Privacy Shield (optional, port 8085)
```

llama-server runs natively for full Metal GPU utilization. Docker containers reach it via `host.docker.internal:8080`.

Portal is enabled by default and disables Hermes while selected. A fresh install
can opt out with `--no-pixel`; this is not a way to disable an existing native
Portal installation. Portal needs neither a separate Lima VM nor access to a
private GitHub repository. Its source and verified install bundle are included
in ODS. See [PIXEL.md](PIXEL.md) for eligibility and authority boundaries.

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
| OpenCode, when selected | `~/.opencode/bin/opencode` |
| OpenCode config, when selected | `~/.config/opencode/opencode.json` |
| OpenCode LaunchAgent, when selected | `~/Library/LaunchAgents/com.ods.opencode-web.plist` |
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
