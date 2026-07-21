# ODS Host Agent API

The ODS Host Agent (`bin/ods-host-agent.py`) is a lightweight HTTP server that runs **on the host machine** (outside Docker). It allows the Dashboard API (running inside a container) to manage extension containers — starting, stopping, and fetching logs — without giving the container direct access to the Docker socket.

## Why It Exists

The Dashboard API runs inside a Docker container and cannot directly run `docker compose` commands on the host. The host agent bridges this gap: it listens on `ODS_AGENT_BIND:ODS_AGENT_PORT`, accepts authenticated requests from the Dashboard API, and executes Docker Compose operations on its behalf. This avoids mounting the Docker socket into the container (a significant security risk).

## How It Runs

| Platform | Mechanism |
|----------|-----------|
| Linux | systemd user service (`scripts/systemd/ods-host-agent.service`) |
| macOS | Started by the installer (`installers/macos/install-macos.sh`) |
| Windows | Started by the installer (`installers/windows/phases/07-devtools.ps1`, managed via `ods.ps1`) |

The agent is started during installation. macOS and Windows bind to `127.0.0.1` by default. Linux auto-detects the `ods-network` gateway so containers can reach the agent, falls back to the default Docker bridge gateway for partial/older installs, and then falls back to `127.0.0.1`. It does not bind to `0.0.0.0` unless `ODS_AGENT_BIND` is explicitly set.

## Configuration

The agent reads its configuration from the `.env` file in the ODS install directory.

| Variable | Default | Description |
|----------|---------|-------------|
| `ODS_AGENT_KEY` | *(none)* | API key for authenticating requests. Falls back to `DASHBOARD_API_KEY` if unset. |
| `ODS_AGENT_BIND` | Platform-specific | Bind address. macOS/Windows default to `127.0.0.1`; Linux uses the `ods-network` gateway when detected, then the Docker bridge gateway, otherwise `127.0.0.1`. |
| `ODS_AGENT_PORT` | `7710` | Port the agent listens on. |
| `GPU_BACKEND` | `nvidia` | Passed to `resolve-compose-stack.sh` when building compose flags. |
| `AMD_INFERENCE_PORT` | `8080` | Validated loopback port used for Windows host-native Lemonade telemetry. |
| `LEMONADE_API_KEY` | *(none)* | Optional bearer token sent only to the configured loopback Lemonade endpoint. |
| `TIER` | `1` | Hardware tier, passed to compose stack resolution. |
| `ODS_DATA_DIR` | `~/.ods` | Data directory root. |
| `ODS_USER_EXTENSIONS_DIR` | `$ODS_DATA_DIR/user-extensions` | Where user-installed extensions live. |

The agent also loads `config/core-service-ids.json` to determine which services are protected from management operations. If this file is missing, a hardcoded fallback list is used.

## Authentication

All `/v1/*` endpoints require a Bearer token. The unversioned `/health`
endpoint is the only unauthenticated route:

