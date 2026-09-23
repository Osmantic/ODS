# Security assurance process

Pixel uses a repeatable test-fix-test loop. The process tests safety and useful
capability together: a system that refuses everything is not a successful
release.

## Severity and release policy

| Severity | Meaning | Required response |
|---|---|---|
| P0 | Credential or authority escape, cross-client disclosure, uncontrolled destructive action, or sandbox-to-host compromise | Stop, quarantine, rotate affected credentials, preserve evidence, and block release |
| P1 | Unauthorized side effect, identity/approval/lease bypass, unsafe failed recovery, or secret in a release artifact | Contain, patch, add a regression, and block release |
| P2 | Bounded denial of service, incomplete audit trail, materially false authorization/refusal, or important defense-depth failure | Fix before release or record a time-bounded owner exception |
| P3 | Low-impact hardening or evidence-quality issue | Track with an owner and target release |

P0 and P1 findings cannot be waived. A release needs two consecutive clean full
passes after the last security-relevant change.

After any sensitive-data history rewrite, scan every local ref and separately fetch
advertised remote branches, tags, and pull-request heads into an isolated temporary
mirror with `security-evals/assurance/audit_remote_refs.py`. The remote audit has two
deliberately separate results:

- The active Pixel lineage is release-blocking. It scans the tree of the reviewed
  lineage root and every descendant commit on an advertised active Pixel ref. A secret,
  skipped object, changed remote identity, unadvertised candidate commit, or unknown
  non-lineage ref fails closed.
- Historical refs outside that lineage are incident and repository-hygiene evidence.
  They are accepted only when both their ref name and immutable object ID exactly match
  `security-evals/assurance/remote-ref-policy.json`. Their findings remain visible and
  hash-bound in the attestation, but do not describe Pixel code and do not block a Pixel
  release. Any new object at one of those refs, or any new non-lineage ref, blocks.

Changing the lineage root or acknowledged historical-ref inventory is a security-policy
change, must be explicit in review, and still needs the repository's independent PR
approval. Removing a historical ref from the provider is safe; remove its retired policy
entry in a later reviewed cleanup. Re-clone or explicitly resynchronize every deployment
source after a rewrite, and include stashes and non-branch refs in the local scan.

The reviewed historical-blob policy (`security-evals/historical-secret-closure/reviewed-blobs.json`)
is a versioned strict contract (schema v2). Every entry is bound by exact blob SHA-1, raw
payload SHA-256 over the exact git blob bytes, exact path, human review labels/reason, and the
exact sorted `audit_source` scanner labels (empty when the source auditor does not flag the
blob). Its semantic digest is bound into `security-evals/assurance/remote-ref-policy.json`, and
the remote-ref audit and upstream attestation reject any digest, path, payload, label, inventory,
or repository-identity drift. Acknowledged synthetic active-lineage fixtures are reported as
visible, hash-bound evidence and never apply to current source; any unreviewed active finding or
current-tree finding still fails closed. Do not create a second broad allowlist: this policy is
the single source of truth.

Credential containment is not credential invalidation. Removing a bot, account,
or key from every known resource limits access, but the release remains blocked
until the issuer rotates, revokes, or expires the credential and a safe probe
proves the old value is rejected.

Gateway-boundary evidence must include the effective systemd properties, loopback
listener addresses, unauthenticated and invalid-token API results, the exact effective
plugin/tool/agent allowlists, and a sandbox-container inspection. A unit file is not
evidence that the kernel enforced it; test the running process namespace. The live
control-boundary probe must also prove an authenticated safe task succeeds and that the
gateway credential is absent from responses and service logs.
The sandbox inspection must discover the exact agent-scoped container by its reserved
name and OpenClaw labels, bind it to the active image digest and Pixel release, and fail
on duplicates or stopped/stale containers. An image-tag-only query is insufficient.

Runner-boundary evidence must prove that the SSH key lands on the distinct
`pixel-ops-transport` identity with one root-owned forced-command entry, workload
commands are dropped to non-login `pixel-runner`, the workload identity has no sudo
authorization, and transport-routed typed managed validation still succeeds. Test both
the denied direct path and the authorized routed path after every runner refresh.

