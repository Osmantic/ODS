# Windows native Portal preview: validation

The demonstrated flow is **EXE setup → existing Portal UI → native local-model
answer → saved conversation → Stop → continue → close/reopen**. This milestone
reuses Portal text-chat contracts; it does not run the OpenClaw/Pixel agent,
workspace tools or the complete ODS service stack.

![Actual native Portal after a Vietnamese answer, stopped inference and another local-model response](docs/native-portal.png)

## Source and environment

- Based on upstream main `bd2a119bb` (2026-10-22). The PR workflow's
  `BUILD-INFO.json` records the exact tested checkout SHA and PR head separately;
  a pull-request checkout can be a synthetic merge commit.
- Windows 10 Pro x64 `10.0.19045`, dual Xeon E5-2686 v4, 80 GiB RAM;
  .NET Framework release `533325`, Microsoft native runtime `14.51.36247.0`,
  WebView2 Runtime `155.0.4283.62`.
- Official llama.cpp b9014 CPU x64 archive, size 15,917,797 bytes,
  SHA-256 `3503cc30154266ac0fbedd38e39e35a313a13412415738ae8f9e23e4fcb44da2`.
- ODS catalog `qwen3.5-2b-q4`, size 1,280,835,840 bytes, SHA-256
  `aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223`.
- WebView2 SDK `1.0.4258.31`, size 9,801,922 bytes, SHA-256
  `56f7f4b8bf9aee4b8efefbbdd4f67d5f74ebd1b100ed0806da71bf76af481aa9`.
- The app uses no Docker/WSL commands or services. This development host has
  those tools available; it is not a clean-machine qualification.

## Executed checks

| Boundary | Result | What it establishes |
| --- | --- | --- |
| Native regression suite | **83 checks passed** | Checked acquisition, real Windows child processes, auth/model identity, legacy WPF recovery, normal/parent-crash Job shutdown, real EXE entrypoint and native Portal adapter |
| Native Portal contracts | Included above | Exact producer payload, UTF-8/SSE result, request idempotency, conflicting payload refusal, origin/tool rejection, conversation deletion isolation/tombstone, exact Stop and idle inspection, retained/unfinished results, schema refusal preserving bytes, pinned asset/manifest tamper refusal |
| Late Stop regression | Former acknowledgement fails; candidate passes | A completed answer remains recoverable and is not replaced by a Stop notice |
| Shared React Portal tests | **186 tests across 7 files passed** | Native profile plus existing normal-mode send, readiness, Stop deadline, stale-writer, keyboard and navigation behavior |
| Real WebView2 fixture | **11 checks passed** | Production-built Portal UI, private native adapter, multi-turn history, close/reopen without inference replay, New chat/select, actual Stop/recovery, external navigation and CSP refusal |
| Actual native inference through Portal | **6 checks passed** | Real model answered `4`, then `Chào bạn!`; Stop during confirmed active inference kept the owned model available; another turn answered `4`; close stopped its exact child and preserved an unrelated listener |
| Frontend lint | Passed, zero errors | Existing warning-only lint policy; 897 warnings reported across the dashboard |
| Frontend builds | Standard dashboard and native builds passed | Existing web bundle and separate native entrypoint both compile; size warnings retained |
| PowerShell analysis | Passed | Native build and test scripts, repository analyzer settings |
| Package entrypoint | Nine checks run by default suite and repeated on review package | Separate EXE process through `Main()`, Unicode path, duplicate-instance refusal, incomplete-package recovery, exit and reopen |

The live harness loaded the production package's WPF setup window, pressed its
real Start button, waited for the owned model and Portal window, and drove the
actual DOM through WebView2's in-process API. Startup with verified caches took
**74.7 seconds** on this host. This is framework-driven execution, not an
external desktop-driver or independent human UX claim. The separate entrypoint
suite starts the actual EXE without downloading weights or starting inference.

An initial real-model run exposed that disconnecting a non-streaming completion
did not quiesce inference within the Stop deadline. The safe fallback stopped
the owned model, so that run failed the keep-model-available assertion. The
native adapter now consumes authenticated llama.cpp SSE with cancellation across
the response body. The final live run above passed. Portal receives a bounded
completed response; incremental frontend token delivery is not implemented.

A separate static review found that late Stop acknowledged already-completed
receipts. The fix preserves completion both before and while awaiting Stop,
and the cancellation delta was re-reviewed. Review was agent-assisted static
analysis; it is separate from maintainer acceptance.

## Reproduce and inspect the package

From `ods/`, follow the [source build instructions](README.md#build-from-source),
then run:

```powershell
.\tests\test-windows-desktop-preview.ps1
.\tests\test-windows-desktop-preview.ps1 -PackageBundle C:\temp\ods-preview
.\tests\test-windows-native-portal.ps1 -Bundle C:\temp\ods-preview
.\tests\test-windows-native-portal.ps1 -Bundle C:\temp\ods-preview `
  -LiveState C:\temp\ods-preview-qa -Screenshot C:\temp\portal.png
```

The screenshot and live results use a compiled package with EXE SHA-256
`944551e67072be79f1fc13c210f54c844c4493a4cc52384f5be649f5d28f47bb`
and Portal DLL SHA-256
`aff11b9b7e32e96cb7626a9f26cc63e41df1423075340f610c92506e3bd98dd8`.
Subsequent documentation/test-only edits do not change those tested application
sources. Hosted builds have their own hashes and custody in `BUILD-INFO.json`
and `portal.json`; compare the artifact's `SHA256SUMS.txt` before transferring it.

The Windows workflow builds the frontend and pinned SDK package, repeats actual
EXE entrypoint and WebView2 fixture tests, checks that only the ODS EXE and four
explicitly allowed Portal/SDK DLLs are present, and uploads an unsigned ZIP.
It downloads no model weights. Frontend tests execute in dashboard CI, and both
PowerShell test scripts are invoked in the Windows workflow. Passing local
checks do not assert that hosted checks or release approval have happened.

## Deferred product and release gates

- Full OpenClaw/Pixel ingress, authenticated host operations, workspace/artifact
  delivery, agent/tool/extension/voice/image execution and the full ODS harness.
  They require native implementations that preserve existing ownership,
  cancellation and persisted-state contracts; this text profile does not bypass
  their Linux services.
- Clean Windows 10/11 first-use and prerequisite installation, accessibility,
  real network/proxy/offline recovery, WebView2 update compatibility and
  independent human review.
- Hardware-aware CPU/GPU routing, model swap and broader hardware/performance
  qualification. One compact CPU model and smoke prompts do not qualify those.
- Signed website installer, update/rollback/removal lifecycle and fleet
  acceptance. The [runtime distribution gate](README.md#runtime-distribution-gate)
  remains open; no llama runtime binary is redistributed in this review ZIP.

Rollback is to close the preview and retain or remove its separate folder/state.
The supported installer and normal Portal mode retain their existing defaults.

## Collaboration

tang-vu leads this contribution, its product direction, scope and quality
decisions, collaborating with Codex on implementation, analysis, automated tests
and review. The command and test results recorded here were agent-executed.
Maintainer review and release acceptance remain separate gates.
