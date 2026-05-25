# Dream Server Service Manifest Schema (v1)

This directory contains the JSON Schema for Dream Server extension manifests: `service-manifest.v1.json`. Manifests are YAML files (`manifest.yaml`) in each service under `extensions/services/<service-id>/`. The schema defines the structure used by the service registry, `scripts/validate-manifests.sh`, and `dream config validate` so that **extensions work seamlessly for the Dream Server version you are on**.

## Schema version

Every manifest must set:

```yaml
schema_version: dream.services.v1
```

The validator and compatibility checks use this to ensure they are reading a v1 manifest.

---

## Root-level blocks

### `compatibility` (optional)

Declares which Dream Server core versions this extension supports. Used by `scripts/validate-manifests.sh` and the installer summary to report compatible/incompatible extensions.

| Field       | Type   | Required | Description |
|------------|--------|----------|-------------|
| `dream_min`| string | no       | Minimum Dream Server version (semver, e.g. `"2.0.0"`). If set, the validator compares it to the core version from `manifest.json`. |
| `dream_max`| string | no       | Maximum Dream Server version tested (semver). Optional; if set and core is newer, the extension may be marked incompatible or warned. |

Pattern for both: `^\d+\.\d+\.\d+$` (exactly three numeric segments). Pre-release suffixes (e.g. `2.0.0-beta`) are not in the schema; the validator may treat them as the base version.

Example:

```yaml
compatibility:
  dream_min: "2.0.0"
  # dream_max: "2.1.0"   # optional
```

If `compatibility` is omitted, the validator reports "ok-no-metadata" (assumed compatible). All bundled extensions in this repo set `dream_min: "2.0.0"`.

---

### `service` (required for runtime)

Identifies the service and how the registry and compose resolver use it.

| Field                 | Type    | Required | Description |
|-----------------------|---------|----------|-------------|
| `id`                  | string  | yes      | Unique service id (lowercase, digits, hyphens). Used in `SERVICE_PORTS`, compose selection, and CLI. |
| `name`                | string  | yes      | Human-readable name (e.g. "Open WebUI (Chat)"). |
| `aliases`             | array   | no       | Shorthand ids for CLI (e.g. `[webui, ui]`). |
| `container_name`      | string  | no       | Docker container name (e.g. `dream-webui`). |
| `host_env`            | string  | no       | Env var for host override. |
| `default_host`        | string  | no       | Default hostname inside the stack. |
| `port`                | integer | yes      | Internal port (0–65535). |
| `external_port_env`   | string  | no       | Env var for external port (e.g. `WEBUI_PORT`). |
| `external_port_default` | integer | no     | Default external port; used by registry and health checks. |
| `health`              | string  | yes      | Health path (e.g. `/health`, `/`). Use `""` only for non-HTTP or one-shot services whose readiness is represented by container state/startup checks. |
| `type`                | string  | no       | `docker` or `host-systemd`. |
| `startup_check`       | boolean | no       | When `false`, the host agent skips the post-install running-state poll and treats `docker compose up`'s clean exit as success. Set this on one-shot CLI / setup-only extensions whose containers intentionally exit after init (e.g. `aider`). Default: `true`. |
| `startup_timeout`     | integer | no       | Seconds the host agent polls for the container to reach the `running` state before declaring install failed. Override the 15-second default for extensions with heavy initialization (postgres, clickhouse, JVM-based services). |
| `gpu_backends`         | array   | no       | `amd`, `nvidia`, `apple`, `all`. Used for compose overlay selection. |
| `compose_file`        | string  | no       | Relative path to compose fragment (e.g. `compose.yaml`). |
| `category`            | string  | no       | `core`, `recommended`, or `optional`. Affects default enable/disable. |
| `depends_on`          | array   | no       | List of service ids this service depends on. |
| `env_vars`            | array   | no       | List of `{ key, required?, secret?, description?, default? }` for documentation and validation. |
| `setup_hook`          | string  | no       | Relative path to a setup script run during installation. |

The service registry (`lib/service-registry.sh`) builds `SERVICE_PORTS`, `SERVICE_HEALTH`, and related maps from these fields. The compose resolver includes only enabled services (compose file present) in the stack.

---

### `features` (optional)

Used by the installer and dashboard to show feature toggles (e.g. "Voice", "Workflows", "RAG"). Each feature has an id, name, description, icon, category, requirements (services, VRAM, disk), and priority.

| Field (per feature) | Type   | Description |
|--------------------|--------|-------------|
| `id`               | string | Feature id (e.g. `voice`). |
| `name`             | string | Display name. |
| `description`      | string | Short description. |
| `icon`             | string | Icon identifier. |
| `category`         | string | Grouping (e.g. `voice`, `creative`). |
| `requirements`     | object | `services`, `services_any`, `vram_gb`, `disk_gb`. |
| `priority`         | integer| Sort order. |
| `gpu_backends`     | array  | Same as service. |

Schema allows additional properties on feature objects for future use.

---

## Validation

- **Schema validation:** If the system has Python with `pyyaml` and `jsonschema`, `scripts/validate-manifests.sh` validates each manifest against `service-manifest.v1.json`. Missing modules result in a warning and only compatibility checks run.
- **Compatibility check:** The script reads the core version from `manifest.json` and compares it to each extension’s `compatibility.dream_min` / `dream_max`, then prints a summary (ok, incompatible, ok-no-metadata).
- **Running validation:** From the repo root: `bash scripts/validate-manifests.sh`. From an install: `./dream-cli config validate` (runs both env and manifest validation).

---

## Example minimal manifest

```yaml
schema_version: dream.services.v1

compatibility:
  dream_min: "2.0.0"

service:
  id: my-service
  name: My Service
  port: 9000
  health: /health
  type: docker
  gpu_backends: [amd, nvidia]
  category: optional
  compose_file: compose.yaml
  external_port_env: MY_SERVICE_PORT
  external_port_default: 9000
```

See `extensions/services/open-webui/manifest.yaml` and the rest of `extensions/services/*/manifest.yaml` for full examples. The catalog is in [../CATALOG.md](../CATALOG.md).
