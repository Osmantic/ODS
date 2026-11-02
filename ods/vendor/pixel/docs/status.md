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
| Repository version | `4.3.41` | `VERSION` and `RELEASE-MANIFEST.json` agree |
| Repository compatibility row | **candidate** | [ODS-QUALIFICATION-4.3.41.md](../ODS-QUALIFICATION-4.3.41.md) binds source `cc49a94b991b99e254e0b5555e6b21740a62dc09` |
| OpenClaw compatibility | Canonical release pin is maintained in [OPENCLAW-COMPATIBILITY.json](../OPENCLAW-COMPATIBILITY.json) | This page does not duplicate an authored release pin |
| Latest Supported compatibility row | Pixel `4.3.26` | `LIVE-AUDIT-4.3.26.md` (not included in this source export) |
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
