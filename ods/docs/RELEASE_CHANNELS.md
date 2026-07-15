# Release Channels

ODS moves quickly because installer, hardware, model, and service
ecosystems move quickly. Treat each ref intentionally.

## Current Stable

The current stable release is `v2.5.3`.

Use `v2.5.3` for normal installs, downstream appliance baselines, lab images,
and forks that want a known-good starting point. Use `release/2.5.x` only for
patches that should preserve the `v2.5.3` user experience while fixing a
stable-user problem.

## Channels

| Channel | Use it for | Expectation |
|---|---|---|
| `main` | Active development, contributor work, rapid fixes, validation candidates | Can change many times per day. Read diffs and run focused validation before using it for an appliance or fork release. |
| `release/2.5.x` | Patch-only maintenance for the current stable line | Only accepts stable hotfixes, security fixes, and docs that clarify current stable behavior. No new feature work. |
| Tagged releases | Stable installs, downstream forks, lab images, appliance baselines | Preferred source for users and downstream operators who want a reproducible starting point. |
| Pinned commits | Security reviews, internal mirrors, release candidates, emergency hotfix baselines | Valid when the commit and validation receipt are recorded together. |
| Downstream forks | Custom hardware images, labs, private extensions, offline mirrors | Should record upstream ref, downstream changes, and local validation results. |

## Default Guidance

- New users can follow the README quickstart.
- Operators who want reproducibility should pin a release tag. Today that means
  `v2.5.3` unless a newer stable release has been published.
- Stable hotfixes should target `release/2.5.x` first, then be merged forward
  or cherry-picked into `main`.
- Forks should either fork-and-pin or fork-and-mirror.
- Hardware builders should treat upstream release receipts as evidence, then add
  their own validation receipt for local changes.
- Do not treat `main` as a frozen API or appliance channel.

## Stable Patch Policy

Use the stable patch lane when the change fixes a real problem for users on the
current stable release. Good candidates include:

- installer, bootstrap, reinstall, restart, or doctor regressions
- security exposure, credential, auth, or network-binding fixes
- dashboard, ODS Talk, model download, model swap, or lifecycle breakage in a
  supported default path
- docs that prevent current stable users from taking the wrong action

Do not target `release/2.5.x` for:

- new bundled services or changed default services
- broad installer, CLI, manifest, or compose refactors
- new model-routing policy unless the current policy is broken
- dependency churn that is not required for a stable fix
- speculative polish that can wait for the next minor release

The stable branch should stay boring. If a change needs a product debate, a new
capability, or broad retesting outside the broken surface, it belongs on `main`
or the next minor release train first.

## Triage Questions

Before opening or reviewing a PR, classify the lane:

1. Is this broken for users on the current stable release?
2. Does it affect install, lifecycle, security, model download/swap, GPU
   routing, dashboard proxy, ODS Talk, or data safety?
3. Does it change a default behavior?
4. Can it wait for the next minor release?

If the answer to the first question is yes and the fix is narrow, consider
`release/2.5.x`. If the answer is no, use `main`. If the change is broad or
feature-shaped, use the next minor milestone.

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
