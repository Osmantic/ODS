# OpenClaw candidate release checklist

Copy this checklist into the candidate PR. Every identifier must refer to the same source
commit and candidate manifest.

## Intake and contract

- [ ] `upstream check` observation retained; checkout unchanged.
- [ ] Stable channel selected; exact registry metadata and publication times retained.
- [ ] All package integrity and SHA-256 values verified in private quarantine.
- [ ] Candidate manifest and matching Candidate compatibility record are the only
      preparation edits, and the intake record names its source commit.
- [ ] Contract diff reviewed; every grouped blocker has a recorded disposition.

## Runtime and capability

- [ ] Ubuntu 24.04 quick lane passed.
- [ ] Debian 12 quick lane passed.
- [ ] Ubuntu 24.04 systemd VM lane passed.
- [ ] Real gateway forged-token denial, loopback shared-operator authentication,
      plugin/tool schema, session tree, enabled limb, disabled limb, agent tool turn,
      live sandbox, shutdown, and rollback passed. Device pairing/scope is separately
      qualified for clients that do not possess the root-equivalent shared secret.
- [ ] Every runtime record carries the same evidence-binding hash.

## Full assurance

- [ ] Source provenance and active Pixel remote refs audited with no findings, skipped
      objects, policy drift, remote mismatch, or unadvertised candidate identity.
- [ ] Exact legacy-ref report reviewed separately; any credential incident or provider
      cleanup is tracked without being misclassified as a Pixel runtime/release failure.
- [ ] Full deterministic/clean-room suite passed twice after the final relevant change.
- [ ] Source/Operations fuzz and concurrency races passed twice with retained seeds.
- [ ] Hostile email, Calendar, web, session, and runner-output cases passed.
- [ ] Modular email, Calendar, web, workspace, subagent, and Operations capability passed.
- [ ] Representative live runner boundary passed without production authority expansion.
- [ ] Backup, wrong-key/signature/corruption rejection, isolated restore, forced-failure
      restore rollback, credential rotation rollback, and release rollback passed.

## Staging, canary, and promotion

- [ ] Clean install/removal and forced apply failure were proven in disposable state.
- [ ] Tower2 staging uses distinct state, ports, credentials, spools, and projections.
- [ ] Synthetic source/web/session/Operations checks passed.
- [ ] Canary scope and observation window were approved and completed.
- [ ] Live rollback rehearsal restored the exact prior supported runtime.
- [ ] Qualification bundle contains no secret and includes all required hashes/results.
- [ ] Signed evidence index covers source audit, split active/legacy remote-ref audit,
      policy digest, and retained-evidence secret scan.
- [ ] Detached signature verifies against the trusted release signer and namespace.
- [ ] Explicit promotion approval names the exact attestation digest.
- [ ] Immediate post-promotion verification passed; prior version remains retained.

## Exceptions

- P0/P1 findings: none (required).
- P2/P3 owner/expiry records:
- Residual operational notes:
