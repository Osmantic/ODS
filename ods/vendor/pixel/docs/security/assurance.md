---
title: Security assurance and evidence
doc_type: how-to
audience: [security-reviewer, maintainer, contributor]
feature_status: supported
owners: [security, release]
sources_of_truth: [SECURITY-ASSURANCE.md, SECURITY.md, INCIDENT-RESPONSE.md]
last_verified_at: 2026-08-27
---

# Security assurance and evidence

Security assurance is a repeated test-fix-test discipline. A green authored test is evidence for that tested contract; it is not proof of correct installation, a real provider call, recovery, publication, activation, or universal security.

## Severity and disposition

| Severity | Meaning | Required disposition |
|---|---|---|
| P0 | Credential or authority escape, cross-client disclosure, sandbox-to-host compromise | Stop, quarantine, rotate, repair, and block release; never waive |
| P1 | Unauthorized side effect, identity bypass, unsafe recovery | Contain, repair, repeat affected and full gates, block release; never waive |
| P2 | Bounded denial of service or incomplete auditability | Repair or record the time-bounded exception allowed by the authoritative policy |
| P3 | Low-impact hardening gap | Track with owner and evidence |

After the last security-relevant change, the authoritative assurance process requires consecutive clean full passes. A new relevant change resets that run. Use the commands and pass count in `SECURITY-ASSURANCE.md`; do not copy an old transcript as current evidence.

## Evidence layers

1. Static checks inspect source shape, generated drift, secrets, dependencies, and policy invariants.
2. Unit and adversarial tests exercise parsers, schemas, plan binding, denial paths, and fail-closed behavior.
3. Clean-room lifecycle tests exercise installation or qualification in disposable roots.
4. Real system-service lanes inspect effective users, groups, hardening, listeners, filesystem modes, restart, crash, and recovery behavior.
5. Real-backend lanes prove only the exact provider/tool route and evidence envelope observed.
6. Signed update, rollback, and reactivation prove the exact candidate lifecycle that was exercised.

Container automation does not substitute for the system-service lane. A fake adapter, model-off result, unreachable-network failure, or synthetic fixture does not substitute for a real-backend turn.

## Evidence handling

Retain evidence outside the source tree and keep it content-free. Bind it to exact source provenance and the relevant artifact hashes. Record the command, lane, outcome, failure classification, and fresh timestamp without copying credentials, prompts, source content, host identity, signer identity, or private paths.

Use fake canaries, synthetic messages, and disposable artifacts. Do not test with a real credential, a real exfiltration endpoint, an uncontrolled reboot, or exploit-grade payload against a live client system.

## When a gate fails

1. Stop promotion for P0/P1 and contain any potentially affected boundary.
2. Preserve secret-free evidence and identify the earliest violated invariant.
3. Repair the implementation or the test only after deciding which contract is authoritative.
4. Re-run the focused adversarial test, its neighboring boundary tests, and then the full required assurance sequence.
5. Start the required consecutive-pass count again after the last relevant change.

For host and product qualification lanes, see [evaluations](evaluations.md). For operational response, use [incident response](../../INCIDENT-RESPONSE.md).
