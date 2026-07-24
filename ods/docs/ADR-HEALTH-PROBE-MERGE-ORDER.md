# ADR: Health Probe Contract Merge Order

**Date:** 2026-07-21  
**Status:** Accepted  
**Decision:** Land the HTTP health-probe foundation before typed probes and external-LLM doctor changes

## Context

Several open PRs touch the same health surfaces (`service-registry.sh`,
manifest schemas, `health-check.sh`, `ods-doctor.sh`, dashboard helpers):

| PR | Intent | State |
|----|--------|-------|
| **#1934** | Centralize HTTP health ports, headers, and 2xx-only probing in the registry | Foundation |
| **#1692** | Add `health_type: http \| tcp \| none` for Wyoming / one-shot CLI services | Extends foundation |
| **#1343** | Duplicate of #1692 | Superseded |
| **#1743** | External LLM backend checks in `ods doctor` when local llama-server is off | Overlaps doctor/CLI |

Independent hand-rolled `curl -sf` probes also remain (or remained) in
doctor voice checks, showcase demos, host-agent status, and voice repair.
Those shadows diverge from registry semantics (redirect handling,
`health_port`, virtual-host headers).

## Decision

1. **Merge #1934 first** — registry-owned `health_port` / `health_port_env` /
   `health_header`, `sr_health_port`, `sr_health_url`, `sr_curl_health` /
   `sr_http_probe_2xx`, consumer migration, and contract tests.
2. **Rebase and merge #1692 second** — typed `health_type` dispatch must build
   on the centralized probe helpers, not reintroduce per-surface curl assembly.
3. **Rebase and merge #1743 third** — external LLM doctor diagnosis routes
   through the same contract once foundation lands (avoids doctor conflict churn).
4. **Close #1343** without merge — treat as duplicate of #1692; cherry-pick only
   unique improvements if any remain after #1692.
5. **Ban new shadow probes** — new health checks for registry services must call
   `sr_curl_health` (or typed successors). Host-local endpoints use
   `sr_http_probe_2xx`.

## Consequences

### Positive

- One semantic contract for ports, headers, redirects (2xx-only), and env overrides
- Predictable rebase cost for TCP/none and external-LLM work
- Fewer false healthy/unhealthy results across CLI, doctor, preflight, showcase

### Trade-offs

- #1692 and #1743 stay blocked until #1934 lands
- Cross-fork PR workflows may still require maintainer approval before checks run
- Host-agent remains a non-manifest endpoint (uses shared 2xx helper, not service id)

### Rollback

Reverting #1934 restores independent curl assembly. Typed-probe and external-LLM
PRs must not land against that older surface without reintroducing the foundation.

## Validation gates for #1934

- `tests/test-ods-cli-health-probe-contract.sh`
- Doctor / health-check unit suites
- Dashboard helpers tests for redirect-unhealthy + headers
- ShellCheck / make lint on touched scripts
- CI workflows after maintainer approval of fork PR runs
