---
title: Plan the first Pixel deployment
doc_type: how-to
audience: [owner, operator]
feature_status: mixed
owners: [documentation]
sources_of_truth: [DEPLOYMENT.md, CONTROL-SURFACE.md, CLIENT-ONBOARDING.md, OPERATIONS-LIMB.md, FRONTIER-LIMB.md, onboarding.example.json, scripts/configure.mjs, scripts/plan.sh, scripts/apply.sh]
last_verified_at: 2026-08-27
---

# Plan the first Pixel deployment

Use this decision tree after bootstrap and before `./pixel plan`. Its purpose is to make disabled capabilities stay disabled and to keep credentials, activation, and consequential authority in their separate trusted paths.

## 1. Choose how to author onboarding

Use `./pixel ui` for credential-free ordinary settings. Use a private, untracked copy of `onboarding.example.json` when you need advanced paths, an external credential binding, signed packs, or private policies.

The two paths converge on the same configuration boundary:

```bash
./pixel configure --answers /secure/path/onboarding.json
```

The local page may also run the fixed configure action after an exact preview. Neither route activates the deployment.

## 2. Choose infrastructure and capability separately

The infrastructure profile answers where dependencies run:

- `prepared`: use already-operated private model and search endpoints;
- `reference`: render and run Pixel's pinned reference services.

The capability profile answers which starting limbs are selected: `minimal`, `chief-of-staff`, `research`, or `engineering-operator`. Review the resolved limb choices rather than assuming the profile name grants a capability.

Record the model provider, model ID, private base URL, context limits, and credential binding accurately. Pixel can verify that configured identity is preserved in a turn receipt; it cannot infer model quality from the name.

## 3. Follow the enabled branches

### Reference services

Only for `deploymentProfile: reference`:

```bash
# Requires exact confirmation; review before running.
./pixel services up --confirm
```

Skip this for `prepared`. A prepared deployment must instead prove its private endpoints are available to the deployed service identity.

### Email, Calendar, or social sources

If any source limb is enabled, complete the client-owned OAuth setup first, then use the trusted terminal:

```bash
./pixel authorize
# Requires exact confirmation; review before running.
./pixel source-broker --confirm
```

The gateway must not receive the raw OAuth credential. Follow [client onboarding](../../CLIENT-ONBOARDING.md) and the source-broker custody steps in the [deployment runbook](../../DEPLOYMENT.md).

### Operations

If Operations is enabled, first create and review the private policy, targets, host identities, and action mappings. Then install only that reviewed boundary:

```bash
# Requires exact confirmation; review before running.
./pixel ops-broker --confirm
```

Do not treat installation as authority for arbitrary shell or production changes. Follow [Operations Limb](../../OPERATIONS-LIMB.md) for target enrollment, transport isolation, approvals, leases, and acceptance.

### Frontier

If Frontier is enabled, select ChatGPT-plan access or separately billed API access and the corresponding local budget policy. Authentication remains outside the browser. After its private inputs are ready:

```bash
# Requires exact confirmation; review before running.
./pixel frontier-broker --confirm
```

This installs the bounded review broker. It does not prove a live provider turn. See [Frontier Limb](../../FRONTIER-LIMB.md) and its separate qualification procedure.

### Disabled branches

If a limb is disabled, do not provision its credential, run its installer, or add its policy merely for completeness. Confirm the rendered configuration and `./pixel limbs` keep its tools denied.

## 4. Review and activate

Build the plan only after every enabled branch is prepared:

```bash
./pixel plan
```

Review:

- the infrastructure and capability profiles;
- every enabled and disabled limb;
- the model and search endpoint identities;
- required secrets as presence checks, never their values;
- the rendered service set and immutable image/plugin identities;
- the source commit/tree and exact plan hash.

Activate in the trusted terminal only when that review matches the intended deployment:

```bash
# Requires exact confirmation; review before running.
./pixel apply --confirm
```

If the source, onboarding, private policy, credential binding, endpoint, or rendered output changes, rebuild and review the plan. Do not reuse an earlier approval.

## 5. Verify and hand off

```bash
./pixel verify
```

Then give the owner:

- the installed profile and enabled-limb inventory;
- the location and custody owner of private onboarding, policies, credentials, backup identity, and allowed signers without copying their contents;
- the [post-install checklist](post-install-checklist.md);
- the [first-conversation procedure](../use/first-conversation.md);
- the [operations](../../OPERATIONS.md), [incident response](../../INCIDENT-RESPONSE.md), [upgrade](../../UPGRADE.md), and [support](../../SUPPORT.md) guides.

Deployment handoff is not complete until the owner understands which status is repository evidence, installed-host evidence, real-backend evidence, and operator acceptance.
