# Dream Host Agent API

The Dream Host Agent (`bin/dream-host-agent.py`) is a lightweight HTTP server that runs **on the host machine** (outside Docker). It allows the Dashboard API (running inside a container) to manage extension containers — starting, stopping, and fetching logs — without giving the container direct access to the Docker socket.

## Why It Exists

The Dashboard API runs inside a Docker container and cannot directly run `docker compose` commands on the host. The host agent bridges this gap: it listens on `DREAM_AGENT_BIND:DREAM_AGENT_PORT`, accepts authenticated requests from the Dashboard API, and executes Docker Compose operations on its behalf. This avoids mounting the Docker socket into the container (a significant security risk).

## How It Runs

| Platform | Mechanism |
|----------|-----------|
| Linux | systemd user service (`scripts/systemd/dream-host-agent.service`) |
| macOS | Started by the installer (`installers/macos/install-macos.sh`) |
| Windows | Started by the installer (`installers/windows/phases/07-devtools.ps1`, managed via `dream.ps1`) |

The agent is started during installation. macOS and Windows bind to `127.0.0.1` by default. Linux auto-detects the `dream-network` gateway so containers can reach the agent, falls back to the default Docker bridge gateway for partial/older installs, and then falls back to `127.0.0.1`. It does not bind to `0.0.0.0` unless `DREAM_AGENT_BIND` is explicitly set.

## Configuration

The agent reads its configuration from the `.env` file in the DreamServer install directory.

| Variable | Default | Description |
|----------|---------|-------------|
| `DREAM_AGENT_KEY` | *(none)* | API key for authenticating requests. Falls back to `DASHBOARD_API_KEY` if unset. |
| `DREAM_AGENT_BIND` | Platform-specific | Bind address. macOS/Windows default to `127.0.0.1`; Linux uses the `dream-network` gateway when detected, then the Docker bridge gateway, otherwise `127.0.0.1`. |
| `DREAM_AGENT_PORT` | `7710` | Port the agent listens on. |
| `GPU_BACKEND` | `nvidia` | Passed to `resolve-compose-stack.sh` when building compose flags. |
| `TIER` | `1` | Hardware tier, passed to compose stack resolution. |
| `DREAM_DATA_DIR` | `~/.dream-server` | Data directory root. |
| `DREAM_USER_EXTENSIONS_DIR` | `$DREAM_DATA_DIR/user-extensions` | Where user-installed extensions live. |

The agent also loads `config/core-service-ids.json` to determine which services are protected from management operations. If this file is missing, a hardcoded fallback list is used.

## Authentication

All mutation endpoints (`/v1/extension/*`) require a Bearer token:

```
Authorization: Bearer <DREAM_AGENT_KEY>
```

The agent uses constant-time comparison (`secrets.compare_digest`) to prevent timing attacks.

## Endpoints

### `GET /health`

Health check. No authentication required.

**Response (200):**
```json
{
  "status": "ok",
  "version": "1.0.0"
}
```

### `GET /v1/update/status`

Return the last host-agent managed update run status.

**Authentication:** Required

**Response (200):**
```json
{
  "status": "succeeded",
  "action": "update",
  "returncode": 0,
  "updated_at": "2026-05-18T18:00:00Z"
}
```

If no update has run, the response is `{ "status": "idle" }`.

### `POST /v1/update/check`, `POST /v1/update/backup`, `POST /v1/update/start`

Run `dream-update.sh` from the host-agent boundary. `check` and `backup` run synchronously and return script output. `start` launches the update in a background thread and writes `data/update-status.json` for polling.

**Authentication:** Required

**Request body:** optional JSON object. `backup` accepts an optional `backup_id`; otherwise the host agent generates one.

**Error responses:**
| Code | Condition |
|------|-----------|
| 401 | Missing Authorization header |
| 403 | Invalid API key |
| 409 | Update already running |
| 501 | Update system or usable Bash runtime not available |
| 504 | Update check/backup timed out |

### `POST /v1/extension/start`

Start an extension container. Runs `docker compose up -d <service_id>` using the full compose stack (resolved via `scripts/resolve-compose-stack.sh`). Before starting, the agent pre-creates any `./data/` volume directories declared in the extension's `compose.yaml`, with correct ownership based on the `user:` field.

**Authentication:** Required

**Request body:**
```json
{
  "service_id": "my-extension"
}
```

**Validation rules:**
- `service_id` must match `^[a-z0-9][a-z0-9_-]*$`
- Core services are rejected (403)
- Extension directory must exist in `user-extensions/` with a valid manifest

**Response (200):**
```json
{
  "status": "ok",
  "service_id": "my-extension",
  "action": "start"
}
```

**Error responses:**
| Code | Condition |
|------|-----------|
| 400 | Invalid `service_id` format or missing request body |
| 401 | Missing Authorization header |
| 403 | Invalid API key or core service |
| 404 | Extension not found (no directory or no manifest) |
| 409 | Operation already in progress for this service |
| 500 | Docker Compose operation failed |
| 503 | Docker Compose operation timed out (120s) |

### `POST /v1/extension/stop`

Stop an extension container. Runs `docker compose stop <service_id>`.

**Authentication:** Required

**Request/response format:** Same as `/v1/extension/start` with `"action": "stop"`.

### `POST /v1/extension/logs`

Fetch recent container logs. Uses `docker logs --tail N dream-<service_id>` directly (bypasses compose for speed).

**Authentication:** Required

**Request body:**
```json
{
  "service_id": "my-extension",
  "tail": 100
}
```

The `tail` parameter is clamped to 1-500 (defaults to 100).

**Response (200):**
```json
{
  "service_id": "my-extension",
  "logs": "...log output...",
  "lines": 100
}
```

If the container does not exist yet (e.g. image is still pulling), a 200 response is returned with a message instead of logs.

**Error responses:**
| Code | Condition |
|------|-----------|
| 503 | Log fetch timed out (5s) |
| 500 | Failed to fetch logs |

## Security Boundaries

The host agent is a **critical security boundary** because it can start and stop Docker containers on the host.

Protections in place:
- **Scoped network binding**: macOS/Windows bind to `127.0.0.1`; Linux binds to the `dream-network` gateway when detected so containers can reach the agent, with Docker bridge as a compatibility fallback. It does not bind to `0.0.0.0` unless explicitly configured.
- **API key auth**: All mutation endpoints require Bearer token authentication
- **Core service protection**: Core services (loaded from `config/core-service-ids.json` with hardcoded fallback) cannot be managed
- **Service ID validation**: Regex-validated, must map to an actual extension directory with a manifest
- **Per-service locking**: Prevents concurrent start+stop races on the same service via `threading.Lock`
- **Request size limit**: Request bodies capped at 4 KB
- **Subprocess timeout**: Docker operations time out after 120 seconds

## How the Dashboard API Calls It

The Dashboard API (`extensions/services/dashboard-api/routers/extensions.py`) communicates with the host agent via the `AGENT_URL` environment variable (constructed from `DREAM_AGENT_HOST` and `DREAM_AGENT_PORT` in `config.py`). It uses `DREAM_AGENT_KEY` for authentication. The connection flows through Docker's `host.docker.internal` DNS name by default, allowing the containerized API to reach the host-bound agent.

If the host agent is unreachable, mutation operations (install, enable, disable) still succeed at the file level but return `"restart_required": true` to signal that `dream restart` is needed.
