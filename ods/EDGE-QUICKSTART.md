# ODS — Edge Quickstart

> **Status: Planned — Not Yet Available.**
>
> This guide describes a future edge deployment mode (Pi 5 / Mac Mini / small CPU-only hosts).
> The referenced `docker-compose.edge.yml` does not exist yet. **Do not follow edge-mode instructions** — they will not work today.

## What to use today (supported)

If you want a lightweight setup on CPU-only machines (no dedicated GPU), use **cloud mode**:

- Install: `./install-core.sh --cloud` (from repo root)
- Full install guide: [QUICKSTART.md](QUICKSTART.md)
- macOS Apple Silicon: [docs/MACOS-QUICKSTART.md](docs/MACOS-QUICKSTART.md)
- Documentation index: [docs/README.md](docs/README.md)

## Edge mode (planned)

When edge mode is implemented, this document will be updated to include:

- A dedicated compose file: `docker-compose.edge.yml`
- A “small footprint” default stack
- Optional profiles for voice/workflows (where hardware allows)

### Target constraints (draft)

- **RAM:** 8GB minimum (16GB recommended)
- **Storage:** 20GB free for models and data
- **Docker:** 24.0+ with Compose v2

### Draft ports (may change)

| Service | Port |
|---------|------|
| Open WebUI | 3000 |
| Ollama API | 11434 |

---

*Part of the ODS project — Mission 5 (ODS) + Mission 6 (Min Hardware)*
