# Sprawl inventory snapshot (2026-07-21 SSE eval)

## Git
- Active: sandboxes/ods-health-probe-fix (health-probe branch)
- Stale: Osmantic/ODS-main (~2868 behind origin/main)
- Shadow: Documents/Codex/.../ods-doctor-health-port

## Docker
- ~180 containers (k8s_mcp, airflow, developer-platform, datahub, ods)
- ~120 images, ~50 volumes
- Safe auto: container prune (exited only)

## Blockers outside agent control
- Osmantic/ODS push:false
- Fork PR workflows action_required
- PR mergeStateStatus BLOCKED + REVIEW_REQUIRED
