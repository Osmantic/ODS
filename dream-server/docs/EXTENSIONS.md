# Dream Server Extensions

## Two Kinds of Extension

| I want to... | Type | Start here |
|---|---|---|
| Add a Docker service (new container, health check, dashboard tile) | Service extension | This guide (below) |
| Change the installer itself (new tier, swap theme, add/skip phase) | Installer mod | [INSTALLER-ARCHITECTURE.md](INSTALLER-ARCHITECTURE.md) |
| Build a custom downstream edition or appliance | Fork / downstream build | [BUILD-ON-DREAM-SERVER.md](BUILD-ON-DREAM-SERVER.md) |

This guide is the fastest path to extend Dream Server without editing core internals.

## Extension Directory Structure

Each extension service is a directory under `extensions/services/`:

```
extensions/services/
  my-service/
    manifest.yaml      # Service metadata (required)
    compose.yaml       # Docker Compose fragment (for extension services)
    compose.amd.yaml   # GPU overlay for AMD (optional)
    compose.nvidia.yaml # GPU overlay for NVIDIA (optional)
```

**Core services** (llama-server, open-webui, dashboard, dashboard-api) have only a `manifest.yaml` — their compose definitions live in `docker-compose.base.yml`.

**Extension services** have both `manifest.yaml` and `compose.yaml`. The compose fragment is merged into the stack automatically by `resolve-compose-stack.sh`.

## What You Can Extend

- **Docker services** via `extensions/services/<name>/compose.yaml`
- **Service metadata** (health checks, ports, aliases, categories) via `manifest.yaml`
- **Feature tiles** exposed by `GET /api/features` via manifest `features` blocks
- **Dashboard UI** via plugin registration in `dashboard/src/plugins/registry.js`

## 30-Minute Path: Add a Service

### Step 1: Create the extension directory

```bash
mkdir extensions/services/my-service
cp extensions/templates/service-template.yaml extensions/services/my-service/manifest.yaml
cp extensions/templates/compose-template.yaml extensions/services/my-service/compose.yaml
```

The commented starter files live in [`extensions/templates/`](../extensions/templates/README.md).

### Step 2: Create the manifest

Edit the manifest:
- set `service.id` to a unique kebab-case ID
- set `service.name`, `service.port`, `service.health`
- set `service.aliases` for CLI shorthand names
- set `service.container_name` (typically `dream-<id>`)
- set `service.category`: `core`, `recommended`, or `optional`
- set `service.compose_file: compose.yaml`
- set `service.depends_on` if it needs other services
- set `service.gpu_backends` (`amd`, `nvidia`, or both)
- add feature entries under `features` if the service unlocks user-visible capability

### Step 3: Create the compose fragment

Create `extensions/services/my-service/compose.yaml`:

```yaml
services:
  my-service:
    image: my-org/my-service:latest
    container_name: dream-my-service
    restart: unless-stopped
    ports:
      - "${MY_SERVICE_PORT:-9200}:8080"
    environment:
      - LLM_URL=http://llama-server:8080/v1
    depends_on:
      llama-server:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s
```

The service automatically joins `dream-network` and can reach other services by Docker DNS name.

### Step 4: Validate

```bash
# Schema check
python3 -c "import yaml; yaml.safe_load(open('extensions/services/my-service/manifest.yaml'))"

# Compose merge check
docker compose -f docker-compose.base.yml -f docker-compose.amd.yml \
  -f extensions/services/my-service/compose.yaml config

# Contract audit (manifest + compose + overlay consistency)
python3 scripts/audit-extensions.py --project-dir .
bash tests/test-extension-audit.sh

# Integration/smoke checks
bash tests/integration-test.sh
bash tests/smoke/linux-amd.sh
```

### Step 5: Test it

```bash
# Enable and start
dream enable my-service
dream start my-service

# Verify healthy
dream logs my-service
curl http://localhost:9200/health

# Check it appears in the list
dream list
```

## Enable/Disable Mechanism

- `compose.yaml` present → **enabled** (included in stack)
- `compose.yaml.disabled` → **disabled** (manifest still visible to CLI/dashboard)
- Core services (`category: core`) have no compose.yaml — always on in base.yml

```bash
dream enable my-service    # Renames compose.yaml.disabled → compose.yaml
dream disable my-service   # Stops container, renames compose.yaml → compose.yaml.disabled
dream list                 # Shows all services with status
```

## Audit Extensions Before You Ship

