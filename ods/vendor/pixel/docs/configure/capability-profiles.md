---
title: Pixel capability profiles
doc_type: reference
audience: [owner, operator]
feature_status: mixed
owners: [documentation, configuration]
sources_of_truth: [RELEASE-MANIFEST.json, profiles/capabilities/, onboarding.example.json, scripts/configure.mjs, scripts/limbs.sh]
last_verified_at: 2026-08-27
---

# Pixel capability profiles

Capability profiles are reviewed starting defaults for limb selection. They do not grant credentials, install brokers, issue approvals, or prove capability.

Current profile names are generated in [status](../status.md). Their exact defaults live in `profiles/capabilities/` and must be reviewed from the checkout rather than copied into long-lived prose.

## Selection workflow

1. Choose the profile closest to the owner's intended work.
2. Review every resolved limb boolean in onboarding and the generated plan.
3. Explicitly disable unused limbs.
4. Prepare credentials/policies/installers only for enabled limbs.
5. After apply, run:

```bash
./pixel limbs
./pixel verify
```

Disabled limbs should be omitted from active plugin/service requirements and explicitly denied where applicable. Enabling a limb can add configuration requirements but does not silently activate its broker.

Changing a profile or individual limb requires reconfiguration, a new plan, apply, and verification. Separately revoke or preserve existing external credentials and broker state according to the feature's lifecycle; configuration alone does not erase them.
