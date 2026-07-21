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

## Blockers

- Fork PR CI `action_required` until Osmantic maintainer approves workflows
- `REVIEW_REQUIRED` on #1934
- #1743 / #1343 CONFLICTING until rebased
