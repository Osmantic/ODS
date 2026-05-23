# Dream Server Extensions

This directory contains all service extensions that ship with Dream Server. Each subdirectory under `services/` is a self-contained extension with a manifest and optional compose files.

## Documentation Map

| Document | What it covers |
|----------|----------------|
| [Extension Authoring Guide](../docs/EXTENSIONS.md) | How to create a new extension: directory structure, manifest contract, compose patterns, GPU overlays, enable/disable mechanism, validation, and runtime lifecycle |
| [Build On Dream Server](../docs/BUILD-ON-DREAM-SERVER.md) | How to fork Dream Server, ship a custom edition, keep changes rebase-friendly, and validate downstream changes |
| [Extension Templates](templates/README.md) | Copy-ready manifest, compose, GPU overlay, and dashboard plugin starter files |
| [Extension Catalog](CATALOG.md) | List of all bundled extensions with ports, categories, and GPU compatibility |
| [Manifest Schema Reference](schema/README.md) | JSON Schema specification for `manifest.yaml` — all fields, types, and validation rules |
| [Host Agent API](../docs/HOST-AGENT-API.md) | API contract for the host agent that starts/stops extension containers from outside Docker |
| [Dashboard API Extensions Endpoints](services/dashboard-api/README.md#extensions) | REST API for browsing, installing, enabling, disabling, and uninstalling extensions |

## Directory Layout

```
extensions/
  CATALOG.md              # Bundled extension catalog
  README.md               # This file
  schema/
    service-manifest.v1.json  # JSON Schema for manifest validation
    README.md                 # Schema documentation
  templates/
    service-template.yaml      # Commented manifest starter
    compose-template.yaml      # Commented compose starter
    compose-gpu-swap.yaml      # CPU base + GPU image swap pattern
    compose-gpu-only.yaml      # GPU-only overlay pattern
  services/
    <service-id>/
      manifest.yaml           # Service metadata (required)
      compose.yaml            # Docker Compose fragment (extension services)
      compose.amd.yaml        # AMD GPU overlay (optional)
      compose.nvidia.yaml     # NVIDIA GPU overlay (optional)
      compose.local.yaml      # Mode-specific overlay (optional)
      README.md               # Per-service documentation (optional)
```

Core services (llama-server, open-webui, dashboard, dashboard-api) only have a `manifest.yaml` here — their compose definitions live in `docker-compose.base.yml` at the repository root.

## Quick Links

- **Add an extension**: [30-Minute Path](../docs/EXTENSIONS.md#30-minute-path-add-a-service)
- **Build a custom edition**: [Build On Dream Server](../docs/BUILD-ON-DREAM-SERVER.md)
- **Copy starter files**: [Extension Templates](templates/README.md)
- **Validate manifests**: `bash scripts/validate-manifests.sh` or `dream config validate`
- **Audit extensions**: `python3 scripts/audit-extensions.py --project-dir .`
- **Enable/disable from CLI**: `dream enable <id>` / `dream disable <id>`
