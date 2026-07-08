# Service Manifest v2 Plan

ODS's bundled and library services use the v1 manifest schema today.
The v1 schema remains supported. This document is a planning note for a future
v2 schema so maintainers can evolve service metadata deliberately instead of
stretching one version forever.

This is not an implementation PR and does not change validation behavior.

## Why Plan v2

The v1 schema has held up well, but several concepts now carry more meaning
than the original service catalog needed:

- health checks can be HTTP, TCP, container-state, CLI, or intentionally absent;
- services may be core, optional, deprecated, owner-card-only, or backend-specific;
- GPU backend support now spans NVIDIA, AMD/Lemonade, Apple Metal, Intel Arc,
  CPU, cloud, and hybrid paths;
- dashboard, CLI, compose resolver, installer summary, and extension audit all
  consume overlapping manifest fields;
- compatibility bounds such as `ods_min` and `ods_max` are doing real work
  for downstream forks and extension catalogs.

A v2 schema should make these semantics explicit while preserving a stable v1
compatibility window.

## Goals

- Keep v1 manifests valid during the migration window.
- Add clearer health semantics without forcing HTTP-only health endpoints.
- Separate user-facing catalog metadata from runtime/compose behavior where that
  reduces ambiguity.
- Make backend and lifecycle capabilities machine-readable for installers,
  dashboards, audits, and forks.
- Provide migration tooling before requiring v2 for bundled services.

## Non-Goals

- No immediate breaking change to existing manifests.
- No dashboard catalog redesign in the same step.
- No removal of v1 validation until v2 conversion and compatibility tooling are
  proven.
- No new service behavior implied by schema metadata alone.

## Candidate v2 Concepts

| Area | v1 pressure | v2 direction |
|------|-------------|--------------|
| Health | `health` can mean HTTP path, empty string, or container-state fallback | Add explicit `health.type` such as `http`, `tcp`, `container`, `cli`, or `none` |
| Lifecycle | Startup, optionality, and restart expectations are spread across manifests and compose | Add explicit lifecycle capabilities such as `startable`, `restartable`, `requires_host_runtime` |
| Backend support | `gpu_backends` is useful but overloaded for service availability and acceleration | Split acceleration support from required platform/runtime constraints if needed |
| Exposure | Network exposure policy lives outside manifests | Keep policy external, but allow manifests to declare intended exposure class for auditing |
| Catalog metadata | Dashboard library fields and runtime fields share one document | Consider nested `catalog` and `runtime` sections |
| Compatibility | `ods_min`/`ods_max` are already useful | Keep explicit compatibility bounds and document fork behavior |

## Migration Shape

1. Add v2 schema alongside v1.
2. Add a converter or linter that can suggest v2 fields for v1 manifests.
3. Convert a small non-core service first.
4. Teach extension audit and catalog generation to read both versions.
5. Convert bundled services in batches.
6. Keep v1 accepted until release notes announce the deprecation window.

## Validation Required

Any v2 implementation PR should run:

- manifest schema validation for v1 and v2;
- extension audit;
- compose resolver checks;
- dashboard extension catalog generation;
- focused dashboard extension UI/API tests when catalog fields change;
- release-grade validation before a release if bundled service runtime behavior
  or compose generation changes.

## Fork Guidance

Forks should not invent incompatible v2 fields privately if they intend to
rebase onto upstream. Prefer adding namespaced experimental fields under an
`x_` prefix, documenting the behavior, and upstreaming the field once it proves
useful across more than one service or hardware class.
