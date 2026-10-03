# OpenClaw-to-Pixel qualification plan

## Objective

Turn a newly published OpenClaw release into a reproducible Pixel candidate, prove its
security and useful capability against the real upstream runtime, promote it through a
rehearsed canary, and retain signed evidence. Detecting `latest` may be automatic;
production promotion is always explicit.

## Release states and policy

- **Supported**: the exact Pixel/OpenClaw/plugin/environment combination approved for
  production and rollback.
- **Candidate**: a registry release undergoing compatibility and assurance testing.
- **Blocked**: a candidate with an unresolved contract change or failed gate.
- **Retired**: a formerly supported combination retained only for bounded rollback.

Track OpenClaw `extended-stable` and `latest` independently. Beta or alpha releases may
run in disposable research jobs but cannot become production candidates. P0/P1 findings
cannot be waived. A security-relevant fix resets the candidate to the start of the two
consecutive full-pass requirement.

## Work package 1: one release source of truth

Make `RELEASE-MANIFEST.json` the only authored location for OpenClaw, official plugin,
installer, image, and runtime pins. Generate `.env.example`, bootstrap defaults, and
configuration constants from the manifest. Extend the release-contract check to reject
duplicated authored versions, missing SHA-256/npm integrity, mutable unpinned artifacts,
and a Pixel/OpenClaw combination absent from the compatibility matrix.

Deliverables:

- a versioned manifest schema;
- `OPENCLAW-COMPATIBILITY.json` plus a readable generated table;
- generated runtime constants;
- migrations for the existing manifest;
- regressions proving cross-file version drift is impossible.

## Work package 2: deterministic upstream intake

Add these operator commands:

```text
./pixel upstream check
./pixel upstream prepare --channel latest
./pixel upstream diff
./pixel upstream qualify
./pixel upstream promote --confirm
```

`check` reads authoritative registry metadata without changing state. `prepare` downloads
the exact OpenClaw and official plugin tarballs into a private quarantine, verifies npm
integrity, calculates SHA-256, records publication metadata, atomically updates the
manifest and matching Candidate compatibility record, and emits a candidate branch/PR
report. It must not execute package lifecycle scripts or touch production. Re-running
it with the same inputs must produce the same manifest, compatibility record, and
artifact hashes.

## Work package 3: upstream contract diff

Compare the supported and candidate packages and classify changes to:

- CLI commands, flags, exit codes, and machine-readable output;
- configuration schema, defaults, and rejected keys;
- gateway authentication, bind behavior, and service environment;
- plugin manifests, registration API, and tool schemas;
- session visibility and parent/child coordination;
- sandbox image, labels, identity, mounts, networking, and resource limits;
- state paths, migration behavior, and recovery assumptions.

New authority, a removed denial, a changed credential path, or a widened network/filesystem
surface is blocking until reviewed. Store a normalized machine-readable diff and a concise
maintainer report in the evidence bundle.

## Work package 4: real-runtime compatibility harness

Keep fixture tests for speed and determinism, but add an isolated harness that installs
the exact candidate OpenClaw tarball and official plugins. It must boot the real gateway
and prove version, config validation, plugin loading, tool schemas, authentication,
session-tree behavior, sandbox confinement, a harmless workspace tool turn, enabled limb
capability, disabled limb refusal, clean shutdown, and rollback to the supported runtime.

Run the harness on Ubuntu 24.04 and Debian 12. It may use containers for fast checks, but
the final release gate must also use a systemd-capable disposable VM because service and
namespace enforcement are part of the security boundary.

## Work package 5: CI and discovery

Add three lanes:

1. **Regression**: fixture-based Pixel tests on every PR.
2. **Compatibility**: supported and candidate real OpenClaw runtimes on both supported
   operating systems.
3. **Discovery**: a scheduled read-only job that reports new `extended-stable`/`latest`
   versions and may open a candidate PR, but never promotes or deploys.

Required candidate checks include shell/static checks, release contract, clean-room
plan/apply/verify/rollback, real gateway, plugin/tool contract, source and dependency
audit, and checksummed handoff packaging.

## Work package 6: full Pixel assurance

Bind the candidate identity and hashes into every evidence record, then run:

- deterministic unit and clean-room suites;
- source and Operations fuzzing;
- authority concurrency and budget races;
- hostile email/Calendar/web/runner-output cases;
- gateway, sandbox, and session isolation;
- modular email, Calendar, web, local workspace, subagent, and Operations tasks;
- backup validation, isolated rehearsal, forced-failure restore, and release rollback;
- live runner-boundary tests on representative hardware;
- two consecutive complete passes after the final relevant change.

Capability regressions are release failures: a candidate that is safe only because useful
tasks stopped working does not pass.

## Work package 7: staging, canary, and promotion

Promotion order:

1. disposable clean installation and removal;
2. deliberate apply/verify failure with automatic rollback;
3. isolated Tower2 staging state, port, and credentials;
4. synthetic source, web, session, and Operations checks;
5. limited canary deployment with a documented observation window;
6. backup validation and live rollback rehearsal;
7. explicit signed promotion to Supported;
8. immediate post-promotion verification and prior-version rollback retention.

Canary credentials, spools, sessions, and projections must be isolated from production.
Promotion fails closed if evidence is missing, stale, unsigned, or refers to another
source commit or artifact hash.

## Work package 8: provenance and attestations

Produce one secret-free qualification bundle containing:

- Pixel commit, dirty-tree state, and source manifest;
- OpenClaw/plugin package URLs, versions, integrity, and SHA-256;
- operating-system, Node, container, and hardware identities;
- compatibility diff and reviewer decisions;
- SBOM, dependency, source/history, and secret-audit summaries;
- deterministic, pressure, live, canary, and rollback results;
- known P2/P3 exceptions with owner and expiry;
- detached signature and final promotion decision.

Release packaging and GitHub promotion must verify this attestation rather than trusting a
branch name, mutable tag, or manually written checklist.

## Work package 9: documentation and handoff

Add a maintainer intake runbook, candidate failure guide, client upgrade/rollback guide,
PR template, release template, compatibility table, evidence-verification instructions,
and recovery decision tree. A clean-room handoff test must have a second engineer qualify
or safely reject a candidate using only the repository documentation.

## Delivery sequence

1. **PR A - manifest and intake**: schema, generation, discovery, quarantine, and hashes.
2. **PR B - compatibility harness**: package diff and real OpenClaw tests.
3. **PR C - CI and promotion**: OS matrix, staging, canary, attestation, and gates.
4. **PR D - proof and handoff**: documentation plus the first real `latest` candidate.

Each PR remains independently testable. Do not combine the first production upstream
upgrade with unfinished promotion automation.

## Definition of done

- A clean clone prepares a candidate with one command and no manual multi-file edits.
- The real candidate OpenClaw runtime passes on Ubuntu and Debian.
- Every upstream authority/config/tool/sandbox change is identified and reviewed.
- All Pixel safety, capability, pressure, clean-room, recovery, and hardware gates pass
  twice after the final fix.
- Canary activation and rollback are proven against the exact candidate artifacts.
- A signed evidence bundle is independently verifiable.
- Another engineer can qualify or reject `latest` without Osmantic tribal knowledge.
