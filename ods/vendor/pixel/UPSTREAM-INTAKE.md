# Deterministic OpenClaw upstream intake

Pixel follows the authoritative npm registry `extended-stable` and `latest` tags. It
does not treat beta or alpha releases as candidates, and it resolves OpenClaw and each
official plugin independently because their stable versions do not always match.

## Read-only discovery

```bash
./pixel upstream check
```

The command reads full registry metadata directly over HTTPS, reports the resolved
versions and publication timestamps for both tracked channels, compares them with the
release manifest, and exits without writing repository, cache, or production state.

## Prepare an exact candidate

Start from a clean candidate branch and choose one channel explicitly:

```bash
./pixel upstream prepare --channel extended-stable
# or
./pixel upstream prepare --channel latest
```

Preparation downloads opaque npm tarballs without extracting them, installing them, or
running lifecycle scripts. Registry URLs are restricted to the authoritative npm
origin. Every archive must match registry SHA-512 integrity; Pixel then records its
SHA-256, size, exact publication time, and URL. Files and the deterministic report are
stored mode `0600` below a mode-`0700` quarantine outside the source tree. Use
`--quarantine /absolute/private/path` to select another private location.

Only `RELEASE-MANIFEST.json` and `OPENCLAW-COMPATIBILITY.json` are updated. The first
file pins the exact candidate artifacts and the second records that exact combination as
Candidate. Runtime files, the installed OpenClaw release, services, credentials, and
brokers are untouched. Repeating the same preparation re-verifies and reuses exact
quarantined bytes, refuses a conflicting report or modified preparation file, and
produces the same candidate manifest, compatibility record, and hashes.

The report names the proposed candidate branch/PR and the next qualification commands.
Do not open or promote the candidate from the intake result alone: generate the derived
files, review the upstream contract diff, run real-runtime qualification, and record a
compatible or blocked state first.

## Review the upstream contract

Use the same quarantine base selected during preparation:

```bash
./pixel upstream diff
# or: ./pixel upstream diff --quarantine /absolute/private/path
```

The diff retrieves the exact supported manifest from the preparation source commit,
verifies both supported and candidate archives, and safely extracts them without
executing package code. It compares package/CLI/config/gateway/plugin/session/sandbox/
state contracts as file hashes, package metadata, flags, environment names, tool names,
and hashed security signals. Source lines are not copied into evidence.

Machine-readable `contract-diff.json` and a maintainer Markdown report remain in the
private analysis directory. New authority, a removed denial, credential-path changes,
and network/filesystem surface changes are marked blocking. Static signals are
deliberately conservative; reviewers must disposition each blocker rather than deleting
or bypassing it. The command never edits the source tree or compatibility state.

For a candidate with conservative findings, copy
`OPENCLAW-UPSTREAM-REVIEW.example.json` to `OPENCLAW-UPSTREAM-REVIEW.json` on the
candidate branch. Bind it to the exact compact candidate-manifest hash and intake source
commit, review every nonblocking contract change, and provide exactly one decision for
every reported blocker ID. A `compatible` decision is allowed only when the reviewer
concludes the change is not a security finding (`risk: "none"`) and records a substantive
rationale. The review mechanism cannot waive P0/P1/P2/P3 risk, cannot omit or invent an
ID, and cannot apply to another candidate. Rerunning `upstream diff` embeds the review
digest and decisions; only a fully covered compatible review changes the report to
`compatible`. PR approval and the final release signature remain separate controls.

## Exercise the real runtime

Runtime qualification is deliberately performed only in disposable Linux state. The
qualification runtime is itself an exact Node archive pinned by URL and SHA-256 in the
release manifest. After `prepare` and `diff`, an appropriately isolated guest can run:

```bash
./pixel upstream qualify --mode quick --quarantine /absolute/private/quarantine
```

The probe verifies every archive again, installs the supported and candidate OpenClaw
packages plus official plugins, and loads the real Pixel plugin. Every lane proves clean
config validation, exact versions, plugin and tool contracts, authenticated gateway
behavior, tree-restricted sessions, enabled-limb capability, disabled-limb refusal,
clean shutdown, and rollback to the exact supported runtime. The systemd VM lane also
performs a harmless sandboxed workspace write and inspects live container confinement;
nested container lanes defer that kernel boundary test to the VM. Core runtime
installation uses `npm --ignore-scripts`; everything executes only inside the
disposable boundary.

The complete operator gate runs Ubuntu 24.04 and Debian 12 containers plus an Ubuntu
24.04 systemd-capable VM through Incus:

```bash
scripts/run-upstream-runtime-matrix.sh \
  --quarantine /absolute/private/quarantine \
  --evidence /absolute/private/evidence
```

All three Incus guests are immutable fingerprints in `RELEASE-MANIFEST.json`, including
the expected guest type, OS release, and architecture. The runner uses a matching local
image or fetches that exact fingerprint with a ten-minute bound; it never launches a
mutable distribution alias. Refreshing these guest pins is a separately reviewed Pixel
release change and cannot ride along with an OpenClaw package intake.

The VM lane runs the gateway as UID 1000 behind a hardened system unit and repeats the
candidate-to-supported rollback. Its live capability check is a complete agent turn,
not a direct tool-endpoint shortcut: a fresh private client with a forged shared secret
must be denied, then the exact shared loopback operator secret must complete one workspace
write through a deterministic model. OpenClaw intentionally permits an operator holding
that shared secret to connect before device identity exists, so this secret is treated as
root-equivalent authority and is protected by the loopback bind, private state, and service
boundary. The tool result must return to the model, after which the harness inspects the
running Docker sandbox. All test and gateway secrets are redacted, and the evidence tree
is scanned before it can pass. Device pairing remains a separately qualified boundary for
clients that do not possess the shared operator secret.