Dream Server now includes an extension audit workflow so new services can be
validated like core components instead of relying on ad hoc manual checks.

Run the full audit:

```bash
python3 scripts/audit-extensions.py --project-dir .
```

Audit one service only:

```bash
python3 scripts/audit-extensions.py --project-dir . whisper
```

Fail on warnings too:

```bash
python3 scripts/audit-extensions.py --project-dir . --strict
```

From an installed system you can use the CLI:

```bash
dream audit
dream audit --json comfyui
```

What the audit checks:
- manifest schema/version, categories, types, ports, and health endpoints
- alias collisions and broken dependency references
- feature IDs and feature service references
- compose file presence for non-core docker services
- compose container names, port mappings, healthchecks, and disabled-state discovery
- GPU overlay coverage for stub-based GPU services such as ComfyUI-style layouts

This is especially useful when validating large extension libraries, because it
surfaces integration regressions before they hit installer or runtime testing.

## Manifest Contract (v1)

Required root field:
- `schema_version: dream.services.v1`

Optional root field:
- `compatibility` — version compatibility hints:
  - `dream_min`: minimum Dream Server version this extension supports (e.g. `"2.0.0"`)
  - `dream_max`: maximum Dream Server version this extension was tested against (optional)

Service section:
- required: `id`, `name`, `port`, `health`
- recommended: `aliases`, `container_name`, `compose_file`, `category`, `depends_on`
- optional: `host_env`, `default_host`, `external_port_env`, `external_port_default`, `type`, `gpu_backends`, `env_vars`

Feature section (optional list):
- required per feature: `id`, `name`, `description`, `icon`, `category`, `requirements`, `priority`
- optional: `enabled_services_all`, `enabled_services_any`, `setup_time`, `gpu_backends`

## Service Categories

| Category | Behavior | Examples |
|----------|----------|---------|
| `core` | Always on, lives in base.yml | llama-server, open-webui, dashboard |
| `recommended` | Enabled by default | searxng, litellm, token-spy |
| `optional` | User opts in | n8n, whisper, tts, comfyui |

## GPU Overlay Patterns

If your service uses a GPU, you need overlay files alongside `compose.yaml`. The compose resolver (`resolve-compose-stack.sh`) automatically picks up `compose.nvidia.yaml` or `compose.amd.yaml` based on the detected GPU vendor. Only one overlay is active at a time.

There are two patterns. Pick the one that matches your service:

### Pattern 1: CPU-Base with GPU Tag Swap

**When to use:** Your service works on CPU but runs faster on GPU (e.g., speech-to-text, embedding generation, transcription).

The base `compose.yaml` has the full service definition with a CPU image. The GPU overlay only overrides the image tag and adds GPU device reservations. Everything else (ports, volumes, healthcheck) is inherited from the base.

**File layout:**
```
extensions/services/my-service/
  manifest.yaml
  compose.yaml            # Full definition, CPU image (e.g., :latest-cpu)
  compose.nvidia.yaml     # Swaps image to :latest-cuda, adds GPU devices
  compose.amd.yaml        # Swaps image to :latest-rocm, adds AMD devices
```

**Example** (from whisper):

`compose.yaml` — full service with CPU image:
```yaml
services:
  whisper:
    image: ghcr.io/speaches-ai/speaches:latest-cpu
    container_name: dream-whisper
    # ... ports, volumes, healthcheck, etc.
```

`compose.nvidia.yaml` — only the GPU-specific overrides:
```yaml
services:
  whisper:
    image: ghcr.io/speaches-ai/speaches:latest-cuda
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
        limits:
          cpus: '4.0'
          memory: 8G
```

### Pattern 2: Empty Base with Full GPU Overlay

**When to use:** Your service only makes sense on a GPU, with no CPU fallback (e.g., image generation, video rendering).

The base `compose.yaml` is an empty stub (`services: {}`). Each GPU overlay contains the complete service definition. The definitions often differ significantly between vendors (different images, device passthrough, environment variables, CLI flags).

**File layout:**
```
extensions/services/my-service/
  manifest.yaml
  compose.yaml            # Empty stub: services: {}
  compose.nvidia.yaml     # Complete NVIDIA definition
  compose.amd.yaml        # Complete AMD definition
```

**Example** (from comfyui):

`compose.yaml` — empty stub so the registry detects the service:
```yaml
# ComfyUI — Image Generation
# The GPU overlay provides the full service definition.
services: {}
```

