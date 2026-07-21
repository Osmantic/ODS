---
priority: high
targets: [claude, cursor, codex, grok]
---

# Health Probe Sprawl Inventory (living)

## Ghosts

- Empty feature worktrees without unique commits (retarget or delete)
- Duplicate PRs (#1343 vs #1692)

## Shadows

- Remaining `curl -sf` on functional API paths (models/completions) — allowed
- bootstrap-upgrade recovery curls — migrate opportunistically
- Dual schema copies — parity gated by health-contract drift test
- **Cleared 2026-07-21:** `ods/ods-preflight.sh`, `scripts/ods-preflight.sh`, `ods-update.sh` health command → `sr_curl_health` / `sr_http_probe_2xx`
- Residual dual preflight entrypoints (root + scripts/) — same registry contract; full de-dup follow-up

## Blockers

- Fork PR CI `action_required` until Osmantic maintainer approves workflows
- `REVIEW_REQUIRED` on #1934
- #1743 / #1343 CONFLICTING until rebased
