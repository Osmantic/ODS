---
priority: high
targets: [claude, cursor, codex, grok]
---

# Device Centralization for ODS + Platform

## Rules

1. **One feature worktree** for health-probe until PR #1934 merges.
2. **Do not** create parallel doctor-health-port / health-probe trees with overlapping files.
3. **Docker:** exited container prune is allowed; volume prune requires human approval.
4. **Merge to main** requires maintainer workflow approval on fork PRs — agents must not claim merged when `mergeStateStatus=BLOCKED`.
5. **Shared tooling** lives in developer-platform (mise, MCP registry); ODS consumes, does not duplicate.

## Gates

- Local: health probe contract + shadow + path/shell + schema drift
- Remote: fork CI not `action_required`
