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
| SEC-002: Python advisories | Upgrade FastAPI/Starlette and aiohttp; all nine API/relay Docker builds install complete hash-checked locks; production audits are clean. All nine production Dockerfile builds and in-container pip checks passed CI on `1788236a9`. | Keep final-head dependency and regression checks green. |
| SEC-003: source containment | Generated source services have numeric non-root UID, no capabilities, no-new-privileges, read-only root, resource limits and an internal network; API publication/re-enable and dynamic resolver enforce the profile. Saved stack arguments are revalidated by the host and platform CLIs, including old receipts; merged recipes cannot override or join source sandboxes. | Real-container CI passed; run the new cross-platform saved-stack CI and broader lifecycle regressions. |
| SEC-004: public AI spending | Paid issue triage and review comments require a trusted association; serialized jobs and per-run budgets; unauthorized comments cannot cancel another comment's review. | CI workflow validation. This bounds individual runs, not the organization's total monthly provider bill. |
| SEC-005: provenance | Python locks/hashes and pinned core/library images; local library images require a forced in-recipe build. Signed-tag source packaging, draft-only checksum/SBOM/OIDC workflow, and Windows/POSIX verified consumers are implemented. README separates verified stable from development opt-in. | Verify the first signed immutable candidate end to end and confirm the new library-policy CI. The current public release lacks the artifacts/immutability flag and is correctly refused: do not switch public onboarding until the producer is released and qualified. Existing tags have not been changed or retroactively signed. |
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
- Earlier registry checks encountered Docker Hub rate limits and an unavailable
  generic InvokeAI tag. The follow-up resolved the frontend/base images and
  verified all three upstream InvokeAI variants: CPU, CUDA and ROCm. The
  earlier inventory did not include the ROCm tag; it exists and is now pinned.

- A later registry pass resolved 18 more tag descriptors, including references
  duplicated in disabled Langfuse fragments. Those defaults now carry their
  top-level digest, including Node, nginx, CUDA/ROCm, Lemonade, Qdrant, Tailscale,
  Aider and the Dockerfile frontend. Installer prefetch and backend metadata use
  the same references. This preserves each image's existing platform set; it
  does not assert GPU compatibility beyond the upstream image.
- The core dependency checker now rejects any external image without a full
  sha256 digest, including malformed hashes and allowlisted mutable tags.
  Twelve dependency contracts passed. Linux AMD contracts passed; the Windows
  run exposed two stale mocks (JSON is now UTF-8 bytes and launch prefers
  installed PowerShell 7), which were corrected to match existing production
  behavior. All 65 AMD contracts then passed in native Windows/Git Bash.
- Runtime security run `36471971155` on `d7aea8ae9` passed all nine locked
  environments/audits, real-container confinement and saved-stack/verified
  bootstrap tests on Windows, Linux and macOS. Full API and frontend Windows/
  macOS jobs passed too. The Ubuntu frontend failed an unsaved-name test that
  passed locally. Subsequent commit `1788236a9` passed every executed PR check,
  including all three frontend hosts and the full API suite. Runtime security
  run `36473239829` also passed the nine actual production Dockerfile builds
  and in-container dependency checks. This supersedes the earlier pending CI.

The image reader now handles Dockerfile heredocs, logical continuations,
global build arguments, local stages and frontend syntax directives. Python
`from` statements inside the AudioCraft heredoc are no longer mistaken for
registry images. Seventeen dependency contracts pass, including malformed input
rejection and the library boundary. The current library inventory contains 357
references: 253 external references with complete digests and 104 local-build
references across 102 recipes. Local tags require `pull_policy: build` and a
Dockerfile confined to the recipe; a matching `ods/` name alone is insufficient.
This prevents normal Compose startup from trusting a pre-existing/pulled tag
instead of rebuilding the reviewed source. Build cache remains available.

