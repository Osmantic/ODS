# open-webui

Optional chat interface for ODS

## Overview

Dashboard/Portal provides the standard ODS chat and agent experience. Open WebUI is an optional additional chat interface backed by the configured ODS model route. It can integrate with SearXNG, ComfyUI, Whisper, and Kokoro when those services are installed.

When selected, Open WebUI is served at `http://localhost:3000` and communicates through the configured OpenAI-compatible model gateway.

## Features

- **Chat interface**: Multi-turn conversations with the local LLM
- **Web search**: Integrated SearXNG metasearch for grounded answers
- **Image generation**: ComfyUI backend using SDXL Lightning 4-step (1024×1024)
- **Voice input**: Speech-to-text via Whisper (`/v1/audio/transcriptions`)
- **Voice output**: Text-to-speech via Kokoro (`/v1/audio/speech`)
- **User authentication**: Opens directly on loopback; enabled by default for LAN/network deployments
- **Document Q&A**: Upload files and chat with their contents (requires Qdrant)
- **Model selection**: Switch between models at runtime

## Configuration

Environment variables (set in `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBUI_SECRET` | *(required)* | Session signing key — generate with `openssl rand -hex 32` |
| `WEBUI_PORT` | `3000` | External port (maps to internal 8080) |
| `WEBUI_AUTH` | conditional | `false` for loopback-only installs; `true` for LAN-bound or ODS proxy installs |
| `TIMEZONE` | `UTC` | System timezone for timestamps |
| `LLM_API_URL` | `http://llama-server:8080` | LLM backend URL (internal Docker hostname) |

### Voice configuration

Open WebUI connects to Whisper and Kokoro automatically using internal Docker hostnames. To change the default models:

| Variable (in `docker-compose.nvidia.yml`) | Default | Description |
|-------------------------------------------|---------|-------------|
| `AUDIO_STT_MODEL` (NVIDIA overlay) | `deepdml/faster-whisper-large-v3-turbo-ct2` | Whisper model on NVIDIA |
| `AUDIO_STT_MODEL` (base) | `Systran/faster-whisper-base` | Whisper model on AMD/CPU |
| `AUDIO_TTS_VOICE` | `af_heart` | Kokoro voice name |

## Architecture

```
Browser
  │
  ▼
Open WebUI (:3000)
  ├── LLM Chat ──────────────▶ llama-server:8080 (OpenAI API)
  ├── Web Search ─────────────▶ SearXNG:8080
  ├── Image Generation ───────▶ ComfyUI:8188
  ├── Speech-to-Text ─────────▶ Whisper:8000
  └── Text-to-Speech ─────────▶ Kokoro TTS:8880
```

## Data Persistence

User accounts, chat history, and uploaded documents are stored in `data/open-webui/`. This volume is mounted at `/app/backend/data` inside the container.

## Settings come from ODS

ODS runs Open WebUI with `ENABLE_PERSISTENT_CONFIG=false`, so Open WebUI reads
its settings from the environment ODS gives it each time it starts: `.env`, the
compose overlays for your hardware and mode, Pixel, and the Dashboard Settings
page. Switching modes, rotating keys or changing a setting there takes effect
when Open WebUI restarts.

Changes made in Open WebUI's **Admin Panel > Settings** (connections,
documents, web search, audio, images, interface, default user permissions)
last until Open WebUI restarts. Make lasting changes in ODS instead. Accounts,
groups, chats, workspace models, knowledge, prompts, tools and functions are
stored in the database as usual. Settings saved in the Admin Panel before
Open WebUI 0.11.4 stay in the database but are no longer used.

## Accounts

