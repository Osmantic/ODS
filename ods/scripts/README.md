# ODS Scripts

Utility scripts for diagnostics, testing, validation, and operations.

## Diagnostics

| Script | Description | Requires Stack? |
|--------|-------------|-----------------|
| `ods-doctor.sh` | JSON diagnostic report with autofix hints | No |
| `ods-preflight.sh` | Pre-install hardware/software checks | No |
| `detect-hardware.sh` | Hardware detection (`--json` for machine output) | No |
| `classify-hardware.sh` | GPU-to-tier classification | No |
| `build-capability-profile.sh` | Machine capability JSON profile | No |
| `health-check.sh` | Service health checks | Yes |

## Testing

| Script | Description | Requires Stack? |
|--------|-------------|-----------------|
| `ods-test.sh` | Full validation (`--quick`, `--json`, `--service`) | Yes |
| `ods-test-functional.sh` | Functional tests (inference, TTS, STT) | Yes |
| `validate.sh` | Post-install validation | Yes |
| `validate-env.sh` | Validate .env against schema | No |
| `audit-extensions.py` | Audit extension manifests and compose contracts | No |
| `simulate-installers.sh` | Cross-platform installer simulation | No |
| `release-gate.sh` | Full pre-release checklist | No |
| `verify-hosted-bootstrap.sh` | Verify all twelve rolling-`main` aliases and hosted bootstrap bytes against an exact Git ref | No |
| `check-compatibility.sh` | Manifest compatibility checks | No |
| `check-release-claims.sh` | Verify release claim accuracy | No |

## Operations

| Script | Description | Requires Stack? |
|--------|-------------|-----------------|
| `mode-switch.sh` | Switch deployment modes | Yes |
| `upgrade-model.sh` | Legacy model-directory swap helper; use [`../docs/MODEL-MANAGEMENT.md`](../docs/MODEL-MANAGEMENT.md) for current GGUF workflows | Yes |
| `migrate-config.sh` | Migrate config between versions | No |
| `pre-download.sh` | Legacy Hugging Face pre-download helper (pre-GGUF tier names); download GGUF models from Dashboard → Models instead | No |
| `llm-cold-storage.sh` | Archive/restore models | No |

## Installer Support

| Script | Description |
|--------|-------------|
| `load-backend-contract.sh` | Load backend contract JSON as env vars |
| `resolve-compose-stack.sh` | Resolve compose overlay stack |
| `preflight-engine.sh` | Preflight validation engine |
| `check-offline-models.sh` | Verify offline model availability |

## Python Utilities

| Script | Description |
|--------|-------------|
| `healthcheck.py` | Container health check helper; [first-response redirect checks](../docs/HEALTHCHECK-REDIRECTS.md) |
| `validate-models.py` | Validate model file integrity |
| `validate-sim-summary.py` | Validate simulation summary output |

## Systemd Units (`systemd/`)

| Unit | Description |
|------|-------------|
| `ods-host-agent.service` | Host agent API; the installer renders and installs it |
| `ods-mdns.service` | Publishes `<device>.local` and service subdomains ([MDNS](../docs/MDNS.md)) |
| `ods-ap-mode.service` | First-boot setup access point; disabled by default ([AP mode](../docs/AP-MODE.md)) |

Memory Shepherd's own `memory-shepherd/install.sh` generates its timers. The
`memory-shepherd-memory` and `memory-shepherd-workspace` timers that older AMD
installs enabled served only the removed legacy OpenClaw extension; see
[the removal notice](../docs/MIGRATION-OPENCLAW-TO-HERMES.md).

## Other

| Script | Description |
|--------|-------------|
| `showcase.sh` | Demo/showcase runner |
| `first-boot-demo.sh` | First-boot guided tour |
| `demo-offline.sh` | Offline mode demo |
