# Dashboard API Development

This guide covers how to iterate on `extensions/services/dashboard-api/` (the FastAPI backend that powers the Dashboard UI) without losing your sanity.

## TL;DR

- Editing `.py` files under `extensions/services/dashboard-api/` on the host **does not** reload the running `ods-dashboard-api` container. The image bakes a copy into `/app/` at build time.
- For frontend work, keep the installed stack running and start **Vite on port 3003** with the installed `DASHBOARD_API_KEY` in its server environment. API requests use port 3002; previews and downloads use the installed dashboard on port 3001. The key never reaches browser JavaScript.
- For Python changes, rebuild the API image or use the scoped native uvicorn workflow below. Native uvicorn requires host-reachable service endpoints; it does not automatically inherit the Docker network.
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

## Frontend Workflow: Vite + the Installed Stack

Keep the `dashboard`, `dashboard-api`, and Pixel services running. Vite hot-reloads frontend changes on **http://127.0.0.1:3003**:

```text
browser -> Vite :3003 -> /api/*           -> installed API :3002
                     -> /pixel-preview/* -> installed nginx :3001 -> pixel-edge
```

The second route is necessary for actual file downloads, preview frames, project files, change summaries, and source review. Pixel's private edge listener stays inside the Docker network. Vite preserves the preview response's content type, digest, sandbox and cross-origin headers.

```bash
cd ods/extensions/services/dashboard
npm ci
DASHBOARD_API_KEY="$(grep '^DASHBOARD_API_KEY=' /path/to/ods/.env | cut -d= -f2-)" \
  npm run dev
```

Use the installed key in the Vite **server process environment**. Do not use a `VITE_` variable, client code, or a browser field. With no key, Vite warns and leaves API authentication to the caller; protected API requests retain their normal authentication error. Production builds do not require the key.

The API proxy preserves browser Host and Origin and adds its server-held key only for admitted local requests. Talk and dashboard login/logout retain their own cookie/session authentication and receive no admin key. The preview proxy adds no key: the installed nginx gate supplies its own credential after its normal checks.

Development access trusts processes on this computer. Vite binds to `127.0.0.1` and refuses to silently change ports. Keep port 3001 available to the installed dashboard; select another development port explicitly if 3003 is occupied (`npm run dev -- --port 3004`). Remote listeners/targets, HTTPS, middleware mode, forwarding headers, and proxy redirects/websockets are rejected for these routes. API requests and source review require the same origin; immutable preview assets retain the cross-origin reads needed by their opaque sandbox. Preview requests allow only GET/HEAD and validated snapshot paths, so path normalization cannot reach a different nginx route.

Use the production dashboard's authenticated access when connecting from another device. Python edits in this checkout still do not update the installed API image.

## Backend Workflow: Native uvicorn for Host-Reachable Routes

For backend iteration, stop **only `dashboard-api`** to free port 3002. Keep `dashboard` running on 3001 for Vite's preview/download route. Browse through Vite on 3003: the production dashboard's `/api` route resolves the container service and cannot reach your native uvicorn process.

```bash
cd /path/to/ods
docker compose stop dashboard-api

cd extensions/services/dashboard-api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
ODS_INSTALL_DIR=/path/to/ods \
ODS_DATA_DIR=/path/to/ods/data \
ODS_AGENT_HOST=127.0.0.1 \
ODS_AGENT_PORT=7710 \
DASHBOARD_API_KEY="$(grep '^DASHBOARD_API_KEY=' /path/to/ods/.env | cut -d= -f2-)" \
ODS_AGENT_KEY="$(grep '^ODS_AGENT_KEY=' /path/to/ods/.env | cut -d= -f2-)" \
  uvicorn main:app --host 127.0.0.1 --port 3002 --reload
```

Start Vite in a separate terminal as above, with the same dashboard key. uvicorn reloads Python edits in its working directory. `ODS_AGENT_HOST=127.0.0.1` replaces the container-only default `host.docker.internal`; the host agent must actually be reachable at the configured port. Dashboard and host-agent keys are independent.

This native backend workflow covers routes whose dependencies are reachable from the host. **It is not a complete native Pixel development environment.** Pixel routes require the private `pixel-edge:9595` Docker endpoint, and native uvicorn does not acquire Docker DNS or network access. Use the installed backend for the full frontend journey, or rebuild the API container when testing changes to those backend routes. Do not expose the private edge listener or weaken its endpoint validation to make a development test pass.

Mirror other environment settings only as required by the route under test. When finished, stop native uvicorn and restore the installed API:

```bash
cd /path/to/ods
docker compose start dashboard-api
```

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
