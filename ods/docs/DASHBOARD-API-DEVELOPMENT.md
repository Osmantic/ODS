# Dashboard API Development

This guide covers how to iterate on `extensions/services/dashboard-api/` (the FastAPI backend that powers the Dashboard UI) without losing your sanity.

## TL;DR

- Editing `.py` files under `extensions/services/dashboard-api/` on the host **does not** reload the running `ods-dashboard-api` container. The image bakes a copy into `/app/` at build time.
- Recommended dev workflow: stop **both** the `dashboard` and `dashboard-api` Docker containers to free host ports 3001 and 3002, then run **Vite dev server** for the dashboard frontend (`npm run dev` on port 3001) plus **native uvicorn** with `--reload` for the API (port 3002). Vite's built-in `/api` proxy already points at `localhost:3002`, so the two host processes wire up automatically. The rest of the compose stack (llama-server, host-agent, etc.) stays running.
- Only rebuild the image (or `docker cp` as a stop-gap) when you actually need to ship the change.

## The Trap

The Dockerfile (`extensions/services/dashboard-api/Dockerfile`) does this at build time:

```dockerfile
WORKDIR /app
COPY main.py config.py models.py security.py gpu.py helpers.py agent_monitor.py user_extensions.py ./
COPY routers/ routers/
...
CMD uvicorn main:app --host 0.0.0.0 --port ${DASHBOARD_API_PORT}
```

Two consequences:

1. The Python source lives at `/app/` inside the image. uvicorn is launched from that `WORKDIR` and imports `main:app` from `/app/main.py`. There is no live link back to the host filesystem.
2. The compose service mounts the host repo at `/ods` (read-only) — see `docker-compose.base.yml`:

   ```yaml
   volumes:
     - ./scripts:/ods/scripts:ro
     - ./config:/ods/config:ro
     - ./extensions:/ods/extensions:ro
     - ./.env:/ods/.env:ro
     ...
   ```

   That bind-mount exists so the API can **read manifests, scripts, and config** at runtime. It is not on Python's import path. Editing `ods/extensions/services/dashboard-api/routers/setup.py` on the host updates `/ods/extensions/services/dashboard-api/routers/setup.py` inside the container — a path nothing imports from. The running uvicorn keeps serving the baked `/app/routers/setup.py`.

If you only test by editing host files and reloading the dashboard, your changes silently no-op. This has bitten contributors before; don't waste an afternoon on it.

## Recommended Workflow: Vite + Native uvicorn

The clean dev story is to swap the **dashboard** and **dashboard-api** Docker containers out for host-side processes that share the same ports, leaving the rest of the compose stack (llama-server, host-agent, etc.) running. Vite's built-in proxy at `extensions/services/dashboard/vite.config.js` already routes `/api` to `http://localhost:3002`:

```js
// vite.config.js (already in repo, no change needed)
server: {
  port: 3001,
  proxy: { '/api': { target: 'http://localhost:3002', changeOrigin: true } }
}
```

So the dev proxy chain becomes:

```
browser → http://localhost:3001 (Vite dev)  → /api/* → http://localhost:3002 (host uvicorn)
```

### Why both containers must be stopped

`docker-compose.base.yml` binds host port 3001 to the `dashboard` (nginx) container and host port 3002 to the `dashboard-api` (uvicorn) container — both with `${BIND_ADDRESS:-127.0.0.1}` defaults. If either is running, the matching host-side process refuses to bind.

