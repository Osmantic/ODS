---
title: Pixel clean-host quickstart
doc_type: tutorial
audience: [owner, operator]
feature_status: mixed
owners: [documentation]
sources_of_truth: [README.md, DEPLOYMENT.md, CONTROL-SURFACE.md, pixel, scripts/bootstrap.sh, scripts/configure.mjs, scripts/plan.sh, scripts/apply.sh, scripts/verify.sh]
last_verified_at: 2026-08-27
---

# Pixel clean-host quickstart

This is the shortest owner-visible path from an empty supported host to a reviewed and verified deployment. It does not skip optional-limb setup, and it does not treat a model-off or synthetic check as a real conversation.

Before starting, complete the [requirements check](requirements.md) and review [current repository status](../status.md).

## 1. Obtain ODS and inspect its bundled Pixel source

```bash
git clone https://github.com/Osmantic/ODS.git ods
cd ods/vendor/pixel
./pixel bootstrap
./pixel bootstrap --apply
./pixel doctor
```

Expected result:

- bootstrap finishes without an unresolved pinned dependency;
- Doctor reports broad, non-identifying capacity and configuration tiers; and
- no service has been activated merely by running these checks.

If Doctor says the host is unsupported or a required check is unknown, do not interpret a successful shell exit as qualification. Resolve the warning before continuing.

## 2. Prepare configuration locally

Start the owner workspace in a dedicated terminal:

```bash
./pixel ui
```

Open the exact process-lifetime URL it prints. The listener is loopback-only, and the URL contains the private review token for this process. Review every setup section, save the settings, and select **Prepare configuration**.

Preparation runs the fixed configuration action. It does not activate services, contact a provider, accept credentials, or prove that the configured model works. Keep credentials in the trusted terminal or external secret path described by the relevant feature guide.

For a private JSON workflow instead of the page:

```bash
cp onboarding.example.json onboarding.json
${EDITOR:-vi} onboarding.json
./pixel configure --answers onboarding.json
```

Keep that file untracked. Configuration preserves a mode-`0600` canonical copy for later upgrades and backups. Re-running configuration requires `--force`; use it only after reviewing the new input.

## 3. Complete only the enabled feature setup

Before planning, follow the [first-deployment decision tree](first-deployment.md). In particular:

- start reference services only for the `reference` deployment profile;
- authorize and install the Source Broker only when a source limb is enabled;
- install Operations only after its private policy and targets are reviewed; and
- install Frontier only after its authentication and budget boundaries are understood.

Do not run every installer as a generic checklist. Disabled limbs should remain absent.

## 4. Build and review the plan

```bash
./pixel plan
```

Read the summary and reviewed hash. Planning checks the selected host, endpoints, plugins, images, schemas, required secrets, rendered deployment, and source identity. It does not activate the plan.

If any input changes after planning, discard the old conclusion and build a new plan.

## 5. Activate from the trusted terminal

```bash
# Requires exact confirmation; review before running.
./pixel apply --confirm
```

The browser cannot perform this step. Apply installs only the reviewed plan and verifies the result. If verification fails, the apply workflow restores the prior managed configuration, services, and release link before returning failure.

## 6. Verify the installed bytes and services

```bash
./pixel verify
```

A successful verify creates a fresh runtime attestation only after the installed manifest, clean source identity, active configuration, services, connectors, and gateway checks pass. The receipt does not prove model quality or capability.

Finish the [post-install checklist](post-install-checklist.md), then perform a [first real conversation](../use/first-conversation.md).

## Completion test

This quickstart is complete only when all of the following are true:

- the selected configuration was reviewed;
- optional installers ran only for enabled features;
- the plan hash reviewed by the operator is the plan that was applied;
- `./pixel verify` succeeds on the installed host; and
- the owner can explain why verification is deployment evidence, not real-backend conversation evidence.

For rollback, backup, or uninstall decisions, use the [operations guide](../../OPERATIONS.md), [upgrade and rollback guide](../../UPGRADE.md), and [support boundary](../../SUPPORT.md). Do not remove private state manually.
