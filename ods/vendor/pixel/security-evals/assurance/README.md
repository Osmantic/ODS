# Security assurance evidence

`cases.json` is the release-level attack catalog. It names the invariant for
each security and capability surface; focused harnesses provide the concrete
test vectors and permanent regression cases.

Before a full run, create a source manifest on trusted storage:

```sh
python3 security-evals/assurance/manifest.py \
  /secure/evidence/pixel-source-manifest.json
```

The command refuses a dirty worktree, a relative path, an output inside the
repository, or an existing output. The manifest binds the exact commit, Git
tree, release versions, attack catalog, and every tracked file digest. It does
not contain credentials or environment state.

The manifest is provenance evidence, not proof of security. Pair it with the
test transcripts, dependency and secret scans, clean-room deployment evidence,
authority events, and fixture before/after state required by
`SECURITY-ASSURANCE.md`.

Also audit server-advertised branches, tags, and GitHub pull-request heads in an
automatically removed temporary mirror:

```sh
python3 security-evals/assurance/audit_remote_refs.py --remote origin
```

The schema-v2 report never emits a credential value. `releaseGate` covers the active
Pixel lineage and fails on a finding, skipped object, unadvertised candidate commit,
remote-identity mismatch, changed acknowledged ref, or unknown non-lineage ref.
`legacyHistory` separately reports exact historical refs pinned by name and immutable
object ID in `remote-ref-policy.json`; findings there are repository-hygiene or credential
incident evidence, not Pixel release failures. Both sections and the policy digest are
retained in the signed assurance evidence. A clean local clone is not sufficient because
hosting-provider refs can retain objects that no writable branch or tag reaches.

Do not casually edit `remote-ref-policy.json`. A lineage-root or legacy-ref change is a
security-policy migration and requires explicit independent PR review. Exact ref pins
make unexpected legacy-ref movement fail closed.

## Reviewed historical-blob policy (strict v2 contract)

`security-evals/historical-secret-closure/reviewed-blobs.json` is the single source of truth
for acknowledged credential-shaped historical blobs (schema v2, loader in
`security-evals/assurance/reviewed_policy.py`). Each entry is bound by exact blob SHA-1, raw
payload SHA-256 over the exact git blob bytes, exact path, human review labels/reason, and the
exact sorted `audit_source` scanner labels (empty when the source auditor does not flag the
blob). Its semantic digest is bound into `remote-ref-policy.json`; the remote-ref audit and
upstream attestation reject digest, path, payload, label, inventory, or repository-identity
drift. Acknowledged synthetic active-lineage fixtures are reported as visible, hash-bound
evidence and never apply to current source. `scan-history-secrets.py` validates the strict
contract and fails on stale, drifted, or unreviewed findings.
