# Installer Preflight Engine

The installer now runs a capability-aware preflight engine before Docker setup.

## Script

- `scripts/preflight-engine.sh`

## Purpose

Validate hard requirements and produce actionable findings before installation continues.

The engine emits:

- blockers: must be acknowledged before continuing
- warnings: non-fatal recommendations
- machine-readable report JSON

Platform behavior:

- Linux/WSL paths are evaluated as primary install targets.
- Windows/macOS paths are evaluated as installer-MVP targets (warnings until full parity).

## Output

Default report path:

- `/tmp/dream-server-preflight-report.json`

Installer can override with:

- `PREFLIGHT_REPORT_FILE=/path/to/report.json`

## Example

```bash
scripts/preflight-engine.sh \
  --tier 3 \
  --ram-gb 64 \
  --disk-gb 120 \
  --gpu-backend nvidia \
  --gpu-vram-mb 24576 \
  --platform-id linux \
  --compose-overlays docker-compose.base.yml,docker-compose.nvidia.yml \
  --script-dir . \
  --report /tmp/dream-server-preflight-report.json
```

For shell integration:

```bash
source lib/safe-env.sh
PREFLIGHT_ENV="$(scripts/preflight-engine.sh --env ...)"
load_env_from_output <<< "$PREFLIGHT_ENV"
echo "$PREFLIGHT_BLOCKERS $PREFLIGHT_WARNINGS $PREFLIGHT_CAN_PROCEED"
```