The service account owns only mutable state. Candidate and supported code trees remain
root-owned and read/execute-only, which preserves `ProtectSystem=strict` while still
allowing the unprivileged service to traverse the pinned npm runtime. Generated instance
names are collision-checked and removed when the run ends. `--containers-only` and
`--systemd-only` are diagnostic subsets; neither substitutes for the complete three-lane
release gate. No production port, state, service, account, credential, or container is
used.

## CI lanes

`Pixel deployment checks` is the required fast regression lane on every pull request.
It runs the static/security suite, clean-room deployment lifecycle, dependency checks,
and checksummed handoff packaging.

`OpenClaw compatibility` always emits the stable `OpenClaw candidate compatibility
gate` result. Expensive package work runs only when the committed manifest contains a
valid `upstreamIntake` record. Such a candidate is re-diffed and exercised against both
the supported and candidate runtimes on Ubuntu 24.04 and Debian 12. A clean committed
candidate checkout and the original manifest-plus-compatibility preparation state are
both accepted; unrelated working-tree changes are not. Any undispositioned contract
blocker or failed runtime lane fails the gate.

The same workflow has an explicit `run_systemd_vm` dispatch option for a private Linux
runner labeled `pixel-systemd-vm` with Incus. That manual job runs the complete
three-environment matrix. It is a release gate, not a substitute for the two hosted
quick lanes, and it is never scheduled onto an untrusted pull-request runner.

`OpenClaw release discovery` runs daily and on manual request with contents-read
permission. It calls only `./pixel upstream check`, proves the checkout was not mutated,
and uploads its JSON observation. It cannot prepare, promote, deploy, push, or open a
pull request.

## Canary and signed attestation

After the complete matrix and two final assurance passes, run the isolated systemd
canary. The command refuses an observation shorter than 30 minutes:

```bash
scripts/run-upstream-canary.sh \
  --quarantine /absolute/private/quarantine \
  --evidence /absolute/private/canary-evidence
```

The candidate stays inside a disposable Ubuntu VM with distinct state, port, generated
credentials, workspace, and projections. It repeats the real paired-agent sandbox turn,
polls service and gateway health throughout the window, retires the candidate sandbox,
and proves a healthy rollback to Supported. It never reuses production state.

Place the source manifest, contract diff, full runtime matrix, two-pass assurance
summary, assurance source/history/ref audits, assurance secret-scan log, and canary
summary in one private evidence directory. The remote-ref record must show a clean
active Pixel release gate; exact acknowledged legacy findings remain separately visible
and hash-bound but do not block candidate promotion. The builder checks and signs all
eight records; the verifier rehashes each one. Build the detached signed decision with a
dedicated SSH release-signing key:

```bash
python3 scripts/upstream-attestation.py build \
  --source-manifest /evidence/source-manifest.json \
  --contract-diff /evidence/contract-diff.json \
  --runtime-matrix /evidence/runtime-matrix.json \
  --assurance-summary /evidence/assurance-summary.json \
  --canary-summary /evidence/canary-summary.json \
  --output /evidence/upstream-attestation.json \
  --signing-key /secure/release-signing-key
```

The builder refuses a dirty checkout, mismatched commit/manifest/package identity,
contract blocker, missing OS lane, fewer than two passes, short/non-isolated canary, or
unhealthy rollback. Verify independently before promotion:

```bash
python3 scripts/upstream-attestation.py verify \
  --attestation /evidence/upstream-attestation.json \
  --signature /evidence/upstream-attestation.json.sig \
  --allowed-signers /secure/release-allowed-signers \
  --identity osmantic-pixel-release \
  --evidence-dir /evidence
```

The signer file, private key, and evidence directory are operator inputs and are never
committed. A branch name, tag, CI badge, or handwritten checklist is not a substitute
for the verified signature and evidence hashes.

Only after independent verification, promote the exact Candidate combination with an
explicit permanent HTTPS evidence reference:

```bash
./pixel upstream promote \
  --attestation /evidence/upstream-attestation.json \
  --signature /evidence/upstream-attestation.json.sig \
  --allowed-signers /secure/release-allowed-signers \
  --identity osmantic-pixel-release \
  --evidence-dir /evidence \
  --evidence-reference https://github.com/Osmantic/Pixel/actions/runs/RUN_ID \
  --confirm
```

Promotion requires a clean checkout at the signed commit, an exact Candidate row, and
exactly one current Supported row. It verifies the signature and every evidence hash,
retires the old Supported combination, promotes the candidate, removes the temporary
intake record, regenerates derived release files, and reruns the release contract. A
failed transaction restores the original manifest/matrix and regenerated outputs.

## Failure rules

Preparation fails closed for a dirty source tree, non-stable tag, missing publication
metadata, missing or malformed integrity, redirect, non-registry tarball, oversized
metadata/archive, symlinked quarantine object, integrity mismatch, non-private
quarantine, conflicting rerun, or source-tree quarantine path.

Contract analysis additionally rejects archive traversal, links, devices, duplicate or
oversized members, extraction tampering, a non-ancestor intake commit, a pre-intake
release absent from Supported compatibility state, and any archive hash mismatch.

Runtime qualification additionally fails for a Node, package, or plugin identity
mismatch; config warnings; missing or quarantined Pixel tools; bypassed authentication;
disabled-limb availability; lost tree/workspace policy; sandbox privilege, mount, root,
or network expansion; unclean shutdown; rollback failure; or a gateway credential in
retained evidence.
