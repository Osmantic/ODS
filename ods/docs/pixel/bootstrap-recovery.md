# Recovering an interrupted Linux bootstrap promotion

A killed bootstrap can leave Pixel admission held even when the host-agent model-switch journal is absent. `ods model recover` now checks the separate root coordinator journal. A held legacy transaction is pending; unavailable or malformed coordinator evidence is unavailable, never idle.

## Confirm the current model

Run `ods model recover` as the installation owner. For a recoverable legacy hold, it prints an explicitly quoted command containing the transaction ID and the current configured GGUF, model ID, and context:

```text
ods model recover --bootstrap-current TRANSACTION GGUF MODEL CONTEXT
```

Review those values before running the command. This chooses the current inference route as the recovery target. The selected values must still match the installed local configuration exactly. Model IDs can contain provider separators such as `namespace/model:latest`; GGUF basenames may contain spaces and brackets. Keep the printed shell quoting.

Recovery proves that inference serves that model and context, checks the `ods/current` route, reacquires both gates under the original root transaction, reconciles the native Pixel configuration to the selected model, restarts and verifies the gateway, and only then uses the original protected finish operation. A prior native rewrite is supported. The original root baseline digest is retained. An interrupted finish can be retried with the same command and target.

If bootstrap died before inference changed, the current bootstrap model can be confirmed and recovered even when the intended full model differs. If native config and inference differ but the current configured inference is healthy, recovery repairs native config to that proven current target.

The command does not activate or restart unavailable inference. If the actual inference model differs from the current configuration, inference cannot be proved, a gateway check fails, or configuration changes during proof, recovery retains the hold. Repair inference under an owner-supervised maintenance procedure before retrying; do not remove journals or use `--release-unverified` for a bootstrap hold. A different target after recovery has begun requires inspection of the existing root recovery record, not an override.

Active lifecycle work, a different transaction, pending modern model switches, source-upgrade custody, and unfinished protected installer release migrations are refused. A completed protected release can finish only with its exact configuration digest and outcome. An old legacy journal has no trustworthy bootstrap provenance: the explicit current-target ceremony uses the managed installation boundary and its existing hold, never an assumed old snapshot.

## Snapshot evidence

New bootstraps create and fsync `data/bootstrap-upgrade-active-config.TRANSACTION/manifest.json` and original configuration files before requesting admission. The root coordinator binds the exact manifest digest, intended target, transaction ID, and original native configuration before either gate is acquired. A lost begin reply therefore leaves discoverable root custody. The snapshots are retained after completion for inspection.

For owner-supervised inspection of a new transaction's snapshot, use the installation's helper while that exact transaction remains pending:

```text
python3 bin/pixel_bootstrap_recovery.py verify INSTALL_DIRECTORY TRANSACTION
```

This accepts only the fixed snapshot directory whose manifest digest is bound by the authenticated root coordinator. Missing, changed, unsafe, or unrelated snapshots fail. Verification is a prerequisite for any manual restore, not authority to restore while lifecycle work is active or a guarantee that restored inference will run. Do not select backups by timestamp, directory order, or a similar filename. Old orphan transactions without root-bound metadata have no supported automatic snapshot restore. The recovery command itself completes a proven current route; it does not perform a manual restore.

## Lifecycle exclusion and deployment

Linux shell bootstrap/installer and the host agent now use the same OS flock keyed by the canonical install path and installation owner UID. Host requests do not wait for it; shell acquisition waits at most 300 seconds. Lock helpers expire when their parent process dies. Root opens the private directory and file without following symlinks, validates custody and file type, and changes ownership only for newly created descriptors. Precreated symlinks, hardlinks, FIFOs, public directories and wrong-owner entries are refused.

There is an unresolved rollout boundary for installations already stuck on an older bundle: their source-update and coordinator-install guards correctly refuse to replace code while a legacy hold exists. Normal update/reinstall therefore cannot deliver this new recovery command and root operation to that installation yet. This patch does not bypass those guards. A separately reviewed, journal-preserving recovery-bundle migration or recovery using the existing installed operations is required before claiming old-install remediation. Installing this candidate first and then creating a hold proves only the candidate runtime path.

Upgrade all participating helpers together and finish any older installer/bootstrap first. An older root-run installer may still hold `/tmp/ods-model-lifecycle-0`, while the new helpers use the installation owner's UID. Those different lock paths do not exclude each other. Controlled installation and recovery must not overlap an older running installer or bootstrap. Do not delete a live lock path to migrate it. A custom lock-root override must be the same trusted path in every participating process.

## Qualification boundary

The focused controls run production host recovery, root bridge journal/proof logic, and shell reconciliation against real temporary native files, with physical inference and services simulated. They cover changed native bytes, before-inference interruption, lost begin/finish replies, each gate release failure, source/release conflicts, stale/missing snapshots, actual process death, OS lock contention, CLI arguments, and Unix peer authentication. Separate UID-0 container controls exercise privileged lock custody.

Installed acceptance still requires a controlled Linux interruption/recovery journey with actual inference and gateway verification, an authenticated Portal turn, a subsequent model switch, and reinstall/uninstall checks. Source tests and root-container controls do not establish those live results.
