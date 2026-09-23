# Upstream candidate failure guide

Treat a failed candidate as evidence about the boundary, not as a reason to reduce the
boundary. Preserve the smallest secret-free transcript, stop disposable state, and leave
the supported deployment unchanged.

## First response

1. Record the exact Pixel commit, candidate/supported manifest hashes, package hashes,
   lane, and failing phase.
2. Confirm the failure occurred only in quarantine, a disposable guest, staging, or the
   canary. If production may be involved, stop and use `INCIDENT-RESPONSE.md`.
3. Remove the disposable instance only after its bounded logs have been copied and
   secret-scanned. Never copy client credentials or raw source-limb data into evidence.
4. Reproduce with the diagnostic subset (`--containers-only` or `--systemd-only`), patch
   the narrow boundary, add a regression, then rerun the complete matrix.

## Failure routing

| Symptom | Likely boundary | Required response |
|---|---|---|
| Integrity, URL, or archive mismatch | Registry intake / quarantine | Reject bytes; do not extract or execute; prepare again from authoritative metadata. |
| Contract blocker | Upstream authority or interface change | Review the grouped hashes; disposition explicitly or keep the candidate Blocked. |
| Config/plugin/tool mismatch | Pixel/OpenClaw integration | Patch generation or adapter code; never enable ambient plugins/tools to make it pass. |
| Forged shared token is accepted | Gateway authority | P0/P1; stop release, preserve evidence, patch and restart the full assurance count. |
| Shared operator token is exposed or accepted beyond the intended loopback boundary | Secret/service isolation | Treat as root-equivalent authority; contain, rotate, audit exposure, and restart assurance. |
| Unshared client pairing or bounded scope fails | Device-state ownership/scope | Qualify that distinct flow without pretending possession of the shared operator secret is unprivileged. |
| Sandbox turn fails | Model/tool/sandbox contract | Inspect the authenticated agent transcript and live container; a direct tool shortcut is not a substitute. |
| systemd-only failure | Service identity/namespace | Inspect effective unit properties, journal, ownership, listener, and process namespace. |
| Capability case fails | Product regression | Release failure even if refusals pass; do not call an inert agent safe. |
| Refusal case succeeds unexpectedly | Policy regression | P1 until proven otherwise; patch externally enforced boundary and add a hostile case. |
| Shutdown/rollback leaves state | Recovery boundary | Block promotion; prove MainPID zero, sandbox retirement, and exact supported runtime health. |
| Evidence identity differs | Provenance | Discard the bundle; rerun against one exact commit/artifact set. |
| Signature fails or signer is untrusted | Promotion authority | Do not promote; recover the trusted signer configuration independently. |

## Recovery decision tree

```text
Candidate failure
+-- Any production/client state touched?
|   +-- yes -> pause affected authority -> incident response -> rotate/revoke if needed
|   `-- no  -> continue in disposable boundary
+-- Integrity/provenance uncertain?
|   +-- yes -> quarantine and reacquire; do not execute
|   `-- no  -> reproduce the narrow failing phase
+-- P0/P1 or widened authority?
|   +-- yes -> Blocked; patch; reset two-pass count
|   `-- no  -> fix or record bounded P2/P3 owner+expiry
+-- Security passes but useful task fails?
|   +-- yes -> Blocked capability regression
|   `-- no  -> complete full matrix, assurance, canary, and rollback
`-- Evidence/signature exact and current?
    +-- no  -> do not promote
    `-- yes -> explicit promotion review
```

Never change a Supported entry to Candidate/Blocked because a test failed. The supported
combination remains the rollback target until a separately qualified candidate is
explicitly promoted.