Stopping only `dashboard-api` (an earlier draft of this guide's recommendation) is **wrong**: the Docker `dashboard` container's nginx config hardcodes `proxy_pass http://dashboard-api:3002` (see `extensions/services/dashboard/nginx.conf`). Without the Docker dashboard-api running, every `/api/*` request the Docker nginx proxies returns 502 Bad Gateway. The correct path is to stop **both** containers and replace them with Vite + uvicorn running on the same host ports.

You can keep the rest of the stack (`llama-server`, `ods-host-agent`, `qdrant`, etc.) running. Only the two dashboard containers need to step aside.

### One-time setup

```bash
# API venv
cd ods/extensions/services/dashboard-api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Frontend deps
cd ../dashboard
npm install
```

### Each session

Stop the two dashboard containers so host ports 3001 and 3002 are free:

```bash
cd /path/to/ods
docker compose stop dashboard dashboard-api
```

Terminal 1 — Vite dev server (claims host port 3001):

```bash
cd ods/extensions/services/dashboard
npm run dev
```

Terminal 2 — native uvicorn (claims host port 3002). Set `ODS_INSTALL_DIR` to the path of your installed ODS repo (the same path the container sees as `/ods`):

```bash
cd ods/extensions/services/dashboard-api
source .venv/bin/activate
ODS_INSTALL_DIR=/path/to/ods \
ODS_DATA_DIR=/path/to/ods/data \
ODS_AGENT_HOST=127.0.0.1 \
ODS_AGENT_PORT=7710 \
DASHBOARD_API_KEY="$(grep ^DASHBOARD_API_KEY /path/to/ods/.env | cut -d= -f2-)" \
ODS_AGENT_KEY="$(grep ^ODS_AGENT_KEY /path/to/ods/.env | cut -d= -f2-)" \
  uvicorn main:app --host 127.0.0.1 --port 3002 --reload
```

`ODS_AGENT_HOST=127.0.0.1` is required when running uvicorn natively on the host — `config.py` defaults it to `host.docker.internal`, which Docker Desktop only injects inside containers. Without this override, any host-agent-backed dashboard-api route (extensions install/start, model download, `/v1/env/update`, etc.) fails DNS resolution and returns 502.

uvicorn's `--reload` uses `watchfiles` and picks up edits to any `.py` file under the working directory. Vite hot-reloads JSX/CSS as you edit.

Browse to **http://localhost:3001** (Vite). Backend traffic flows through Vite's proxy to your host uvicorn; the rest of the stack (host-agent on 7710, llama-server on 8080, etc.) is reached the same way it was via the Docker dashboard.

When you're done, restart the production containers:

```bash
docker compose start dashboard dashboard-api
```

Mirror any other env vars your code path reads (`OLLAMA_URL`, `LLM_MODEL`, `KOKORO_URL`, etc.) from the values in `.env`. A small wrapper script that exports the relevant subset is a reasonable thing to keep locally. Note that `DASHBOARD_API_KEY` and `ODS_AGENT_KEY` are independent secrets (since PR #979 they are no longer aliased) — if you only export one, calls in the direction that needs the other will return 401.

## Stop-gap: `docker cp`

If running native uvicorn isn't an option (you only have Docker, you're debugging something that depends on the container's exact env, etc.), copy the file straight into `/app/` and restart the service:

```bash
cd ods/extensions/services/dashboard-api
docker cp routers/setup.py ods-dashboard-api:/app/routers/setup.py
docker compose restart dashboard-api
```

This survives until the next image rebuild, after which the baked copy wins again. It is a survivable stop-gap for hot patches; it is not a substitute for committing the change.

## Permanent Change: Rebuild the Image

Anything you want to ship has to make it into the image:

```bash
docker compose build dashboard-api
docker compose up -d dashboard-api
```

This is what running installs pick up the next time they pull or rebuild.

## Why Not Just Bind-Mount the Source? (Option B, rejected)

Mapping `./extensions/services/dashboard-api:/app` would give "live edits in the container" at the cost of:

1. **Writable mount required.** Python writes `__pycache__/` next to source files. A read-only bind-mount makes every import fail; a read-write mount lets the container scribble bytecode back into the contributor's working tree.
2. **Breaks standalone use of the prebuilt image.** Anyone running `docker run <dashboard-api-image>` (or starting the image without our compose file) would get an empty `/app/` because the bind-mount isn't there. The image would no longer be self-contained.
3. **Divergent dev and prod images.** Dev would import from the bind-mount; prod from the baked copy. Bugs that depend on file layout, permissions, or pycache state would only show up on one path.

Native uvicorn gets the same iteration speed without any of those drawbacks.

## Why Not a `--reload` Compose Overlay? (Option C, rejected)

The obvious alternative is a `docker-compose.dev.yml` that overrides the dashboard-api command:

```yaml
# rejected
services:
  dashboard-api:
    command: uvicorn main:app --host 0.0.0.0 --port 3002 --reload --reload-dir /ods/extensions/services/dashboard-api
```

The trap: `WORKDIR` is still `/app`. uvicorn launches in `/app`, imports `main:app` from `/app/main.py`, and watches `/ods/extensions/services/dashboard-api/` for file changes. The watcher fires correctly, uvicorn reloads — and re-imports the **same baked `/app/` code**. The reload appears to work and the change appears to do nothing.

To actually pick up host edits, the overlay has to also change `WORKDIR` (or wrap the command in `bash -c "cd /ods/extensions/services/dashboard-api && uvicorn ..."`) and ensure Python's import path points at the bind-mount. At that point you've effectively re-implemented Option B inside an overlay, with all of its drawbacks plus a confusing dev-only command. Running uvicorn on the host is shorter and clearer.

## Cross-Platform Notes

- **macOS (Apple Silicon / Intel).** Native uvicorn works perfectly. Watchfiles uses FSEvents; reloads are fast and reliable. No osxfs involvement because the source is on the host filesystem to begin with.
- **Linux.** Native uvicorn works perfectly. Watchfiles uses inotify.
- **Windows / WSL2.** Run the WSL2 instance (uvicorn) with the repo on the **WSL2 filesystem** (e.g. `~/ODS`). Editing on `/mnt/c/...` (the Windows NTFS mount) makes inotify watches unreliable — file change events drop or arrive late. Keep the working tree inside the WSL2 home directory, edit it via VS Code's WSL remote, and reload triggers behave the same as on Linux.

## See Also

- [EXTENSIONS.md](EXTENSIONS.md) — adding a new service extension.
- [HOST-AGENT-API.md](HOST-AGENT-API.md) — the host-agent endpoints the dashboard-api calls into.
- `extensions/services/dashboard-api/Dockerfile` — the source of truth for what gets baked into `/app/`.
- `docker-compose.base.yml` — the dashboard-api service definition (volumes, env, `extra_hosts`).
