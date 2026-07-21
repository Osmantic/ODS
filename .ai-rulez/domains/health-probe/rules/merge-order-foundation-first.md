---
priority: critical
targets: [claude, cursor, codex, grok]
---

# Health Probe Foundation-First Merge Order

1. Land registry HTTP contract (#1934)
2. Rebase typed `health_type` (#1692) onto #1934
3. Rebase external LLM doctor (#1743)
4. Close #1343 as duplicate of #1692

Do not invent parallel probe stacks while these PRs are open.
See `ods/docs/ADR-HEALTH-PROBE-MERGE-ORDER.md`.
