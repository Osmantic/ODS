# ODS v3 audit remediation — 2026-09-28

This is the implementation ledger for the eight findings in the audit of
`v3.0.0` (`bec0c42e7c9885a5aecd419a166a6a81e0d37236`). Changes target current
`main`; findings about the historical tag are not automatically findings about
current main. In particular, remote Dashboard session authentication was already
implemented on main before this work.

**Status: work in progress. Do not merge or describe the audit as closed.**

| Finding | Implemented in this branch | Remaining verification/work |
| --- | --- | --- |
| SEC-001: LAN exposure | Private Compose ports and native inference stay loopback-bound; authenticated UI entrypoints retain LAN access; Hermes LAN proxy requires an owner session; new recipes cannot interpolate host binds. | Finish upgrade/cached-stack migration coverage and cross-platform lifecycle checks. |
| SEC-002: Python advisories | Upgrade FastAPI/Starlette and aiohttp; all nine API/relay Docker builds install complete hash-checked locks; production audits are clean. | CI installation/build coverage and triage remaining full-suite failures. |
| SEC-003: source containment | Generated source services have numeric non-root UID, no capabilities, no-new-privileges, read-only root, resource limits and an internal network; API publication/re-enable and dynamic resolver enforce the profile. Saved stack arguments are revalidated by the host and platform CLIs, including old receipts; merged recipes cannot override or join source sandboxes. | Real-container CI passed; run the new cross-platform saved-stack CI and broader lifecycle regressions. |
| SEC-004: public AI spending | Paid issue triage and review comments require a trusted association; serialized jobs and per-run budgets; unauthorized comments cannot cancel another comment's review. | CI workflow validation. This bounds individual runs, not the organization's total monthly provider bill. |
| SEC-005: provenance | Python locks/hashes and pinned multi-platform Python base image indexes. | Release checksums/SBOM/attestations, verified bootstrap channel and remaining image pins. Existing published tags have not been changed or retroactively signed. |
| SEC-006: React Router | Coordinated update to react-router-dom 7.18.4 and its lockfile; production npm audit is clean. | CI across supported frontend hosts. |
| SEC-007: local origin trust | State-changing requests require exact Origin/Host agreement; the CORS allowlist no longer grants mutation authority. | CI regression coverage. |
| SEC-008: mutable Actions | Remaining twelve Action uses pinned to full commit hashes. | CI workflow validation. |

## Validation evidence

- Nine Python production locks: `pip-audit --require-hashes` reports no known
  vulnerabilities for each; the Dashboard lock also installed successfully in
  a clean Linux virtual environment.
- Dashboard frontend: 210 files / 1,773 tests passed; build and lint passed
  (lint retained pre-existing warnings). Production npm audit: zero findings.
  Development-tool advisories are not included in that production-only result.
- API authentication/origin tests: 35 passed.
- Focused recipe/API suite on Linux: 679 passed, two skipped. A subsequent
  policy/source run passed 272, with 134 platform-gated skips. Windows policy
  tests passed 713; one Bash integration case failed because Windows selected
  the WSL launcher in a restricted test environment. That integration case
  passed in the Linux run.
- Native model launch and Lemonade tests on Windows: 142 passed. The LAN bind
  test covers Windows, Linux and Darwin branches using mocked process launch;
  this is not a physical macOS/GPU validation.
- macOS bridge shell contracts passed, including loopback inference and bridge
  restoration with IPv4/IPv6 LAN settings. Network exposure contracts passed
  15 cases; the private-port sweep covers core and community Compose files.
- Source recipe compiler: eight tests passed. Pixel edge: 155 passed; model
  relay: nine passed with the upgraded aiohttp.
- The earlier full Linux API run passed 5,953, skipped 138, failed nine.
  A library-staging race while files were being edited passed on rerun.
  Darwin metrics and WSL-to-Windows PowerShell fixtures still require baseline
  comparison/triage. Do not label the full suite green.
- Runtime security CI run `36463490433` passed on commit `1a9e126fce`:
  all nine locked Python environments installed and passed dependency checks
  and audits, frontend production audit and policy checks passed, and the
  source sandbox passed with real Docker containers and a canary service.
  Docker Desktop remained stopped locally; the live ODS stack was not used.
- Saved-stack policy and model-store integration: 39 tests passed on Linux,
  including actual shell entrypoints and dynamic resolution. Windows PowerShell
  5.1 passed the native saved-stack/model-store integration. Host-agent Compose
  tests: ten passed. Symlink aliases, changed receipts, unsafe overlays and
  cross-extension network attachment are rejected without rewriting approvals.
- The full API CI on the first PR commit passed 6,116 tests and failed two
  assertions still expecting native inference to follow the UI bind address.
  Both expectations now require private loopback and all three related cases
  pass locally. A smoke assertion expecting interpolated OpenClaw ports was
  updated to require literal loopback; its six contracts passed. Await new CI
  before claiming the entire suite passed.

These results refer to the relevant focused changes, not to every combination
of installer, GPU and operating system. The PR remains draft until the remaining
items above and CI are resolved.

## Upgrade behavior to review

LAN clients use authenticated UI/gateway routes. Direct inference and extension
ports no longer inherit the UI LAN preference. Applications requiring remote
access need an explicitly configured authenticated proxy or private tunnel.

Older extension recipes with interpolated host binds or without source
confinement must be reviewed again. Approved recipe bytes are not silently
rewritten. Curated recipes can be refreshed from the updated library; custom
recipes must declare literal loopback binds and the required sandbox. A source
application requiring runtime internet access is not silently granted access
to the shared ODS network.

Most changed Compose files contain the same one-line host-binding correction.
Most added dependency lines are generated wheel hashes, not application logic.