InvokeAI's CPU/CUDA/ROCm indexes were verified anonymously with `docker buildx
imagetools inspect`. Each currently publishes Linux amd64, not native Metal or
arm64. Actual Compose rendering selects the expected backend, private port and
`/api/v1/app/version` readiness URL. The AMD entrypoint receives the render GID.
These checks do not claim physical GPU execution or image-generation success.

The Dify and Jan placeholders were already excluded from the deployable catalog
and refused by the install API. Their unverified disabled Compose templates are
removed; reference documentation explains the unsupported integration and links
to upstream. No installed data is deleted and the generated catalog still has
200 entries. Dify is not being claimed as a newly working integration.

Locally, 338 library staging/resolver cases, 17 dependency contracts, three
actual Compose backend renders and two retired-entry rejection tests passed.
A new disposable Docker CI test seeds an unreviewed local tag and verifies the
shipped pull policy rebuilds the reviewed source. That new test remains pending.
Runtime security run `36476143179` on `4471122f9` passed all jobs, including the
UTF-8 recovery correction; this precedes the library update.

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

The subsequent Windows CI confirmed both native checks passed, but propagated
the deliberately failing helper's exit code from the rejection test. The test
now exits successfully only after all assertions and cleanup complete. The
integration run reached the macOS CLI suite and found older expectations for
LAN-bound model traffic; these now require loopback while preserving custom
ports and cloud credentials, consistent with the new listener policy.

Release producer contracts pass on Windows and Linux (12 cases each), covering
signature-response rejection, moved tags, unmerged commits, source-only
packaging, reproducibility and symlink refusal. GitHub is the actual signature
verifier; mocked API responses exercise the gate but do not prove production
OIDC signing or published asset verification. No release workflow was dispatched.

Verified consumer contracts exercise the actual PowerShell 5.1/POSIX command
bodies, real archive extraction and fixture installer execution. They constrain
repository, workflow, ref, commit and runner identity before extraction; failed
metadata, tag or attestation checks preserve an existing installation. Paths
include spaces and accented/CJK characters. HTTP and the verifier are controlled
fixtures: real OIDC acceptance remains a first-release gate. Public downloads
avoid GitHub CLI login requirements; only bundle verification invokes `gh`.
The README bodies are checked against the executable scripts to avoid drift.

Rollout must stage the producer before switching public onboarding to the
verified channel. This draft contains both sides for review, but the current
published release is not eligible and no successful new-user installation of
an eligible signed artifact has yet been demonstrated. This is a merge/release
gate, not a reason to add an unsigned fallback.

## Upgrade behavior to review

When a legacy recipe fails current Compose validation, stop/disable operations
now have a recovery path that never evaluates that recipe. Docker's existing
container IDs and Compose labels must match the exact installation directory,
base Compose file and requested service. A shared project name such as `ods`
alone does not authorize stopping another installation. Recovery disables the
matched containers' automatic restart, then stops them without deleting data
or containers. Repair and recreate the reviewed recipe before starting again.
Start and restart operations still reject invalid recipes.

Recovery tests cover Linux/macOS CLI delegation, PowerShell 5.1 stop/disable,
host-agent stop-only recovery, ownership mismatches, malformed Docker metadata,
and preserving recipe files when stopping fails. The dedicated real-container
CI test verifies that another installation with the same project and service
names remains running with its restart policy unchanged. Runtime security CI
`36475219401` on `b6d40a9aa` passed: 21 recovery cases including real containers,
all three saved-stack platform jobs and all nine production image builds.
The local combined cache/recovery run passed 66 cases (one Docker-only skip),
and native PowerShell 5.1 stop/disable passed. Docker Desktop remains stopped
locally. Broader PR checks were still running when this evidence was recorded.
Docker metadata is decoded explicitly as UTF-8 so accented/CJK ownership paths
do not depend on the Windows code page. A real subprocess decoding regression
test forces a legacy default encoding and verifies the path is preserved.

The follow-up lifecycle review also found native background model upgrades still
reading the dashboard's LAN bind, and the macOS doctor probing that address.
Those paths now preserve loopback through model replacement and recovery.
Production argument assembly is tested with LAN/IPv6 settings, custom ports,
model paths containing spaces and GPU/cache options; the Windows restart fixture
and doctor diagnostics also pass. These are controlled runtime fixtures, not
physical Windows GPU or macOS Metal validation.

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
