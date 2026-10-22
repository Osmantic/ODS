# Windows native Portal installer: validation

The demonstrated scope is **graphical install → native setup → existing Portal
text UI → CPU model answer → saved conversation → Stop → continue → reopen**.
OpenClaw/Pixel agents, host tools, workspace artifacts and the full ODS harness
remain outside this preview.

![Actual native Portal after a Vietnamese response, Stop and another CPU-model answer](docs/native-portal.png)

## Source and environment

- Installer work is based on upstream main `72e59cdc6` (2026-10-22), merged into
  the existing preview branch. `BUILD-INFO.json` records the exact tested
  checkout and PR head separately, including synthetic PR merge checkouts.
- Windows 10 Pro x64 build 19045, dual Xeon E5-2686 v4, 80 GiB RAM; .NET
  Framework release 533325, WebView2 Runtime 155.0.4283.62.
- The installer lifecycle was executed with a **non-elevated** token on this
  development host. Docker/WSL exist on this host but were not used by the app.
  This is not a clean Windows result.
- Engine: llama.cpp b9014 source `d4b0c22f9e67f0295e91dc1ab4f17c0fb2557fa4`,
  LLVM-MinGW UCRT 20261020, static baseline x64 CPU; OpenMP/TLS/dynamic backends
  disabled. One exact missing-header portability patch is retained in the build
  receipt. PE imports were checked against the Windows system DLL allowlist.
- Locally qualified engine: 13,929,472 bytes, SHA-256
  `e356d042985363fbff84b5992aae3932a666aeae149cf1a8a9aef7fc894fef1a`.
  Hosted rebuilds have their own exact hashes; binary reproducibility across
  builders is not claimed.
- ODS catalog `qwen3.5-2b-q4`: 1,280,835,840 bytes, SHA-256
  `aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223`.
- WebView2 SDK 1.0.4258.31: SHA-256
  `56f7f4b8bf9aee4b8efefbbdd4f67d5f74ebd1b100ed0806da71bf76af481aa9`.
  Compiler, source archive, bootstrapper and tool pins are in
  `native-build-pins.json`. The WebView2 bootstrapper also passed the Microsoft
  Authenticode signer check.

## Executed local checks

| Boundary | Result | What it establishes |
| --- | --- | --- |
| Native regression suite | **89 passed** | Acquisition/cancellation, real Windows children, Job cleanup on parent crash, model/auth identity, Portal request/history/result/Stop behavior, bundled engine tamper and unexpected-file refusal |
| Actual EXE | **11 passed**, repeated on package | Real Main entrypoint, Unicode path, duplicate instance, incomplete-package recovery, reopen and side-effect-free idle/busy installation probe |
| Graphical installer lifecycle | **22 passed** | Real wizard cancellation/install/Finish launch; HKCU and Start Menu target; busy upgrade/uninstall refusal; repair; upgrade; downgrade refusal; failed directory inspection; descendant junction refusal; removal; foreign/saved-data preservation |
| Installed Portal UI | **11 passed** | Actual production frontend in WebView2, multi-turn text, history, New chat/select, Stop/recovery, external navigation/CSP refusal |
| History after repair/upgrade/uninstall | **2 passed** | Actual saved Portal history remains readable, exact result bytes retained, no inference replay |
| Real included engine/model through Portal | **6 passed** | Math answered 4; Vietnamese answer; Stop during confirmed active inference; continued answer 4; exact child stopped at close; unrelated listener preserved |
| Build and analysis | Passed | Source-built engine with PE dependency gate, .NET application, Inno compiler, pinned compiler/bootstrapper acquisition and PowerShell analysis |

Installer tests use a unique application ID, private install/data paths and
their own shortcut/registration. They do not install the default product into
the developer's real preview profile. Saved-history assertions operate on an
actual private WebView2/result fixture; they are not a separate claim about all
user profiles. Failed and completed fixture/log directories are retained for
inspection after owned uninstallation.

