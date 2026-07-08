# ADR: Docker Image Tag Pinning Policy

**Date:** 2026-03-04
**Status:** Accepted
**Decision:** Retain `:latest` tags for third-party service images

## Context

A security audit of the ODS Docker Compose stack identified three
services using unpinned `:latest` image tags:

| Service | Image | Compose File |
|---------|-------|-------------|
| OpenClaw | `ghcr.io/openclaw/openclaw:latest` | `extensions/services/openclaw/compose.yaml` |
| SearXNG | `searxng/searxng:latest` | `extensions/services/searxng/compose.yaml` |
| Whisper (CPU) | `ghcr.io/speaches-ai/speaches:latest-cpu` | `extensions/services/whisper/compose.yaml` |
| Whisper (GPU) | `ghcr.io/speaches-ai/speaches:latest-cuda` | `extensions/services/whisper/compose.nvidia.yaml` |

Unpinned tags are generally flagged as a supply chain risk because a new
upstream release could introduce breaking changes or, in a worst case,
compromised code.

## Analysis

We evaluated each image against three criteria:

### 1. Upstream project stability

- **OpenClaw** uses date-based tags (`YYYY.M.D`). Releases are frequent but
  the project maintains backward compatibility on its gateway API.
- **SearXNG** publishes multiple builds per day with commit-hash suffixes
  (e.g. `2026.3.3-65ae6ad90`). There is no formal "stable release" concept;
  every main-branch push produces a new tag.
- **Speaches** uses semantic versioning (`0.8.x` stable, `0.9.0-rc.x`
  pre-release) with `-cpu`/`-cuda` suffixes. The stable line is mature.

All three are actively maintained open-source projects under their respective
organizations' GitHub accounts, published to official registries (GHCR,
Docker Hub).

### 2. Risk of pinning

- **SearXNG** has no stable release channel. Pinning to a commit-hash tag
  means manually tracking builds with no release notes to consult. A stale
  pin could silently accumulate missed security patches.
- **Whisper/Speaches** has a `sed` entrypoint patch in our compose file that
  modifies an internal source path. A pinned version mismatch (too old or
  too new) could cause the patch to silently fail if the target file moves.
- **OpenClaw** is the lowest risk to pin, but as an optional service its
  blast radius is already contained.
- All three services are **optional** (category `optional` or `recommended`
  in their manifests) and are not part of the core inference stack.

### 3. Supply chain exposure

- Images are pulled only at install time or explicit `docker compose pull`.
  There is no auto-update mechanism that would silently swap images.
- The `no-new-privileges` security option, non-root users, resource limits,
  and network isolation already constrain what a compromised image could do.
- ODS targets local/air-gapped deployments where images are often
  cached after first pull.

## Decision

Retain `:latest` tags for these three services. The stability risk of
pinning (silent patch failures, stale security fixes, maintenance burden of
tracking tagless projects) outweighs the supply chain risk given:

1. All three are optional services with contained blast radius.
2. Images are only pulled on explicit user action, not auto-updated.
3. Existing container hardening (non-root, no-new-privileges, resource
   limits) limits impact of a compromised image.
4. SearXNG's tagging scheme makes stable pinning impractical without a
   dedicated version-tracking process.

## Consequences

- Upstream breaking changes could surface after a fresh `docker compose pull`.
  The existing health-check system (Phase 12) will catch service failures.
- If a higher-assurance deployment is needed, operators can override the
  image tag in their `.env` or a local compose override file.
- This decision should be revisited if any of these projects adopt a formal
  stable release channel or if ODS moves to a signed/verified image
  pipeline.

## Alternatives considered

- **Pin to specific tags:** Rejected due to SearXNG's lack of stable
  releases and the Whisper entrypoint patch sensitivity.
- **Pin OpenClaw only:** Low value in isolation; consistency across optional
  services is preferred.
- **Digest pinning (`image@sha256:...`):** Maximum reproducibility but
  highest maintenance burden and no human-readable version context.
