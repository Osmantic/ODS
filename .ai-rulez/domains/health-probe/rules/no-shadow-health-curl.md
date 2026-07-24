---
priority: critical
targets: [claude, cursor, codex, grok]
---

# No Shadow Health Curl for Registry Services

## Ban

Agents MUST NOT introduce or reintroduce:

```bash
curl -sf "http://127.0.0.1:${PORT}/health"
curl -sf "${SERVICE_URL}/health"
```

for services present in the extension service registry.

## Required native path

```bash
sr_load
# load .env
sr_resolve_ports
sr_curl_health "$sid" "$timeout"    # registry services
sr_http_probe_2xx "$url" "$timeout" # host-local only
```

## Exceptions (must document)

- Functional API probes (`/v1/models`, chat completions) that are not health paths
- Docker Compose healthcheck commands inside container images (image-owned)
- Bootstrap upgrade multi-step recovery sequences after migrating primary status paths
- Installer phase `12-health.sh` retry/backoff loops that already resolve URLs from `SERVICE_PORTS`/`SERVICE_HEALTH` (prefer `sr_curl_health` when adding new checks)

## Required surfaces (must use registry)

- `ods-cli`, `scripts/ods-doctor.sh`, `scripts/health-check.sh`, `scripts/validate.sh`
- `scripts/showcase.sh`, `scripts/first-boot-demo.sh`
- `ods/ods-preflight.sh`, `scripts/ods-preflight.sh`
- `ods/ods-update.sh` `cmd_health`

## Gate

`ods/tests/test-health-probe-shadow-audit.sh` must pass before merge.
