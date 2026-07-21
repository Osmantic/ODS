# Sprawl inventory snapshot (2026-07-21 SSE eval)

## Git
- Active: sandboxes/ods-health-probe-fix (health-probe branch)
- Stale: Osmantic/ODS-main (~2868 behind origin/main)
- Shadow: Documents/Codex/.../ods-doctor-health-port
- Empty feature worktrees without unique commits (retarget or delete)
- Duplicate PRs (#1343 vs #1692)

## Docker
- ~180 containers (k8s_mcp, airflow, developer-platform, datahub, ods)
- ~120 images, ~50 volumes
- Safe auto: container prune (exited only)

## Shadows
- Remaining `curl -sf` on functional API paths (models/completions) — allowed
- bootstrap-upgrade recovery curls — migrate opportunistically
- Dual schema copies — parity gated by health-contract drift test
- **Cleared 2026-07-21:** `ods/ods-preflight.sh`, `scripts/ods-preflight.sh`, `ods-update.sh` health command → `sr_curl_health` / `sr_http_probe_2xx`
- Residual dual preflight entrypoints (root + scripts/) — same registry contract; full de-dup follow-up

## Blockers outside agent control
- Osmantic/ODS push:false
- Fork PR workflows action_required
- PR mergeStateStatus BLOCKED + REVIEW_REQUIRED
- #1743 / #1343 CONFLICTING until rebased onto #1934
