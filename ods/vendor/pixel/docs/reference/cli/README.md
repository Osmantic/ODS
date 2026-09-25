---
title: Pixel CLI command families
doc_type: reference
audience: [owner, operator, contributor, maintainer]
feature_status: mixed
owners: [documentation]
sources_of_truth: [pixel]
generated_by: scripts/docs/generate-cli-reference.mjs
last_verified_at: generated
---

<!-- GENERATED FILE. Run `node scripts/docs/generate.mjs --write`; do not edit by hand. -->

# Pixel CLI command families

The dispatcher exposes **110 user-facing commands**, plus `help`. Each family page records purpose, state/mutation behavior, confirmation semantics, command-specific exit handling, and the normative dispatch source.

| Family | Commands | Status boundary |
|---|---:|---|
| [Install, configure, and maintain](install-configure-maintain.md) | 18 | Mixed; installed-host state and each command contract control the claim. |
| [Sources and external actions](sources-external-actions.md) | 6 | Mixed; projection, proposal, approval, and actuator authority remain separate. |
| [Operations](operations.md) | 13 | Mixed; private policy, grants, approval, lease, and target identity bound authority. |
| [Frontier](frontier.md) | 9 | Mixed; a configured provider route still needs the applicable live qualification. |
| [Qualification, release, and migration](qualification-release-migration.md) | 33 | Mixed; signatures, qualification, publication, staging, activation, and recovery are distinct authorities. |
| [Customization](customization.md) | 3 | Mixed; status and authority depend on the selected extension seam. |
| [Deep Work and local work](deep-work-local-work.md) | 29 | Deep Work runtime is development-disabled; source-visible preparation and qualification commands do not enable it. |

Root help is a routing contract, not permission to run a privileged, destructive, provider, or external-effect command. Use preview/read-only modes and the owning guide first.
