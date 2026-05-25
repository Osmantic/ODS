# Release Channels

Dream Server moves quickly because installer, hardware, model, and service
ecosystems move quickly. Treat each ref intentionally.

## Channels

| Channel | Use it for | Expectation |
|---|---|---|
| `main` | Active development, contributor work, rapid fixes, validation candidates | Can change many times per day. Read diffs and run focused validation before using it for an appliance or fork release. |
| Tagged releases | Stable installs, downstream forks, lab images, appliance baselines | Preferred source for users and downstream operators who want a reproducible starting point. |
| Pinned commits | Security reviews, internal mirrors, release candidates, emergency hotfix baselines | Valid when the commit and validation receipt are recorded together. |
| Downstream forks | Custom hardware images, labs, private extensions, offline mirrors | Should record upstream ref, downstream changes, and local validation results. |

## Default Guidance

- New users can follow the README quickstart.
- Operators who want reproducibility should pin a release tag.
- Forks should either fork-and-pin or fork-and-mirror.
- Hardware builders should treat upstream release receipts as evidence, then add
  their own validation receipt for local changes.
- Do not treat `main` as a frozen API or appliance channel.

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
