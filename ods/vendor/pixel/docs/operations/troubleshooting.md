---
title: Troubleshoot Pixel
doc_type: how-to
audience: [owner, operator]
feature_status: mixed
owners: [documentation, operations]
sources_of_truth: [OPERATIONS.md, DEPLOYMENT.md, INCIDENT-RESPONSE.md, UPGRADE.md, CONTROL-SURFACE.md, scripts/verify.sh]
last_verified_at: 2026-08-27
---

# Troubleshoot Pixel

Start with the first failing boundary. Do not delete locks, receipts, claims, tokens, staging directories, or service state to make a command proceed.

| Symptom | Safe interpretation | Next step |
|---|---|---|
| Bootstrap reports missing tools | Host prerequisites are incomplete | Review and run `./pixel bootstrap --apply`, then repeat Doctor/bootstrap |
| Doctor says unsupported or unknown | Host evidence is outside or incomplete for the documented boundary | Resolve the host issue; do not treat advisory output as qualification |
| Plan fails after configuration | A schema, secret-presence, endpoint, image/plugin, version, or source identity check failed | Correct the authored input and rebuild the plan; do not edit generated output |
| Deployment lock is held | Another mutation may be active or recovering | Identify the owning process and wait/reconcile; never kill it or remove the lock without incident review |
| Verify reports source/plugin/broker drift | Installed bytes no longer match the active immutable release | Keep affected services offline and use transactional apply/rollback |
| Gateway is not loopback-only or unauthenticated access works | Security boundary failure | Stop the gateway and follow incident response |
| Chat says not connected | Exact private onboarding/runtime is not configured | Finish configuration; this is not yet a model failure |
| Chat says unavailable | Runtime/history/receipt custody failed validation | Preserve private evidence and diagnose; no partial history is authoritative |
| Turn is interrupted | Pixel retained interruption rather than an answer | Reconcile the original turn before retrying |
| Source action is processing/uncertain | Provider outcome may be indeterminate | Inspect its protected snapshot, claim/result, action journal, and unit journal; do not retry/delete |
| Operations target identity changed | Trust pin no longer proves the target | Quarantine the target and verify identity independently before any pin update |
| Frontier usage differs from provider records | Cache/call/routing or credential anomaly may exist | Pause Frontier and reconcile local content-free and provider-side ledgers |
| Backup validates but restore rehearsal fails | Archive contract or destination/key state is incompatible | Keep live state unchanged; fix custody/input or select another authenticated archive |
| Update was interrupted | Receipt may be missing while state is already settled | Use `update-recover --preview`; never rerun activation blindly |

## Minimal diagnostic sequence

```bash
./pixel doctor
./pixel limbs
./pixel verify
```

Then inspect only the expected unit and the shortest relevant journal window. Use [monitoring and logs](monitoring-and-logs.md) and keep private output out of issue attachments.

## When to stop troubleshooting

Escalate to incident response when a credential, private content, target identity, external effect, listener, service identity, installed byte, or evidence-integrity boundary may be compromised. Escalate to the [support boundary](../../SUPPORT.md) with sanitized facts when the failure is reproducible but the safe repair is not established.
