---
title: Release failure guide
doc_type: runbook
audience: [operator, security-reviewer, maintainer]
feature_status: mixed
owners: [release, security, operations]
sources_of_truth: [QUALIFICATION.md, UPGRADE.md, UPSTREAM-INTAKE.md, SECURITY-ASSURANCE.md, scripts/package-release.sh, scripts/release-update.py]
last_verified_at: 2026-08-27
---

# Release failure guide

Release controls fail closed. Preserve the failed artifact and secret-free evidence, identify the first violated invariant, and repair the cause. Do not bypass a check, edit a generated result, delete a blocker, reuse a stale receipt, or relabel partial evidence.

| Failure | First checks | Recovery boundary |
|---|---|---|
| Dirty tree or exact-head mismatch | `git status`, recorded commit/tree, generated diff | Return to the reviewed exact head or restart evidence on the new head |
| Version/manifest/generated disagreement | Authoritative inputs and generator diff | Fix the source-of-truth input; regenerate twice; never hand-edit output |
| Package failure or existing signature | Linux environment, clean tree, artifact path, prior signed artifacts | Preserve prior signature; choose a new valid candidate identity rather than overwrite |
| Archive/SBOM/provenance mismatch | Envelope subjects, embedded manifest, source identity, checksums | Reject bundle and rebuild from clean exact source |
| Signature or trust failure | Namespace, identity, trust file, file modes, exact envelope bytes | Quarantine bundle; investigate before any re-sign attempt |
| Qualification gate missing/stale | Required gate list, source hash, timestamps, lane and evidence type | Re-run the exact gate and any consecutive full sequence it resets |
| Host-lane failure | Effective service properties, listener, users/groups/modes, logs, rollback state | Keep candidate isolated; repair and repeat focused plus full host lane |
| Real provider/tool failure | Configured route, credential custody, model/tool receipt, observed usage, returned tool result | Classify honestly; synthetic or model-off evidence cannot replace the live lane |
| Prepare/rehearse failure | Absolute private paths, ownership/modes, signature, eligibility, staged/rehearsal identity | No candidate execution has occurred; correct inputs and restart from inspect |
| Activation claim with no terminal result | Claim/marker identity and trusted-controller state | Follow `UPGRADE.md` recovery preview; never assume success or re-execute automatically |
| Activation failed with rollback armed | Activation result, rollback marker, service and active-version truth | Use the exact update-bound recovery/rollback path; do not manually swap releases |
| Rollback result requires recovery | Previous-version verification, receipts, marker, service state | Keep services contained and use the trusted recovery procedure |
| Cleanup failure | Terminal rollback/activation state and audit tombstone | Preserve evidence; cleanup is not a reason to falsify lifecycle outcome |
| Upstream integrity or contract blocker | Registry integrity, quarantine, diff blocker IDs, review binding | Block Candidate or repair/disposition non-security differences exactly; never waive risk |
| Evidence secret scan failure | Retained files and generation path | Quarantine evidence, rotate any real exposure, rebuild a content-free evidence set |

## Interrupted update

Start with read-only status and the preview operation named by `UPGRADE.md`. An interruption after a durable claim may intentionally forbid automatic re-execution. Do not remove markers, move release directories, restart arbitrary services, or invoke cleanup until the trusted controller has classified the state.

```bash
./pixel update-recover --help
./pixel update-rollback --help
./pixel update-cleanup --help
```

## Escalate

Treat credential/authority escape, cross-client disclosure, unauthorized side effect, identity bypass, sandbox escape, or unsafe recovery as a security incident. Contain the affected boundary and follow [incident response](../../INCIDENT-RESPONSE.md) plus [security assurance](../security/assurance.md). P0/P1 findings cannot be waived.

For normal operator symptoms outside a release transaction, use [troubleshooting](../operations/troubleshooting.md).
