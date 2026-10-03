# Contributing to Pixel

Pixel is a security-sensitive deployment kit. A change is complete only when its data,
authority, network, filesystem, recovery, and upgrade effects are explicit and tested.

## Before opening a change

- Work from a current branch based on `main`; do not commit private onboarding, runtime
  state, credentials, messages, memory, host identities, or live evidence.
- Use the smallest capability surface that solves the problem. New source access belongs
  behind a projection broker, writes belong behind a separate actuator, and machine work
  belongs behind a named Operations action or an exact break-glass proposal.
- Do not add a generic Frontier prompt, file, repository, URL, message, or log forwarder.
- Treat dependency, runtime, image, and plugin pin changes as release-contract changes.

## Required checks

On Ubuntu 24.04 LTS or Debian 12, run:

```bash
./pixel test
./pixel package
```

Run the focused pressure, race, live, isolation, or recovery harnesses for every boundary
the change affects. A bug fix must add a regression that fails before the fix. Security
changes require two consecutive clean full passes after the final relevant edit.

Use the pull-request template to record exact release identity, evidence, findings, and
exceptions. P0/P1 findings cannot be waived. P2/P3 exceptions require an owner, expiry,
and compensating control. Never paste private evidence into a pull request.

## Generated and private files

Edit release pins only in `RELEASE-MANIFEST.json` and compatibility state only in
`OPENCLAW-COMPATIBILITY.json`, then run:

```bash
node scripts/generate-release-files.mjs --write
node scripts/generate-release-files.mjs --check
```

Keep onboarding answers, `.env`, `.generated`, `.runtime`, `.secrets`, OpenClaw state,
broker state, approval material, and live audit transcripts outside Git. Use synthetic
fixtures for tests and sanitized, identity-bound summaries for release evidence.

## Reviews and releases

All changes use pull requests and must pass required CI. Authority, isolation, credential,
release, and recovery changes require maintainer review. Releases follow
`SECURITY-ASSURANCE.md` and `UPSTREAM-RELEASE-CHECKLIST.md`; a green unit suite alone is
not release evidence.
