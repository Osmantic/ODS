---
title: Pixel requirements
doc_type: tutorial
audience: [owner, operator]
feature_status: mixed
owners: [documentation]
sources_of_truth: [README.md, DEPLOYMENT.md, RELEASE-MANIFEST.json, QUALIFICATION-MATRIX.json, scripts/bootstrap.sh, scripts/pixel-doctor.py]
last_verified_at: 2026-08-27
---

# Pixel requirements

Use this page to decide whether a host and an owner are ready to begin. It describes the repository's documented deployment boundary; it is not evidence that this checkout has passed a fresh clean-host trial. Check [current repository status](../status.md) before treating a release as Supported.

## Host and owner

Start with:

- Ubuntu 24.04 LTS or Debian 12 on a host you administer;
- a dedicated, non-root Linux account that will own the Pixel deployment;
- Git and a POSIX shell;
- enough local storage for the checkout, immutable releases, private state, and any enabled container images;
- administrator access available only when bootstrap must install a missing host dependency.

Do not run the deployment as root. Root ownership is used only for narrowly isolated services and state installed by the reviewed commands.

Pixel Doctor reports rounded CPU, memory, storage, accelerator-vendor, container-readiness, and generated-model tiers. Those observations are advisory. They do not guarantee that a model fits, that a configured endpoint works, or that the host has passed release qualification.

## Deployment profile

Choose the infrastructure profile before configuration:

| Profile | Use it when | Additional host requirement |
|---|---|---|
| `prepared` | You already operate the model and search services Pixel will use | Those endpoints must be private and reachable from the deployed service identity |
| `reference` | Pixel should render and run the repository's pinned reference services | A working Docker installation and capacity for the digest-locked images are required |

The capability profile is a separate decision. It selects a starting set of limbs; it does not grant credentials or prove that an enabled limb is operational. See the [configuration reference](../reference/configuration.md).

## Private inputs you may need

The credential-free local page can collect ordinary onboarding choices, but it intentionally has no API-key, OAuth, SSH, provider-session, or backup-key fields. Depending on the features you enable, prepare these outside Git:

- the private model endpoint and credential or supported secret-provider binding;
- a client-owned Google OAuth application for Email or Calendar;
- private policy, target, and identity files for Operations;
- the selected Frontier authentication and budget-policy inputs;
- an `age` recipient and separately retained allowed-signers file for encrypted backups.

Use public placeholders in tickets and documentation. Do not paste these values into browser fields, committed JSON, or support messages.

## Inspect before changing the host

From the checkout, run:

```bash
./pixel doctor
./pixel bootstrap
```

`doctor` is a local-only readiness summary. `bootstrap` without `--apply` inspects the required pinned tools and artifacts. Neither command activates Pixel or proves a working model conversation.

If bootstrap reports missing requirements, review the proposed installations and then run:

```bash
./pixel bootstrap --apply
```

The mutating mode may request administrator access for missing host packages. It also verifies the pinned runtime, OpenClaw, plugins, sandbox, and enabled optional dependencies. Re-running it is intended to be idempotent.

## Ready-to-start test

You are ready for the [quickstart](quickstart.md) when:

- `./pixel doctor` gives you an understood host summary;
- `./pixel bootstrap` reports no unresolved requirement after any reviewed apply pass;
- you have chosen `prepared` or `reference` and a capability profile;
- every required private credential or policy has an owner and a safe external location; and
- you understand that configuration, planning, activation, verification, and a real model turn are separate evidence boundaries.

If those conditions are not true, stop before configuration. Fix the host or narrow the selected profile rather than bypassing a failed check.

## Related sources

- [Deployment runbook](../../DEPLOYMENT.md)
- [Current repository status](../status.md)
- [CLI reference](../reference/cli.md)
- [Security overview](../../SECURITY.md)
