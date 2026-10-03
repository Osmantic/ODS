---
title: Build a signed Pixel projection limb
doc_type: tutorial
audience: [contributor, security-reviewer, maintainer]
feature_status: candidate
owners: [documentation, architecture, security]
sources_of_truth: [CUSTOMIZATION.md, scripts/limb-kit.py, schemas/limb-pack-v1.schema.json, tests/test_limb_kit.py, security-evals/limb-kit-isolation/, security-evals/limb-kit-pressure/]
last_verified_at: 2026-08-27
---

# Build a signed Pixel projection limb

The limb kit is a development preview for offline, credential-free projection workers. It is not a stable third-party compatibility promise.

## Generate an inert skeleton

```bash
./pixel limb-kit generate host-health /absolute/work/host-health --name "Host health"
./pixel limb-kit validate /absolute/work/host-health
```

The generated tree includes a fixed gateway adapter, offline worker, hardened service/timer templates, manifest, projection contract, and negative-case baseline. Customize only the declared worker surface. The adapter remains generated and read-only.

The worker may read its signed pack and own state and write only its bounded projection. Networked or credential-bearing workers are rejected. Do not include tokens, keys, client data, raw source corpora, target hostnames, or provider configuration.

## Add only needed declarations

Use the separate `add-local`, `add-operations`, and `add-frontier` subcommands described in the [CLI reference](../reference/cli.md), then validate again. These create data-only policy declarations; they do not execute in the worker or bypass their owning brokers.

## Sign and verify

```bash
# Requires exact confirmation; review before running.
./pixel limb-kit sign /absolute/work/host-health \
  --signing-key /secure/pixel-limb-publisher --identity owner@example --confirm
./pixel limb-kit verify /absolute/work/host-health \
  --allowed-signers /secure/pixel-limb-allowed-signers --identity owner@example
```

Keep the publisher key private and provision the allowed-signers file independently. A signature binds the canonical tree; it does not make worker logic safe or enable it.

## Install and enable separately

Installation verifies and places the pack but leaves it disabled. Enablement revalidates the receipt, installs exact worker units under a non-login pack identity, grants only projection read access to the explicitly named gateway user, runs the worker, enables its timer, and then updates private onboarding/registry. Follow the exact commands in [CUSTOMIZATION.md](../../CUSTOMIZATION.md), then configure, plan, apply, and verify the deployment.

## Test and remove

Run limb-kit unit, isolation, pressure, malformed projection, stale data, injection, disabled-limb, upgrade, rollback, and residue tests. Disable verifies installed unit bytes before removal. Remove refuses an enabled pack or leftover units and deletes only managed pack/projection state after the registry transaction.