Frontier live evidence must come from `./pixel frontier-live-qualify` on the exact
deployment under review. Retain its content-free passing receipt plus the exact release,
policy, and authorization hashes in private deployment evidence; do not retain the
credential, prompt, response, account, job, provider, or model identifier. The receipt
must show one observed call, a held one-call ceiling, coherent usage, the intended
ChatGPT-plan/credits or API Platform billing boundary, and API cost within the explicit
worst-case authorization when applicable. A fake adapter, network-disabled failure,
ordinary Frontier job, failed receipt, or inconclusive recovery is not live evidence.

Recovery-boundary evidence must prove that encrypted backups are signed by a trusted
deployment key, checksum- and structure-audited without a plaintext archive, recoverable
with the correct age identity, rejected with a wrong identity, absent/forged/non-canonical
signature, corrupt payload, or path-escaping member, and rehearsable outside live state.
A forced post-restore verification failure must restore the exact pre-transaction state.
The isolated rehearsal must include the mode-`0600` canonical private onboarding input
needed to reproduce that client without relying on an ignored source-worktree file.
The same suite must prove deployment mutations are mutually exclusive and that both a
successful gateway-credential rotation and a forced failed rotation preserve a usable,
single source of truth without emitting either credential.

## Test ethics and production limits

Use fake canary credentials, synthetic messages and events, and disposable
artifacts. Exploit-grade payloads stay on isolated fixtures. Production testing
must not reboot hosts, invoke break-glass paths, send real invitations, alter
unrelated data, exfiltrate a real secret, or create an unbounded network or
compute load. Pause or revoke Operations authority before any test that could
escape a disposable target.

## Evidence contract

Retain these secret-free artifacts outside the source tree on access-controlled
storage:

- exact source provenance manifest;
- attack catalog, generated seeds, versions, and test transcripts;
- clean-room plan, apply, verify, and removal results;
- dependency, source-history, secret, identity, and ACL audit summaries;
- active Pixel branch/tag/pull-ref release audit, separately reported exact legacy-ref
  inventory, and any hosting-provider purge confirmation;
- sanitized live request/decision/runner transcripts and authority events;
- fixture state and digests before and after every side-effecting scenario.

Evidence should be append-only or non-overwritable. Evidence is never a place
to copy tokens, cookies, private message bodies, or other client data.

`QUALIFICATION-MATRIX.json` is the release-wide host and model-capacity contract.
`./pixel promotion-status` validates that matrix against the release manifest and source
capability profiles, then emits only a hash-bound content-free index. A passing index
requires two consecutive exact-commit passes and exact reviewed evidence for every gate;
it does not make the underlying evidence authentic by itself. Reviewers must verify the
owner-controlled records before accepting their SHA-256 values. An automated container
lane never substitutes for either supported host's real systemd, recovery, isolation,
rollback, and removal evidence. See [QUALIFICATION.md](QUALIFICATION.md).

## Test-fix-test cycle

1. Freeze the exact source and environment under test and generate provenance.
2. Run the narrow deterministic suites, then randomized, concurrent, fault, and
   clean-room suites.
3. Reproduce each failure with the smallest safe case and assign severity.
4. Patch the narrowest responsible boundary and add a permanent regression.
5. Rerun the focused case, its boundary suite, and all cross-boundary suites.
6. Rerun the complete release gate twice after the final relevant change.

After the final commit, the reproducible local gate is:

```sh
scripts/run-upstream-assurance.sh \
  --evidence /absolute/private/assurance-evidence \
  --passes 2 \
  --pressure-iterations 2
```

It refuses a dirty or changing source tree, binds every transcript to the exact commit,
tree, release manifest, and attack catalog, audits active Pixel and acknowledged legacy
remote refs as separate results, runs the full
clean-room suite and randomized pressure tests at least twice, checks retained pressure
records, and emits a non-overwriting `assurance-summary.json`. The systemd runtime
matrix, live runner-boundary evidence, canary/rollback evidence, and signed release
attestation remain separate required inputs; this command does not claim they passed.

The release gate covers every case in
`security-evals/assurance/cases.json`, plus the deployment acceptance checklist.
It also measures authorized task completion and unnecessary escalation so that
security controls do not silently destroy Pixel's usefulness.
