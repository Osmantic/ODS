---
title: Verify and diagnose Pixel
doc_type: how-to
audience: [owner, operator]
feature_status: mixed
owners: [documentation, operations]
sources_of_truth: [OPERATIONS.md, DEPLOYMENT.md, CONTROL-SURFACE.md, scripts/verify.sh, scripts/preflight.sh, schemas/runtime-attestation-v1.schema.json]
last_verified_at: 2026-08-27
---

# Verify and diagnose Pixel

Use verification after activation, host maintenance, upgrades, recovery work, and at least monthly.

```bash
./pixel verify
```

Verification first invalidates the prior runtime attestation, then checks the active manifest and source identity, generated/OpenClaw configuration, service hardening, enabled connectors and brokers, loopback gateway, plugin resolution, sandbox boundary, and hashed model identity. Only a complete pass writes a new attestation.

## Interpret the result

| Result | Meaning | Next action |
|---|---|---|
| Command passes and a fresh attestation exists | The exact installed-host contract passed at that time | Continue to the applicable real model/tool and operator checks |
| Command fails | At least one required invariant did not pass | Preserve the exact error and inspect that subsystem |
| UI says unavailable | Retained evidence was absent, malformed, stale, or unsafe | Use the trusted terminal; do not infer healthy or disabled state |
| UI says prepared | Configuration/evidence is coherent but activation or external setup remains | Complete the named next step; prepared is not active |

The control surface treats runtime attestations as stale after a short freshness window. Refreshing the page is not a substitute for rerunning verification when a current host claim matters.

## Diagnose by subsystem

1. Run `./pixel doctor` for rounded host and generated-model orientation.
2. Run `./pixel limbs` to identify which optional services are actually expected.
3. For the reference profile, run `./pixel services status` and `./pixel services logs`.
4. Check the gateway and enabled units with the read-only commands in [monitoring and logs](monitoring-and-logs.md).
5. Compare the first failing verifier message with the active source/configuration boundary.
6. If installed bytes or plugin/broker resolution drifted, rebuild a plan and use transactional apply or rollback. Do not edit an immutable release or root-owned broker bytes in place.

## Failure classes

- **Host or dependency**: resolve bootstrap/Doctor prerequisites, then rerun plan and verify.
- **Endpoint unavailable**: repair the separately operated model/search endpoint without weakening gateway configuration.
- **Service hardening or listener**: keep the affected service offline until its unit and loopback boundary match the generated plan.
- **Credential isolation**: treat cross-identity readability as an incident, not a file-mode tweak to work around.
- **Source, release, plugin, or broker drift**: use exact plan/apply/rollback; never copy individual implementation files into the active release.
- **Projection or external-action uncertainty**: preserve claims/results/journal and reconcile before retry.
- **Receipt integrity**: preserve private evidence and use the owning recovery command; never delete a lock, claim, receipt, or token to force progress.

## Completion evidence

Diagnosis is complete when the root cause is named, the repair used a documented boundary, `./pixel verify` passes again, and any real model/tool or recovery acceptance affected by the failure is rerun separately.