Signup is closed (`ENABLE_SIGNUP=false`). With sign-in on (`WEBUI_AUTH=true`),
Open WebUI still lets the first account sign up on a new install and makes it
the administrator; the administrator adds everyone else in **Admin Panel >
Users**. Without sign-in (loopback installs), Open WebUI uses its built-in
`admin@localhost` account; see [Quick LAN Access](../../../SECURITY.md#quick-lan-access)
before turning sign-in on.

## Upgrades and backups

Each version of Open WebUI migrates its database when it first starts, and the
migration cannot be undone. Before Open WebUI starts, `openwebui-prepare.py`
(run by `openwebui-entrypoint.sh`) does the following:

- When the Open WebUI version differs from the one recorded in
  `data/open-webui/.ods-open-webui-version`, it copies `webui.db` to
  `data/open-webui/ods-backups/<UTC time>-open-webui-<previous version>.db` and
  keeps the two newest copies. Installs updated from ODS releases that shipped
  Open WebUI 0.7.2 have no recorded version, so their copy is named
  `…-open-webui-unrecorded.db`. Open WebUI is not running at that point, so the
  copy is consistent, and any write-ahead log is folded into the single file.
- It refuses to start Open WebUI, and says why in `ods logs open-webui`, when
  starting would damage the database:
  - two accounts have email addresses that differ only in case, which Open
    WebUI's unique-email migration rejects partway through;
  - the image is older than the version that last used the data;
  - the database was migrated by a newer Open WebUI.

The first start of a new version takes longer: Open WebUI migrates before it
answers, and on a large install with many chats that can take many minutes.
The chat UI shows as unhealthy meanwhile. Let it finish.

To go back to the version a copy was made for:

1. `ods stop open-webui`
2. Restore the copy and remove the files that belong to the newer database
   (they are owned by the container, so Linux may need `sudo`):

   ```bash
   cd ~/ods/data/open-webui
   cp ods-backups/<copy>.db webui.db
   rm -f webui.db-wal webui.db-shm webui.db-journal .ods-open-webui-version
   ```

3. Pin the previous image in `~/ods/docker-compose.base.yml`: set the
   `open-webui` `image:` line to that version. For a `…-unrecorded.db` copy from
   an install that ran Open WebUI 0.7.2, that is
   `ghcr.io/open-webui/open-webui:v0.7.2@sha256:16d9a3615b45f14a0c89f7ad7a3bf151f923ed32c2e68f9204eb17d1ce40774b`.
4. `ods start open-webui`

Updating ODS's code (rerunning the installer from a newer release, or the
Dashboard's Update) restores the newer pin, so hold updates until the cause is
fixed. `ods rollback` restores ODS's configuration, not this database.

**Dashboard updates.** The Dashboard's Update button runs `ods-update.sh`. If a
service is not running 120 seconds after the update, it restores ODS's previous
configuration and restarts the stack, but not Open WebUI's database. Open WebUI
keeps running while it migrates, so a slow migration does not trigger this, and
when ODS refuses to start a new version the database is untouched and the
previous version runs normally. A migration that fails does trigger it: the
previous Open WebUI then starts on a partly migrated database. If that happens,
stop Open WebUI and restore the copy as above.

## Tool calling

Since Open WebUI 0.10, chats and models that have not chosen a function-calling
mode use **Native** tool calling, which relies on the model's own tool support.
If a local model does not handle tools well, set **Function Calling** to
**Legacy** for that model, or for all models in the default model parameters.

## First Use

1. If a lean installation omitted Open WebUI, choose **Add Open WebUI** in Dashboard → Extensions Library. Linux and macOS support this action; it downloads only the WebUI image and reuses retained chat data. On macOS, rerunning the installer with `--with-webui` also selects it.
2. Open `http://localhost:3000` in your browser.
3. Start chatting; a normal loopback-only ODS install does not require an account. On a LAN/network deployment, create the first admin account when prompted.

## Troubleshooting

**Open WebUI not loading:**
```bash
docker compose ps open-webui
docker compose logs open-webui
```

**"Connection refused" to LLM:**
- Verify llama-server is healthy: `curl http://localhost:8080/health`
- Check `LLM_API_URL` in `.env`

**Voice input not working:**
- Confirm Whisper is running: `curl http://localhost:9000/health`
- Browser must have microphone permission

**Image generation not available:**
- Requires ComfyUI service to be running
- Enable via `ods enable comfyui`

**Authentication issues:**
- Local-only installs should have `BIND_ADDRESS=127.0.0.1` and `WEBUI_AUTH=false`
- LAN, ODS proxy, VPN, and public deployments should set `WEBUI_AUTH=true`
- After changing `WEBUI_AUTH`, recreate Open WebUI with `ods restart open-webui`
- To reset admin password: remove `data/open-webui/` (loses all chat history)

## Files

- `manifest.yaml` — Service metadata and feature definitions
- `openwebui-entrypoint.sh` — Runs `openwebui-prepare.py`, then the image's own start command
- `openwebui-prepare.py` — Pre-start backup and upgrade checks (see [Upgrades and backups](#upgrades-and-backups))
- `tests/` — Contracts for the pre-start step and its compose wiring
- `../../../tests/test-open-webui-live.sh` — Read-only check of a running install, for the fleet harness

## License

The ODS integration and the upstream application have different terms.
The pinned Open WebUI v0.11.4 image uses the
[Open WebUI license](https://github.com/open-webui/open-webui/blob/v0.11.4/LICENSE),
including its branding conditions; code from before those conditions keeps the
terms in its [LICENSE_HISTORY](https://github.com/open-webui/open-webui/blob/v0.11.4/LICENSE_HISTORY).
See [BRANDING.md](BRANDING.md) before making branding changes. ODS's Apache
license does not relicense Open WebUI.
