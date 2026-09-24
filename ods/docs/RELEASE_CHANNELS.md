# Release Channels

ODS moves quickly because installer, hardware, model, and service
ecosystems move quickly. Treat each ref intentionally.

## ODS V3 Pre-Release

V3 is in **public testing and refinement ahead of the official V3 launch**.
The published source snapshot is `v3.0.0`, named **ODS V3 Pre-Release** on GitHub.
It pins commit `bec0c42e7c9885a5aecd419a166a6a81e0d37236` on September 24, 2026.
The tag is immutable. Full fleet qualification is still in progress: publication
and the GitHub Latest label do not mark the official V3 launch or certify user
journeys or operational recovery. See [V3 Pre-Release notes](RELEASE_NOTES_3.0.0.md)
for the acceptance boundaries.

This presentation change preserves the existing tag, numeric product versions,
and GitHub release classification used by update discovery. The public
pre-release phase is described by the release name and these notes.

`main` also identifies as `3.0.0` and continues receiving qualification fixes.
The manifest's `release.stable_version` records the published non-prerelease
version used by documentation checks; it does not select a maintenance branch
or prove fleet acceptance. `release.channel` remains `development` while this
qualification campaign is incomplete.

## Channels

| Channel | Use it for | Expectation |
|---|---|---|
| `v3.0.0` | Reproducing the V3 Pre-Release source snapshot | Read its qualification limitations; later main fixes are not included. |
| `main` | Active development, V3 fixes and validation candidates | Can change many times per day. Bind tests to the exact commit. |
| `release/2.6.x` | Patch-only maintenance for the older 2.6 line | Narrow security or operator-continuity fixes for deployments remaining on 2.6. |
| `release/2.5.x` | Older 2.5 maintenance baseline | Critical security or operator-continuity fixes only. |
| Tagged releases | Reproducible source snapshots | Publication alone does not establish an acceptance result; inspect each release's receipt. |
| Pinned commits | Security reviews, internal mirrors, candidates and hotfix baselines | Record the commit with its validation receipt. |
| Downstream forks | Custom hardware images, labs, private extensions and offline mirrors | Record upstream ref, downstream changes and local validation results. |

## Default Guidance

- New users can follow the README quickstart, which tracks `main`.
- Pin `v3.0.0` to reproduce the V3 Pre-Release source, or an audited later commit
  to include subsequent fixes. Do not relabel earlier tests as a later-head pass.
- V3 fixes target `main`. No `release/3.x` branch is implied by the new tag.
- Only fixes specifically needed on the older 2.6 line should target
  `release/2.6.x`; merge applicable fixes forward into `main`.
- Hardware builders and downstream operators must add their own validation
  receipts for local changes. Do not treat moving `main` as a frozen API.

## Older-Line Patch Policy

Use an older maintenance lane for narrow installer, lifecycle, security,
model-routing or data-safety fixes affecting that line. New capabilities,
changed defaults, broad refactors and speculative changes belong on `main`.
Choose the lane based on the affected installed version, and preserve the
upstream/downstream evidence for every backport.

## Fork-And-Pin

Use this when you want a stable local edition and do not need frequent upstream
updates.

1. Choose a tagged release or audited commit.
2. Record it in `DOWNSTREAM.md`.
3. Apply your local extensions, model catalog changes, branding, or docs.
4. Run the validation subset from [HIGH_RISK_CHANGE_MAP.md](HIGH_RISK_CHANGE_MAP.md).
5. Update only on an explicit cadence you control.

## Fork-And-Mirror

Use this when you want to stay closer to upstream while still owning the
operational substrate.

1. Mirror the upstream repository.
2. Mirror allowed Docker images, model artifacts, and checksums.
3. Track upstream tags or selected commits, not every push to `main`.
4. Re-run downstream validation after each upstream merge.
5. Keep release receipts with both upstream and downstream refs.

See [OFFLINE_AND_MIRRORING.md](OFFLINE_AND_MIRRORING.md) for artifact details.

## Validation Receipts

A ref is most useful when paired with a receipt:

```text
Upstream ref:
Downstream ref:
Install command:
Hardware / OS:
Services enabled:
Model selected:
Validation run:
Skipped or deferred surfaces:
Known local patches:
```

Use [RELEASE_VALIDATION.md](RELEASE_VALIDATION.md) to understand upstream User
Green gates and [VALIDATION_REPRODUCIBILITY.md](VALIDATION_REPRODUCIBILITY.md)
to reproduce the relevant layers in your own environment.
