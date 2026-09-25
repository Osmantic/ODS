---
title: Pixel status
doc_type: reference
audience: [owner, operator, contributor, security-reviewer, maintainer]
feature_status: mixed
owners: [documentation, release]
sources_of_truth: [VERSION, RELEASE-MANIFEST.json, QUALIFICATION-MATRIX.json, OPENCLAW-COMPATIBILITY.json]
generated_by: scripts/docs/generate-status.mjs
last_verified_at: generated
---

<!-- GENERATED FILE. Run `node scripts/docs/generate.mjs --write`; do not edit by hand. -->

# Pixel status

This page reports repository and release-contract facts. It does **not** report what is installed or active on any live host.

| Surface | Source-derived status | Evidence boundary |
|---|---|---|
| Repository version | `4.3.27` | `VERSION` and `RELEASE-MANIFEST.json` agree |
| Repository compatibility row | **candidate** | [LIVE-AUDIT-4.3.27.md](../LIVE-AUDIT-4.3.27.md) binds source `5983c27edb1c41d6e944abd13b6e6f780dd6cb4c` |
| OpenClaw compatibility | Canonical release pin is maintained in [OPENCLAW-COMPATIBILITY.json](../OPENCLAW-COMPATIBILITY.json) | This page does not duplicate an authored release pin |
| Latest Supported compatibility row | Pixel `4.3.26` | [LIVE-AUDIT-4.3.26.md](../LIVE-AUDIT-4.3.26.md) |
| Deep Work runtime | **development-disabled** | `deepWorkCapability.runtimeEnabled` is `false`; source presence and admission do not imply runtime authority |
| Documented host scope | Ubuntu 24.04 LTS; Debian 12 | Manifest and qualification matrix agree; this is not a fresh clean-host qualification |

## Capability profiles

- `minimal`
- `chief-of-staff`
- `research`
- `engineering-operator`

## Reading status words

- **Supported** applies only where the compatibility and qualification evidence says it does.
- **Candidate** is not merged, deployed, activated, or live-accepted merely because source or tests exist.
- **Development-disabled** means the source surface may be inspectable while runtime authority remains disabled.
- Generated, synthetic, or content-free evidence proves only the facts named by its contract.

For installed-host truth, use the documented local status and verification commands on that host. Do not infer live state from this repository page.