The model probe pressed the production WPF setup's real Start button and drove
Portal's actual DOM through WebView2. Verified-cache startup took **76.2 seconds**
on this host. The Vietnamese response was “Chào mừng bạn đến với cuộc trò chuyện
của chúng ta!” This is automated framework-driven execution, not independent
human UX acceptance or a broader performance benchmark.

A first new-runtime live run passed two model turns but timed out in the
harness's read-only active-slot observation under concurrent setup work.
Only that observer now treats its two-second cancellation as unavailable and
continues the existing bounded wait for a real `is_processing=true`. The product
Stop/idle guard was not weakened. The subsequent six-check live run passed.

## Regression and review evidence

A separate static review found an installer safety gap: failed filesystem
enumeration could be treated as an empty/link-free tree. The corrected path
uses direct Win32 enumeration with retained last-error codes; only expected
absence/end-of-enumeration is accepted. An actual list-directory DENY ACL
regression now proves repair and uninstall refuse before changing owned bytes.
A substituted descendant junction likewise refuses both operations and
preserves the foreign target. The corrected safety delta was re-reviewed.

Earlier Portal work also fixed a late Stop overwriting a completed result and
changed model response consumption to authenticated cancellable SSE. Actual
inference confirms Stop can quiesce a live model while retaining it for another
turn. Portal still receives bounded completed text rather than incremental
frontend tokens.

The previously published Portal head `841854214` passed 74/74 hosted checks,
including **3,014 frontend tests across 252 files** on Linux, macOS and Windows.
The installer update changes no React frontend source. Its hosted results and
artifact custody are reported against its own head on the PR; prior green
checks are not substituted for current-head validation.

## CI and reproduction

[Build and test commands](README.md#build-from-source) are in the preview README.
Windows CI builds the pinned engine from source, builds the frontend and .NET
app, executes native/entrypoint/WebView2/installer tests, checks the binary
census, builds the installer and produces an isolated Sandbox QA kit.
It downloads no model weights. Frontend tests execute in dashboard CI.

The artifact includes an unsigned Setup EXE, portable ZIP, SHA-256 sums, build
receipts, installer logs and QA kit. The native package contains only its ODS
EXE, static CPU engine EXE and four allowed Portal/WebView2 DLLs. The separate
installer embeds the checked Microsoft WebView2 bootstrapper. Complete engine,
toolchain, frontend, SDK and installer-engine notices are retained.

## Deferred acceptance gates

- **Clean Windows first use**, especially missing WebView2 installation,
  prerequisite cancellation/failure and offline/proxy recovery. The prepared
  Sandbox kit records OS/token/tool availability, installer hash, real model
  results and screenshot; generating it is not successful guest execution.
  This host has Sandbox/Hyper-V management disabled and no prepared Windows VM.
  No host feature or reboot was triggered. A guest token is recorded rather
  than assumed non-elevated.
- Windows 11 and wider hardware qualification, accessibility and independent
  human review. A single non-elevated development host does not establish these.
- Signed public website release, SmartScreen reputation, authenticated update
  delivery/rollback and fleet acceptance. Current repair/manual-upgrade/removal
  tests do not qualify automatic updates. The application's idle probe is
  advisory, not an atomic update lock.
- Full OpenClaw/Pixel agent, tools, workspace/artifact delivery, voice, images,
  extensions, GPU routing and model switching. Unsupported controls/API routes
  remain refused rather than presenting incomplete services as ready.

The preview's data stays separate from existing ODS stacks. Uninstall removes
registered application files and leaves saved model/history state available.
The supported installer and normal Portal mode retain their existing defaults.

## Collaboration

tang-vu leads this contribution, its product direction, scope and quality
decisions, collaborating with Codex on implementation, analysis, automated tests
and review. Recorded command/test results were agent-executed. Maintainer review
and release acceptance remain separate gates.
