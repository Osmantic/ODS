---
title: Update and roll back Pixel
doc_type: runbook
audience: [owner, operator]
feature_status: mixed
owners: [documentation, release, operations]
sources_of_truth: [UPGRADE.md, OPERATIONS.md, RELEASE-MANIFEST.json, scripts/release-update.py, scripts/prepare-release-update.sh, scripts/rehearse-release-update.sh, scripts/activate-release-update.sh, scripts/rollback-release-update.sh, scripts/recover-release-update.sh, scripts/cleanup-release-update.sh, scripts/archive-release-update.sh]
last_verified_at: 2026-08-27
---

# Update and roll back Pixel

Use only a published, Supported, signed release bundle obtained with its publisher trust anchor through an independent channel. A Candidate, qualification signature, unsigned archive, or successful rehearsal is not activation authority. Check [repository status](../status.md) and the exact release evidence.

## 1. Back up and define the window

Create, validate, and rehearse a fresh private-state backup. Record the current installed release, enabled limbs, plan/attestation state, owner, maintenance window, rollback decision-maker, and observation period.

## 2. Inspect without extraction or execution

```bash
./pixel update-inspect \
  --envelope /absolute/intake/pixel-VERSION.update.json \
  --allowed-signers /absolute/trust/pixel-release-allowed-signers \
  --identity pixel-release
```

Inspection verifies the production signature and signed artifact/source/compatibility bindings under strict archive limits. Its `verified` receipt does not authorize installation.

## 3. Prepare private staging

```bash
# Requires exact confirmation; review before running.
./pixel update-prepare \
  --envelope /absolute/intake/pixel-VERSION.update.json \
  --allowed-signers /absolute/trust/pixel-release-allowed-signers \
  --identity pixel-release \
  --confirm
```

Record the returned stable candidate ID. Preparation re-verifies and copies exact artifacts into owner-private staging; it does not extract or execute candidate code.

## 4. Rehearse the exact candidate

```bash
# Requires exact confirmation; review before running.
./pixel update-rehearse \
  --candidate-id pixel-VERSION-FULL_ENVELOPE_SHA256 \
  --allowed-signers /absolute/trust/pixel-release-allowed-signers \
  --identity pixel-release \
  --confirm
```

Rehearsal safely materializes bounded regular files and parses fixed source surfaces with installed trusted interpreters. It performs no network operation and does not change the active deployment.

## 5. Preview and activate

```bash
./pixel update-activate --preview \
  --candidate-id pixel-VERSION-FULL_ENVELOPE_SHA256 \
  --allowed-signers /absolute/trust/pixel-release-allowed-signers \
  --identity pixel-release
```

Review the complete preview and copy its full activation hash into the exact confirmed command:

```bash
# Requires exact confirmation; review before running.
./pixel update-activate \
  --candidate-id pixel-VERSION-FULL_ENVELOPE_SHA256 \
  --allowed-signers /absolute/trust/pixel-release-allowed-signers \
  --identity pixel-release \
  --activation-hash FULL_ACTIVATION_SHA256 \
  --confirm
```

Activation creates a durable single-use claim before running the candidate's fixed configure, bootstrap, plan, transactional apply, and verification sequence. After success, run one harmless real model turn and one applicable tool check.

## 6. Roll back when required

Obtain a fresh rollback preview against the unchanged activation state, then repeat its exact hash:

```bash
./pixel update-rollback --preview \
  --candidate-id pixel-VERSION-FULL_ENVELOPE_SHA256 \
  --allowed-signers /absolute/trust/pixel-release-allowed-signers \
  --identity pixel-release \
  --activation-hash FULL_ACTIVATION_SHA256

# Requires exact confirmation; review before running.
./pixel update-rollback \
  --candidate-id pixel-VERSION-FULL_ENVELOPE_SHA256 \
  --allowed-signers /absolute/trust/pixel-release-allowed-signers \
  --identity pixel-release \
  --activation-hash FULL_ACTIVATION_SHA256 \
  --rollback-hash FULL_ROLLBACK_SHA256 \
  --confirm
```

The trusted current controller restores the preceding release/configuration transaction. Separately held broker state, remote targets, provider credentials, policies, and workspace/database content are not silently reversed.

## Interruption and cleanup

Use `./pixel update-recover --preview` with the same candidate/trust/activation inputs after an interrupted receipt. Its confirmed exact-hash mode may finalize observed activation or rollback state; it cannot rerun candidate code or resume a partial deployment.

After a completed rollback, use `./pixel update-cleanup --preview` and its exact confirmed cleanup hash. Cleanup removes only verified update workspaces while retaining the audit tombstone. Never infer a hash, reuse a preview after state changes, or manually delete staging/claim/receipt files.

If all eight staging slots are occupied and no journey qualifies for cleanup, `./pixel update-archive --preview` may preserve one exact terminal failed rollback outside the bounded staging namespace. It rejects active, live-marker-bound, reactivation-bearing, linked, attributed, foreign, special-mode, or drifting evidence. Confirm only the exact returned archive hash. The operation first publishes an immutable replay-tombstone claim, then atomically moves all three evidence roots into the fixed owner-private same-filesystem archive, rehashes them, and records completion only after the staging count is seven. It never deletes the failed chain.

The narrower `./pixel update-reactivation-archive --preview ...` operator exists only for a terminal failed reactivation that proves no live mutation began. It requires an exact newer production-signed controller and binds that controller's source, package, signature, dispatcher, engine, and command hashes. At the exact eight-candidate capacity boundary, an interactive exact-hash confirmation may atomically preserve the candidate, rehearsal, activation, and reactivation roots in the fixed same-filesystem archive. Any live-mutation marker, retry chain, rollback artifact, receipt drift, controller drift, service outage, or ambiguous legacy receipt fails closed. A frozen compatibility bridge recognizes only the exact historical receipt identified by the release audit linked from [repository status](../status.md).

The complete command shapes and compatibility transitions remain in [UPGRADE.md](../../UPGRADE.md).
