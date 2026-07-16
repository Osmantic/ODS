# ODS Service Manifest Schema (v1)

This directory contains the canonical JSON Schema for ODS extension manifests: `service-manifest.v1.json`. `manifest.json` declares this path at `contracts.extensions.serviceManifestSchema`; validators resolve that contract dynamically instead of hard-coding a second schema path. Manifests are YAML files (`manifest.yaml`) in bundled services under `extensions/services/<service-id>/` and library services under `extensions/library/services/<service-id>/`. The schema is the source of truth for manifest structure used by the service registry, `scripts/validate-manifest-schema.sh`, `scripts/validate-manifests.sh`, and `ods config validate` so that **extensions work seamlessly for the ODS version you are on**.

`extensions/library/schema/service-manifest.v1.json` is kept synchronized with this canonical schema for library/catalog consumers; it is not a separate contract.

## Schema version

Every manifest must set:

```yaml
schema_version: ods.services.v1
```

The validator and compatibility checks use this to ensure they are reading a v1 manifest.

---

## Root-level blocks

### `compatibility` (optional)

Declares which ODS core versions this extension supports. Used by `scripts/validate-manifests.sh` and the installer summary to report compatible/incompatible extensions.

| Field       | Type   | Required | Description |
|------------|--------|----------|-------------|
| `ods_min`| string | no       | Minimum ODS version (semver, e.g. `"2.0.0"`). If set, the validator compares it to the core version from `manifest.json`. |
| `ods_max`| string | no       | Maximum ODS version tested (semver). Optional; if set and core is newer, the extension may be marked incompatible or warned. |

Pattern for both: `^\d+\.\d+\.\d+$` (exactly three numeric segments). Pre-release suffixes (e.g. `2.0.0-beta`) are not in the schema; the validator may treat them as the base version.

Example:

```yaml
compatibility:
  ods_min: "2.0.0"
  # ods_max: "2.1.0"   # optional
```

If `compatibility` is omitted, the validator reports "ok-no-metadata" (assumed compatible). All bundled extensions in this repo set `ods_min: "2.0.0"`.

---

### `service` (required for runtime)

Identifies the service and how the registry and compose resolver use it.

| Field                 | Type    | Required | Description |
|-----------------------|---------|----------|-------------|
| `id`                  | string  | yes      | Unique service id (lowercase, digits, hyphens). Used in `SERVICE_PORTS`, compose selection, and CLI. |
| `name`                | string  | yes      | Human-readable name (e.g. "Open WebUI (Chat)"). |
| `aliases`             | array   | no       | Shorthand ids for CLI (e.g. `[webui, ui]`). |
| `container_name`      | string  | no       | Docker container name (e.g. `ods-webui`). |
| `container_uid`       | integer | no       | Numeric UID the container process runs as when compose does not declare `user`. The host agent uses this to prepare bind-mounted data directories. |
| `host_env`            | string  | no       | Env var for host override. |
| `default_host`        | string  | no       | Default hostname inside the stack. |
| `port`                | integer | yes      | Internal port (0–65535). |
| `external_port_env`   | string  | no       | Env var for external port (e.g. `WEBUI_PORT`). |
| `external_port_default` | integer | no     | Default external port; used by registry and health checks. |
| `health`              | string  | conditional | Health path (e.g. `/health`, `/`). Required unless `host_network: true`; use `""` only for non-HTTP or one-shot services whose readiness is represented by container state/startup checks. |
| `health_timeout`      | integer | no       | Health-check timeout in seconds, 1–300. |
| `ui_path`             | string  | no       | Service UI path; must start with `/`. |
| `external_link`       | boolean | no       | When `false`, hide the service from dashboard quicklinks while keeping health/status visibility. |
| `host_network`        | boolean | no       | Whether the service joins the host network namespace. Host-network services may omit `health` when readiness is represented by compose/native checks. |
| `type`                | string  | yes      | `docker` or `host-systemd`. |
| `startup_check`       | boolean | no       | When `false`, the host agent skips the post-install running-state poll and treats `docker compose up`'s clean exit as success. Set this on one-shot CLI / setup-only extensions whose containers intentionally exit after init (e.g. `aider`). Default: `true`. |
| `startup_timeout`     | integer | no       | Seconds the host agent polls for the container to reach the `running` state before declaring install failed. Override the 15-second default for extensions with heavy initialization (postgres, clickhouse, JVM-based services). |
| `gpu_backends`         | array   | no       | `amd`, `nvidia`, `apple`, `cpu`, `all`, or `none`. Used for compose overlay selection. |
| `compose_file`        | string  | no       | Relative path to compose fragment (e.g. `compose.yaml`). |
| `category`            | string  | yes      | `core`, `recommended`, or `optional`. Affects default enable/disable. |
| `depends_on`          | array   | no       | List of service ids this service depends on. |
| `env_vars`            | array   | no       | List of `{ key, required?, secret?, description?, default? }` for documentation and validation. |
| `setup_hook`          | string  | no       | Relative path to a setup script run during installation. |
| `hooks`               | object  | no       | Lifecycle hooks such as `pre_install`, `post_install`, `pre_start`, `post_start`, `pre_uninstall`, and `post_uninstall`. |

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
| `requirements`     | object | `services`, `services_any`, `vram_gb`, `vram_mb`, `disk_gb`. |
| `priority`         | integer| Sort order. |
| `launch`           | object | Dashboard launch target: `type` is `service`, `internal`, or `none`; optional `service` and slash-prefixed `path`. |
| `gpu_backends`     | array  | Same as service: `amd`, `nvidia`, `apple`, `cpu`, `all`, or `none`. |

Schema allows additional properties on feature objects for future use.

---

## Validation

- **JSON Schema is the source of truth:** `service-manifest.v1.json` defines the manifest contract. Keep required fields, enum values, type checks, and patterns in the JSON Schema so validation tools do not drift.
- **Strict schema gate:** `scripts/validate-manifest-schema.sh` validates bundled and library manifests against `service-manifest.v1.json`. It requires Python with `PyYAML` and `jsonschema` and fails with a clear dependency message if either module is missing. This dependency is for CI, release checks, and developer manifest validation; normal ODS runtime should not require `jsonschema`.
- **Compatibility check:** `scripts/validate-manifests.sh` reads the core version from `manifest.json` and compares it to each extension’s `compatibility.ods_min` / `ods_max`, then prints a summary (ok, incompatible, ok-no-metadata). If Python schema modules are missing, it warns and only compatibility checks run.
- **Running validation:** From the repo root, run `bash scripts/validate-manifest-schema.sh` for strict JSON Schema validation, or `bash scripts/validate-manifests.sh` for compatibility-oriented validation. From an install: `./ods-cli config validate` runs env and manifest validation.

---

## Example minimal manifest

```yaml
schema_version: ods.services.v1

compatibility:
  ods_min: "2.0.0"

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
