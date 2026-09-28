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
| SEC-005: provenance | Python locks/hashes, Python base image indexes and 31 additional external image references resolved from registry descriptors. Compose, installer prefetch/defaults and the dependency inventory agree on those digests. | Release checksums/SBOM/attestations, verified bootstrap channel and remaining image pins. Existing published tags have not been changed or retroactively signed. |
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
- Image pin update: dependency inventory check and ten dependency contracts
  passed; Whisper CPU/CUDA selection passed 13 cases and Dashboard ownership
  contracts passed. Pins use each tag's top-level descriptor, preserving the
  platform set instead of selecting only an amd64 child manifest.
- Registry verification is not finished. Docker Hub returned anonymous rate
  limits for some remaining images. InvokeAI's configured `v6.11.1` image does
  not exist; upstream publishes separate `v6.11.1-cpu` and `v6.11.1-cuda`
  images, so the AMD/NVIDIA recipe needs explicit review before replacing it.
  Dockerfile frontend directives and disabled fragments also need final review.

These results refer to the relevant focused changes, not to every combination
of installer, GPU and operating system. The PR remains draft until the remaining
items above and CI are resolved.

### Regression review following installer concerns

The `install-macos.sh` loopback change is intentional, not a claim of unchanged
network behavior: native OpenCode connects to the host's published LiteLLM port
or native llama port. Both listeners remain private even when the UI is exposed
to the LAN. The Colima bridge is a separate container-to-host route.

An additional 30 cases execute the actual installer route-selection block and
config writer for switchboard, cloud and native modes, five IPv4/IPv6 UI bind
settings, and default/custom ports. Custom-port cases make an actual loopback
HTTP request using the generated URL, key and model; upgrade fixtures verify
that unrelated user settings survive. These are fixture endpoints, not model
inference, launchd or a full macOS installation. The test also runs on macOS CI.

The latest completed checks on the preceding commit exposed a PowerShell 5.1
UTF-8 BOM handling error, a Linux-only shell fixture running under macOS Bash
3.2, and an outdated SearXNG locale image-reference comment. The remediation
accepts one optional UTF-8 BOM while rejecting invalid/oversized input, selects
Bash 4+ only for the Linux fixture, and aligns the comment with the pinned image.
Native macOS shell cases continue to run with the system Bash. Final-head CI
must confirm these changes before any merge recommendation.

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
