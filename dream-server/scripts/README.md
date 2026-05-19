# Dream Server Scripts

Utility scripts for diagnostics, testing, validation, and operations.

## Diagnostics

| Script | Description | Requires Stack? |
|--------|-------------|-----------------|
| `dream-doctor.sh` | JSON diagnostic report with autofix hints | No |
| `dream-preflight.sh` | Pre-install hardware/software checks | No |
| `detect-hardware.sh` | Hardware detection (`--json` for machine output) | No |
| `classify-hardware.sh` | GPU-to-tier classification | No |
| `build-capability-profile.sh` | Machine capability JSON profile | No |
| `health-check.sh` | Service health checks | Yes |

## Testing

| Script | Description | Requires Stack? |
|--------|-------------|-----------------|
| `dream-test.sh` | Full validation (`--quick`, `--json`, `--service`) | Yes |
| `dream-test-functional.sh` | Functional tests (inference, TTS, STT) | Yes |
| `validate.sh` | Post-install validation | Yes |
| `validate-env.sh` | Validate .env against schema | No |
| `audit-extensions.py` | Audit extension manifests and compose contracts | No |
| `simulate-installers.sh` | Cross-platform installer simulation | No |
| `release-gate.sh` | Full pre-release checklist | No |
| `check-compatibility.sh` | Manifest compatibility checks | No |
| `check-release-claims.sh` | Verify release claim accuracy | No |

## Operations

| Script | Description | Requires Stack? |
|--------|-------------|-----------------|
| `mode-switch.sh` | Switch deployment modes | Yes |
| `upgrade-model.sh` | Legacy model-directory swap helper; use [`../docs/MODEL-MANAGEMENT.md`](../docs/MODEL-MANAGEMENT.md) for current GGUF workflows | Yes |
| `migrate-config.sh` | Migrate config between versions | No |
| `session-cleanup.sh` | OpenClaw session lifecycle | Yes |
| `pre-download.sh` | Pre-download models for offline use | No |
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
| `healthcheck.py` | Container health check helper |
| `validate-models.py` | Validate model file integrity |
| `validate-sim-summary.py` | Validate simulation summary output |

## Systemd Units (`systemd/`)

| Unit | Description |
|------|-------------|
| `openclaw-session-cleanup.service/.timer` | Periodic OpenClaw session cleanup |
| `memory-shepherd-memory.service/.timer` | Agent memory lifecycle management |
| `memory-shepherd-workspace.service/.timer` | Agent workspace maintenance |

## Other

| Script | Description |
|--------|-------------|
| `showcase.sh` | Demo/showcase runner |
| `first-boot-demo.sh` | First-boot guided tour |
| `demo-offline.sh` | Offline mode demo |