`compose.nvidia.yaml` — full service definition:
```yaml
services:
  comfyui:
    build:
      context: ./comfyui
      dockerfile: Dockerfile
    container_name: dream-comfyui
    restart: unless-stopped
    ports:
      - "${COMFYUI_PORT:-8188}:8188"
    volumes:
      - ./data/comfyui/models:/models
      # ... other mounts
    shm_size: '8g'
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    healthcheck:
      test: ["CMD", "wget", "--spider", "--quiet", "http://localhost:8188"]
      interval: 30s
      timeout: 10s
      start_period: 120s
      retries: 3
```

`compose.amd.yaml` — full service with AMD-specific config:
```yaml
services:
  comfyui:
    image: ignatberesnev/comfyui-gfx1151:v0.2
    container_name: dream-comfyui
    devices:
      - /dev/dri:/dev/dri
      - /dev/kfd:/dev/kfd
    group_add:
      - "${VIDEO_GID:-44}"
      - "${RENDER_GID:-992}"
    environment:
      - HSA_OVERRIDE_GFX_VERSION=11.5.1
    # ... ports, volumes, healthcheck, deploy, etc.
```

### GPU Overlay Quick Reference

| | Pattern 1 (tag swap) | Pattern 2 (GPU-only) |
|---|---|---|
| CPU fallback? | Yes | No |
| Base compose.yaml | Full service definition | `services: {}` |
| GPU overlay contains | Image tag + deploy block | Entire service definition |
| Example service | whisper | comfyui |
| Template | `extensions/templates/compose-gpu-swap.yaml` | `extensions/templates/compose-gpu-only.yaml` |

### AMD-Specific Notes

AMD ROCm requires additional container configuration compared to NVIDIA:
- **Device passthrough:** `/dev/dri` (rendering) and `/dev/kfd` (compute)
- **Group membership:** Container user must be in the host's `video` and `render` groups
- **GFX version override:** Set `HSA_OVERRIDE_GFX_VERSION` to match your GPU (check with `rocminfo | grep gfx`)
- **Security relaxation:** `cap_add: SYS_PTRACE` and `seccomp:unconfined` may be needed for ROCm profiling

## Compatibility Checklist

- Service ID is unique and stable
- Health endpoint is cheap and deterministic
- Feature requirements use real service IDs
- AMD/NVIDIA support is explicitly declared
- Docs/examples reference canonical paths (`config/n8n`, `docker compose`)
- CI scripts pass locally (`integration-test`, smoke scripts, syntax checks)

## Testing Checklist (PR Gate)

- `bash -n` on changed shell files
- `python3 -m py_compile extensions/services/dashboard-api/main.py`
- `bash tests/integration-test.sh`
- relevant smoke scripts in `tests/smoke/`
- if dashboard code changed and Node is available:
```bash
cd extensions/services/dashboard
npm install
npm run lint
npm run build
```

## Runtime Lifecycle

This section describes the end-to-end flow of how extensions are discovered, installed, enabled, and managed at runtime. Each step references the source file that implements it.

### 1. Catalog Generation

At startup, the Dashboard API (`config.py:load_extension_catalog()`) loads `config/extensions-catalog.json` — a static JSON file listing all available extensions. This catalog is served via `GET /api/extensions/catalog` and enriched at request time with live status (enabled, disabled, not_installed, incompatible) by checking the filesystem and service health (`routers/extensions.py:_compute_extension_status()`).

### 2. Manifest Loading

The Dashboard API loads manifests from `extensions/services/*/manifest.yaml` at startup (`config.py:load_extension_manifests()`). This populates the `SERVICES` dict (used for health checks) and `FEATURES` list (used by the features endpoint). Disabled extensions (those with `compose.yaml.disabled` instead of `compose.yaml`) are skipped during manifest loading — they do not appear in service health checks or feature recommendations.

### 3. Install (Library to User Extensions)

When a user installs an extension via the dashboard (`POST /api/extensions/{service_id}/install`), the extensions router:

1. Validates the service ID and confirms it is not a core service
2. Locates the extension in the **extensions library** (`$DREAM_DATA_DIR/extensions-library/<id>/`)
3. Performs a size check (max 50 MB) and security scan of the compose file (rejects privileged mode, Docker socket mounts, host network, dangerous capabilities, non-localhost port bindings, and other unsafe directives)
4. Copies the extension to `$DREAM_DATA_DIR/user-extensions/<id>/` atomically via a temp directory on the same filesystem
5. Calls the host agent to start the container (`POST /v1/extension/start`)

