# Validation Reproducibility

ODS's full release-grade validation uses public CI, distro labs, and a
private real-hardware fleet. Not every fork can reproduce every lane, but every
operator should be able to understand what was tested and run an appropriate
subset for their own hardware.

Use this guide with [RELEASE_VALIDATION.md](RELEASE_VALIDATION.md),
[VALIDATION-MATRIX.md](VALIDATION-MATRIX.md), and
[HIGH_RISK_CHANGE_MAP.md](HIGH_RISK_CHANGE_MAP.md).

## Validation Layers

| Layer | Publicly reproducible | Purpose |
|---|---|---|
| Static checks | Yes | Syntax, contracts, docs, manifest health, config drift |
| Dashboard tests | Yes | UI/API unit and build confidence |
| Compose matrix | Yes | Validates stack file combinations without full hardware |
| Distro containers | Mostly | Package-manager and bootstrap behavior across distro families |
| Incus/systemd VMs | Lab-dependent | Docker daemon and systemd behavior |
| Real hardware fleet | Hardware-dependent | GPU/backend, model, lifecycle, and full-product behavior |
| Full-model capabilities | Hardware/model-dependent | Proves agent, search, files, code, model identity, and context after model swap |

## Minimum Local Audit Set

From `ods/`:

```bash
git diff --check
python scripts/audit-extensions.py --project-dir .
python scripts/validate-generated-configs.py
python scripts/validate-golden-paths.py
python scripts/check-dependency-pins.py
```

Add dashboard checks when dashboard code or dashboard-api behavior changes.

## Reproducing Installer Confidence

For a local fork or appliance candidate:

1. Start from a clean checkout.
2. Record the commit.
3. Run a fresh install on representative hardware.
4. Confirm service health.
5. Exercise dashboard model and extension flows.
6. Confirm Hermes can answer a seed prompt.
7. Wait for full model download and hot-swap.
8. Run capability probes or an equivalent manual checklist.
9. Run idempotent reinstall, `ods restart`, and `ods doctor`.
10. Record skipped surfaces honestly.

If you cannot run the full fleet, say which hardware and distro classes were not
tested.

## Capability Deferrals

Capability checks should not fail just because a large model is still
downloading. A valid report distinguishes:

- bootstrap model active;
- full model downloading;
- full model downloaded but not served yet;
- full model served and capability probes passed;
- full model served and a probe failed.

Do not mark a release as fully validated until deferred full-model checks have
resolved or are explicitly excluded from that release.

## Owner-Card And Talk Probes

ODS Talk owner-card probes only gate releases when the owner-card surface is
enabled and `ods-proxy` is healthy. A default non-LAN install should skip
those probes rather than failing a surface the user did not expose.

If your fork depends on owner-card access, add a dedicated validation lane for
the LAN/proxy path.

## Recording A Validation Receipt

Use a concise receipt:

```text
Project ref:
Downstream ref:
Date:
Hardware:
OS/distro:
Install command:
Services enabled:
Model selected:
Gates passed:
Gates skipped/deferred:
Known limitations:
Logs/report path:
```

Validation receipts are most valuable when they are boring, specific, and easy
to compare with the next run.

## How To Read Upstream Claims

Upstream validation proves the upstream candidate on the tested hardware and
software surface. It is a strong signal for forks, but it does not automatically
validate:

- private extensions;
- changed model catalogs;
- changed compose overlays;
- custom LAN exposure;
- different Docker Desktop versions;
- different GPU drivers;
- unsupported distros or hardware.

Forks should cite upstream receipts and add their own downstream receipt for
local changes.