```
Authorization: Bearer <ODS_AGENT_KEY>
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

### `GET /v1/gpu/metrics`

Return host GPU identity and metrics that are unavailable inside Docker
Desktop. The current collector is active on Windows and uses DXGI adapter
identity plus Windows GPU performance counters. Unsupported sensors are
represented by `null` and an explicit `*_available: false` flag.

**Authentication:** Required

**Response (200):**
```json
{
  "schema_version": "ods.host-gpu-metrics.v1",
  "name": "AMD Radeon RX 9070 XT",
  "gpu_count": 1,
  "memory_type": "discrete",
  "memory_total_mb": 16188,
  "memory_used_mb": 4449,
  "memory_usage_available": true,
  "utilization_percent": 12,
  "utilization_available": true,
  "temperature_c": null,
  "temperature_available": false,
  "source": "windows-dxgi-performance-counters",
  "sampled_at": "2026-07-20T22:00:00+00:00",
  "gpus": [
    {
      "index": 0,
      "uuid": "luid-00000000-0000abcd",
      "name": "AMD Radeon RX 9070 XT",
      "memory_type": "discrete",
      "memory_total_mb": 16188,
      "memory_used_mb": 4449,
      "memory_usage_available": true,
      "utilization_percent": 12,
      "utilization_available": true,
      "temperature_c": null,
      "temperature_available": false
    }
  ]
}
```

The top-level fields are a backward-compatible aggregate. `gpus` preserves
per-adapter identity on multi-GPU hosts. Hybrid AMD systems exclude an
integrated display adapter when discrete Radeon adapters are present. Unified
memory APUs report `memory_type: "unified"` and include observed shared-memory
use; unavailable counters remain explicit rather than being presented as zero.

Returns 503 when the platform collector or required counters are unavailable.

### `GET /v1/llm/status`

Bridge health and last-request statistics from host-native Lemonade over the
validated loopback port. ODS accepts the compatibility `/api/v1` routes and
the current `/v1` routes during runtime upgrades. Raw runtime payloads and
local checkpoint paths are not returned.

**Authentication:** Required

**Response (200):**
```json
{
  "schema_version": "ods.host-llm-status.v1",
  "health": {
    "status": "ok",
    "version": "10.0.0",
    "model_loaded": "extra.Model.gguf"
  },
  "stats": {
    "time_to_first_token": 0.2,
    "tokens_per_second": 42.5,
    "input_tokens": 32,
    "output_tokens": 64,
    "prompt_tokens": 32
  },
  "source": "windows-loopback",
  "sampled_at": "2026-07-20T22:00:00+00:00"
}
```

The statistics object describes Lemonade's most recently completed request; it
is not a cumulative counter. It is `null` when health is available but optional
request statistics are not. Runtime responses are bounded to 1 MiB. Returns 503
when host inference health is unavailable.

### `GET /v1/service/health`

Return a read-only Docker lifecycle and declared healthcheck snapshot for ODS
containers. Compose service labels are preferred over container-name parsing.

**Authentication:** Required

**Response (200):**
```json
{
  "schema_version": "ods.host-service-health.v1",
  "containers": [
    {
      "service_id": "dashboard",
      "container_name": "ods-dashboard",
      "state": "running",
      "health": "healthy"
    }
  ],
  "sampled_at": "2026-07-20T22:00:00+00:00"
}
```

Returns 503 when Docker cannot provide a complete snapshot.

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

Run `ods-update.sh` from the host-agent boundary. `check` and `backup` run synchronously and return script output. `start` launches the update in a background thread and writes `data/update-status.json` for polling.

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

Fetch recent container logs. Uses `docker logs --tail N ods-<service_id>` directly (bypasses compose for speed).

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
- **Scoped network binding**: macOS/Windows bind to `127.0.0.1`; Linux binds to the `ods-network` gateway when detected so containers can reach the agent, with Docker bridge as a compatibility fallback. It does not bind to `0.0.0.0` unless explicitly configured.
- **API key auth**: All mutation endpoints require Bearer token authentication
- **Core service protection**: Core services (loaded from `config/core-service-ids.json` with hardcoded fallback) cannot be managed
- **Service ID validation**: Regex-validated, must map to an actual extension directory with a manifest
- **Per-service locking**: Prevents concurrent start+stop races on the same service via `threading.Lock`
- **Request size limit**: Request bodies capped at 4 KB
- **Subprocess timeout**: Docker operations time out after 120 seconds

## How the Dashboard API Calls It

The Dashboard API (`extensions/services/dashboard-api/routers/extensions.py`) communicates with the host agent via the `AGENT_URL` environment variable (constructed from `ODS_AGENT_HOST` and `ODS_AGENT_PORT` in `config.py`). It uses `ODS_AGENT_KEY` for authentication. The connection flows through Docker's `host.docker.internal` DNS name by default, allowing the containerized API to reach the host-bound agent.

If the host agent is unreachable, mutation operations (install, enable, disable) still succeed at the file level but return `"restart_required": true` to signal that `ods restart` is needed.