The install uses file locking (`fcntl.flock`) to prevent double-install races.

### 4. Enable / Disable

Extensions are enabled or disabled by renaming their compose file:

- **Enable** (`POST /api/extensions/{service_id}/enable`): Renames `compose.yaml.disabled` to `compose.yaml`, then calls the host agent to start the container. Before enabling, the router checks that all dependencies declared in the manifest's `service.depends_on` are present and enabled.
- **Disable** (`POST /api/extensions/{service_id}/disable`): Calls the host agent to stop the container first (prevents zombie containers), then renames `compose.yaml` to `compose.yaml.disabled`. Warns if other enabled extensions depend on this one.

Both operations validate that the compose file is not a symlink (TOCTOU prevention under lock) and re-scan compose contents before enabling.

### 5. Host Agent Container Management

The [host agent](HOST-AGENT-API.md) (`bin/dream-host-agent.py`) runs on the host machine and exposes `/v1/extension/start`, `/v1/extension/stop`, and `/v1/extension/logs`. When the Dashboard API calls start/stop, the host agent:

1. Resolves the full compose stack by calling `scripts/resolve-compose-stack.sh`
2. Runs `docker compose <flags> up -d <service_id>` (start) or `docker compose <flags> stop <service_id>` (stop)
3. For start operations, pre-creates any `./data/` volume directories with correct ownership

If the host agent is unreachable, file-level operations (install, enable, disable) still succeed, but `restart_required: true` is returned to signal that `dream restart` is needed.

### 6. Compose Stack Discovery

`scripts/resolve-compose-stack.sh` dynamically builds the list of `-f` flags for `docker compose`. It:

1. Selects the base compose files based on GPU backend and tier (e.g., `docker-compose.base.yml` + `docker-compose.nvidia.yml`)
2. Walks `extensions/services/*/` and includes each extension's `compose.yaml` if it exists (not `.disabled`), skipping extensions incompatible with the current GPU backend
3. Picks up GPU-specific overlays (`compose.<backend>.yaml`) and mode-specific overlays (`compose.local.yaml`) per extension
4. Walks `data/user-extensions/*/` and includes compose files from user-installed extensions
5. Appends `docker-compose.override.yml` if it exists (user customizations)

### 7. Uninstall

Uninstalling (`DELETE /api/extensions/{service_id}`) requires the extension to be disabled first (compose file must be renamed to `.disabled`). It then removes the extension directory from `user-extensions/` under file lock.

### 8. Dashboard UI Status Polling

The dashboard frontend polls `GET /api/extensions/catalog` to display extension status. Each extension's status is computed by checking:

- For core/built-in services: whether the service health check reports "healthy"
- For user-installed extensions: whether `compose.yaml` (enabled) or `compose.yaml.disabled` (disabled) exists in the user-extensions directory
- For extensions not yet installed: whether the current GPU backend is compatible

The catalog response also includes `agent_available` (whether the host agent is reachable) and `library_available` (whether the extensions library directory exists and is non-empty).

### Lifecycle Summary

```
  Extensions Library                User Extensions Dir
  ($DATA_DIR/extensions-library/)   ($DATA_DIR/user-extensions/)
         │                                   │
         │  POST .../install                 │
         │  (copy + security scan)           │
         └──────────────────────────────────►│
                                             │
                     compose.yaml ◄──────────┤ (enabled)
                     compose.yaml.disabled ◄─┤ (disabled)
                                             │
                     Host Agent              │
                     (127.0.0.1:7710)        │
                         │                   │
                         ▼                   │
                     docker compose up -d    │
                     docker compose stop     │
                                             │
                     resolve-compose-stack.sh │
                         │                   │
                         ▼                   │
                     Merges base + GPU +     │
                     extensions + user-ext   │
                     into one compose stack  │
```

## Notes

- Manifest loading is additive with safe fallback defaults.
- Unknown/malformed manifests are skipped with warnings, not fatal crashes.
- Keep extension files ASCII and small; one service per directory is preferred.
- The service registry (`lib/service-registry.sh`) provides bash functions for resolving aliases and discovering enabled services.
- **Scripts that load `.env`:** Source `lib/safe-env.sh` and use `load_env_file "<path>"`; do not use `eval` or `export $(grep ... .env | xargs)` (injection risk).
