---
title: Pixel support and qualification matrix
doc_type: reference
audience: [owner, operator, contributor, security-reviewer, maintainer]
feature_status: mixed
owners: [documentation, qualification, release]
sources_of_truth: [RELEASE-MANIFEST.json, QUALIFICATION-MATRIX.json, OPENCLAW-COMPATIBILITY.json]
generated_by: scripts/docs/generate-platform-reference.mjs
last_verified_at: generated
---

<!-- GENERATED FILE. Run `node scripts/docs/generate.mjs --write`; do not edit by hand. -->

# Pixel support and qualification matrix

This page reports repository contracts. It does not prove a fresh run on any host, and it does not broaden Pixel to other Linux distributions, Windows, macOS, hardware, providers, or models.

## Documented host lanes

| Lane | Host | Environment | Promotion-required | Provider calls | Credential inputs | Checks |
|---|---|---|---:|---:|---:|---|
| `ubuntu-24.04-automated` | Ubuntu 24.04 LTS | github-hosted | yes | not allowed | not allowed | `static-unit-security`, `clean-room-lifecycle`, `plugin-integrity`, `release-package` |
| `debian-12-automated` | Debian 12 | manifest-pinned-container | yes | not allowed | not allowed | `static-unit-security`, `clean-room-lifecycle`, `plugin-integrity`, `release-package`, `reproducible-package` |
| `ubuntu-24.04-systemd` | Ubuntu 24.04 LTS | deployment-owned-systemd | yes | not allowed | not allowed | `install-apply-verify`, `service-isolation`, `sandbox-isolation`, `deep-work-real-crash-endurance`, `deep-work-supervised-service`, `capability-pack-live-runtime`, `backup-recovery`, `knowledge-vault-backup-recovery`, `release-identity-refusal`, `disposable-gateway-removal`, `degraded-recovery`, `owner-ui-reachability` |
| `debian-12-systemd` | Debian 12 | deployment-owned-systemd | yes | not allowed | not allowed | `install-apply-verify`, `service-isolation`, `sandbox-isolation`, `deep-work-real-crash-endurance`, `deep-work-supervised-service`, `capability-pack-live-runtime`, `backup-recovery`, `knowledge-vault-backup-recovery`, `release-identity-refusal`, `disposable-gateway-removal`, `degraded-recovery`, `owner-ui-reachability` |

Automated/container lanes do not substitute for real system-service lanes. A declared lane does not claim that a fresh run exists for the current checkout.

## Capability profiles

| Profile | Email | Calendar | Social | Web | Operations | Frontier |
|---|---:|---:|---:|---:|---:|---:|
| `minimal` | off | off | off | off | off | off |
| `chief-of-staff` | on | on | off | on | off | off |
| `research` | off | off | off | on | off | off |
| `engineering-operator` | on | on | off | on | on | off |

Profiles select requested limbs; they do not supply credentials, policy, approval, target identity, provider qualification, or live evidence.

## Model-capacity guidance

| Memory band | Suggested local class | Context guidance | Fit guaranteed | Required validation |
|---|---|---|---:|---|
| `under-8` | remote-or-compact | start-small | no | `synthetic-task-contract`, `latency-and-memory-measurement`, `owner-quality-review` |
| `8-15` | compact-local | start-small | no | `synthetic-task-contract`, `latency-and-memory-measurement`, `owner-quality-review` |
| `16-31` | balanced-local | moderate | no | `synthetic-task-contract`, `latency-and-memory-measurement`, `owner-quality-review` |
| `32-63` | larger-local | expanded-after-measurement | no | `synthetic-task-contract`, `latency-and-memory-measurement`, `owner-quality-review` |
| `64-plus` | large-memory-local | expanded-after-measurement | no | `synthetic-task-contract`, `latency-and-memory-measurement`, `owner-quality-review` |

These are starting points, not a provider/model support promise. Measure the exact artifact, context, latency, memory, task quality, and owner acceptance.

## Current source-derived boundaries

- Compatibility row: **candidate**; see [generated status](../status.md) and [release evidence](../releases/evidence-index.md).
- Deep Work runtime enabled: `false`; admission boundary: `admission-only-no-runtime-no-tool-call-no-network-no-external-effects`.
- Publication requires the complete promotion gate set; qualification evidence does not grant publication or activation authority.
