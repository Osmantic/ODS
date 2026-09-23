---
title: Monitor Pixel and inspect logs
doc_type: how-to
audience: [owner, operator]
feature_status: mixed
owners: [documentation, operations]
sources_of_truth: [OPERATIONS.md, CONTROL-SURFACE.md, scripts/reference-services.sh, scripts/verify.sh]
last_verified_at: 2026-08-27
---

# Monitor Pixel and inspect logs

Prefer content-free status for routine orientation, then inspect private logs from the trusted terminal only when a specific failure requires it.

## Owner-visible status

```bash
./pixel ui
./pixel verify
./pixel limbs
```

The local page can show bounded readiness, pending counts, recent fixed-action outcomes, budget totals, and recovery/update orientation. It does not expose private command output, verify backup artifacts, validate update signatures, or authorize activation/resume/recovery.

## Service status

```bash
systemctl status openclaw-gateway.service
systemctl status pixel-web-courier.service
systemctl status pixel-source-broker.timer
systemctl status pixel-source-broker.service
systemctl status pixel-ops-broker.service
systemctl status pixel-frontier-broker.service
```

Check only units expected for the selected profile and limbs. An absent disabled broker is safer than an unexpectedly running one.

For reference services:

```bash
./pixel services status
./pixel services logs
```

## Focused journal windows

```bash
journalctl -u openclaw-gateway.service --since today
journalctl -u pixel-web-courier.service --since today
journalctl -u pixel-source-broker.service --since today
journalctl -u pixel-ops-broker.service --since today
journalctl -u pixel-frontier-broker.service --since today
```

Logs are private even when the browser projection is content-free. They may contain host details, object identifiers, errors, or operator context. Do not attach raw journals to public issues.

## What to watch

- failed or repeated service starts;
- gateway listener or authentication failures;
- stale projections or timers;
- an external action left in processing/uncertain state;
- Operations lease, budget, pause, transport, or target-identity failures;
- Frontier provider/local-ledger mismatch or quality-circuit events;
- disk growth in private ledgers, results, receipts, staging, backups, and journals;
- runtime attestation becoming unavailable after source/configuration/service drift.

## Alert response

Refresh current state, preserve the smallest private evidence needed, and follow [verify and diagnose](verify-and-diagnose.md). For authority, privacy, credential, or uncertain external-effect alerts, follow [incident response](../../INCIDENT-RESPONSE.md) before any retry or resume.
