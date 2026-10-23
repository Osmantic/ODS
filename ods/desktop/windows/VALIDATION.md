# Windows native Portal installer: validation

The demonstrated scope is **graphical install → native setup → existing Portal
→ CPU chat or explicit Create document → verified Download → Windows save
dialog → saved conversation → Stop/continue → reopen**. Full OpenClaw/Pixel
agents, host tools, workspace editing and the full ODS harness remain outside
this preview. The document capability is one bounded native Markdown tool.

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
| Native regression suite | **264 passed at the document milestone** | Acquisition/cancellation, real Windows children, Job cleanup, storage capacity/recovery/API errors, exact-token context selection, cached repair/reopen, reflection errors, model/auth identity, Portal history/result/Stop, document publication/ownership/HTTP tool calls and runtime refusal; ownership follow-up below |
| Actual EXE | **11 passed**, repeated on package | Real Main entrypoint, Unicode path, duplicate instance, incomplete-package recovery, reopen and side-effect-free idle/busy installation probe |
| Graphical installer lifecycle | **23 passed** | Real wizard cancellation/install/Finish launch; HKCU and Start Menu target; busy upgrade/uninstall refusal; data-retention notice/cancellation; repair; upgrade; downgrade refusal; failed directory inspection; descendant junction refusal; removal; foreign/saved-data preservation |
| Installed Portal UI | **11 passed** | Actual production frontend in WebView2, multi-turn text, history, New chat/select, Stop/recovery, external navigation/CSP refusal |
| Native document WebView flow | **31 passed** | Explicit mode, completed host receipt, real Markdown save/cancel, exact bytes/hash/path, small and >64 KiB JSON export, delayed owner choice, reopening without replay and default-path preservation |
| Real model document through Portal | **11 passed** | Genuine allowlisted model call, validated tool result and final answer, durable artifact/card, real cancel/save, exact document bytes/hash, idle slot and owned process shutdown |
| History after repair/upgrade/uninstall | **2 passed** | Actual saved Portal history remains readable, exact result bytes retained, no inference replay |
| Real included engine/model through Portal | **9 passed** | Math/Vietnamese; Stop/continue; over-context history fits without mutation; oversized latest prompt refuses before inference; next request succeeds; owned child closes and foreign listener remains |
| Real-model context negative control | **3 passed** | Prior unbounded request receives HTTP 400/context refusal; exact overflowing history fits without modification; corrected request answers 4 |
| Build and analysis | Passed | Source-built engine with PE dependency gate, .NET application, Inno compiler, pinned compiler/bootstrapper acquisition and PowerShell analysis |

Installer tests use a unique application ID, private install/data paths and
their own shortcut/registration. They do not install the default product into
the developer's real preview profile. Saved-history assertions operate on an
actual private WebView2/result fixture; they are not a separate claim about all
user profiles. Failed and completed fixture/log directories are retained for
inspection after owned uninstallation.

The model probe pressed the production WPF setup's real Start button and drove
Portal's actual DOM through WebView2. Verified-cache startup took **23.1 seconds**
on this host in the review follow-up (the earlier installer milestone took
76.2 seconds). The Vietnamese response was “Chào bạn! Rất vui được làm bạn.”
These single observations are not a controlled speed comparison. This is
automated framework-driven execution, not independent
human UX acceptance or a broader performance benchmark.

A first new-runtime live run passed two model turns but timed out in the
harness's read-only active-slot observation under concurrent setup work.
Only that observer now treats its two-second cancellation as unavailable and
continues the existing bounded wait for a real `is_processing=true`. The product
Stop/idle guard was not weakened. The subsequent six-check live run passed.

## Native Markdown follow-up (2026-10-23)

![Actual native Portal after the pinned model created and saved a Markdown document](docs/native-document.png)

Chat stays the default. Explicit Create document admits one `create_markdown`
call with fixed `document.md`, strict UTF-8 and a 64 KiB bound. The actual tool
schema, requested output and tool result take part in both rendered-prompt
token budgets. The model receives the host's staged byte count/hash before its
final summary. No shell, host paths, file reads or network tools are exposed.

Only an atomic completed v2 receipt publishes a document; Markdown is inline in
that receipt, avoiding separate blob-file/orphan transactions. Reads revalidate
private file handles, ACLs, hashes and durable tombstones. The owner index selects
only relevant receipts, without treating cached bytes as authority. Shared
content remains available while another completed chat owns it. Stop, close,
provider failure and quota exhaustion after admission publish no document.
Existing v1 text results remain readable and are not rewritten or evicted.

Within the **264-check native suite**, document coverage includes **37** store
and path/ACL controls, **24** exact tool-budget controls, **27** admitted API,
quota, cancellation, restart and deletion controls, and **13** real authenticated
HTTP tool-round/history/refusal/cancellation checks. These are included in the
suite total. Four budget mutants were rejected, including loss of the tool-result
reserve, incorrect tokenizer flags, partial-turn trimming and dropped schemas.

The real model produced **452 bytes**, SHA-256
`cebf8f84fd67bb2fe662b9a84a00732fda13553deb2e0bc78f38606632cb79b3`,
containing the requested title and three practical notes. Actual WebView Download
verified and saved those bytes through the production Windows picker. The fixture
also exported complete **1,552-byte** and **74,173-byte** conversations as JSON.
Accepted Markdown/JSON choices were held for two seconds, beyond the frontend's
one-second Blob-URL revocation timer, and still completed with exact bytes.
Content integrity is not a claim about model factual quality or speed.

Independent static review caught the first download handler applying the Markdown
name/size cap to existing conversation exports. MIME/filename classification now
preserves JSON export and its Windows picker. Actual UI qualification then exposed
synchronous modal UI inside WebView2's download callback. The corrected handler
uses the pinned SDK's [deferred-post pattern](https://learn.microsoft.com/dotnet/api/microsoft.web.webview2.core.corewebview2.downloadstarting?view=webview2-dotnet-1.0.4258.31);
Cancel remains the default until explicit owner acceptance. That safety delta
was independently reviewed before the passing fixture/live-model runs.

Intermediate harness failures were retained: UIAutomation's root tree missed an
owned modal; script-only clicks lacked browser activation; a filename value echoed
without committing Shell edit notifications; and object marshalling reordered
expected JSON keys. The final driver binds controls to its exact PID/Portal owner,
uses native edit notifications and private-WebView input, compares raw saved
producer input to exported bytes, and runs from a private working directory with
an unchanged default-path sentinel. One 76-byte fixture output from the earlier
filename test was verified and preserved separately; no overwrite confirmation
was accepted. A protocol fixture initially asserted the wrong prior turn count,
and cleanup masked that assertion during child-handle release; the corrected
count and bounded cleanup pass in the final native run. These were failed
attempts, not additional passing results.

Installer version **0.2.0.0** separates this schema-capable build from the earlier
0.1 preview. Both PowerShell versions pass **8** numeric version/default/refusal
checks before package reads, downloads or output changes. The builder refuses
older labels. Setup's existing numeric downgrade guard protects an existing 0.2
installation; old portable EXEs and reinstalls after uninstall are outside that
guard. Earlier readers refuse document histories while preserving their bytes;
keep a backup and use 0.2 or newer to reopen them.

The full frontend run passed **3,035 tests across 253 files** on Windows, with
native and normal builds passing. ESLint reported **zero errors / 897 existing
warnings**. The new graphical fixture is executed explicitly in the path-filtered
native workflow; catalog-model execution remains a separately recorded local
gate, with no model download added to CI.

### Hosted Windows ownership follow-up

The first hosted run at `536578c7a` passed **74 other checks** but failed the
native suite while reopening legacy default-owner files. The earlier 0.1 producer
used an ordinary `FileStream`, so an elevated Windows token can create a file
owned by exact `BUILTIN\Administrators` despite its private current-user-only
DACL. The new file-owner gate initially rejected that legitimate history. Local
non-elevated tests did not exercise that ownership choice.

Reads now accept only the current SID or that exact privileged built-in owner.
Administrators ownership additionally requires a freshly validated current-SID
private parent. Foreign allow grants, arbitrary owners, links and failed reads
still refuse, and no read changes ownership/permissions. New receipts and
tombstones explicitly use the current SID. Independent review checked the safety
delta and the unchanged-byte/foreign-owner/grant controls.

The focused standard-user qualification passed **41 document + 19 existing state
checks**. Four unconditional regressions recreate the old default-owner producer
and exercise recovery, update, tombstones and deletion; four additional tests
require an already elevated token and record whether actual Administrators and
foreign-owner branches executed. The local host records its privilege limit and
does not attempt elevation. Hosted logs provide the actual token/owner evidence;
a standard-user pass does not establish those elevated paths.

The actual retained 0.1 application also refused a real v2 receipt while keeping
its exact bytes (**2 checks**). The final local installer passed
**23 lifecycle + 2 saved-history checks**, and the rebuilt package passed the **9-check actual
text-model flow**, including Stop, context fitting and recovery.

One repeated native frontend build on Node 24.21.0 emitted its output and then
aborted during process shutdown with a libuv assertion. That attempt was recorded
as failed. Fresh native and normal builds on CI's declared Node 22 line
(22.23.4, verified official archive/executable digests) passed; native assets
matched the already exercised package byte-for-byte.

## Regression and review evidence

### Scalar CPU thread budget

Qualification of downloaded artifact `39833793883` exposed a real document
timeout on this dual-socket host. Setup/Finish and all 11 EXE checks passed,
but the document card never completed. An isolated exact-payload model probe
then confirmed the first tool request reached its unchanged five-minute
deadline: only 41 tokens had decoded after about 260 seconds. A UI timeout
receipt alone did not establish the failing phase; phase-timed HTTP evidence
did. The previous failed run and its normal owned uninstall are retained.

Pinned b9014's MinGW build falls back to logical CPU counts for its default
worker budget. This host reports 72 logical CPUs system-wide and 36 to .NET
Framework. Excess workers hurt the scalar profile here. With the same checked
CI engine/model and prompt, explicit eight-thread generation/batch budgets
completed both tool rounds in **76.99 seconds**, including startup. The first
request took 49.67 seconds (138 output tokens), and the final reply took 14.56
seconds (19 output tokens). The resulting document was 503 bytes. Sampling is
not fixed, so these observations are not a controlled speed ratio or a universal
benchmark.

The launcher now explicitly caps matching generation/batch budgets at eight,
preserving the existing lower-core selection. Context, output budgets, the
five-minute deadline, authentication, integrity and Job ownership are unchanged.
Boundary checks cover low-core and large logical counts; the actual fixture
child rejects missing, duplicate, mismatched or unbounded flags and records its
received budget. Poisoned inherited thread defaults must still be stripped.
The policy/argv/environment delta received independent static review.

The corrected source passed **277 native checks** locally and the full **11-check
real-model document WebView flow**, including actual Cancel/Save, exact bytes and
hash, idle confirmation and exact-child closure. That UI run produced a 547-byte
document. A private negative control removing only the two thread arguments
failed at the actual fixture child's startup; the unchanged policy boundaries
passed first. No timeout, output limit or safety check was relaxed to make the
qualification pass.

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
Those are historical results. The review follow-up at `dfa6c744d` changes native
React behavior and passed **3,021 tests across 252 files locally on Windows**,
both native and standard frontend builds, and ESLint with zero errors (897
existing warnings). Hosted results are reported against the actual head on the
PR; prior green checks are not substituted for current-head validation.

### Community review follow-up

[IronicRayquaza's review](https://github.com/Osmantic/ODS/pull/7938#issuecomment-6345076519)
identified lifetime storage exhaustion, over-context conversations, slow-download
failure, duplicate startup hashing and wrapped constructor errors. The follow-up
keeps existing results accessible above the admission budget, preserves drafts
on typed HTTP 507 refusal, and handles deletion's separate error consumer.
Exact server template/tokenizer calls select complete recent turns while keeping
saved history unchanged and reserving 512 output tokens.

Real loopback regressions cover progressive downloads lasting multiple scaled
inactivity windows, stalled headers/body, cancellation and preservation of an
older cache. Cached source-built launches select an unverified candidate without
reading its bytes, then pass one mandatory CNG SHA-256 gate before creating a
child or session key. A corrupt installed seed repairs into private cache; a
second launch makes no extra download. Unsafe paths and runtime corruption never
authorize model repair. WPF tests cover nested reflection errors and distinct
setup timeout guidance. The two WebView2 version boundaries now share the pinned
SDK's **154.0.4258.31** minimum (14 boundary checks on PowerShell 5.1 and 7).

Independent static review checked the combined launch/repair paths and the
template/tokenizer contract against the shipped b9014 source. It caught and
resolved a repeated corrupt-seed download loop and loss of actionable DELETE
capacity guidance. New native tests run in the path-filtered **Windows native
preview** workflow; heavy build/model-free package/lifecycle tests are removed
from unrelated PRs' PowerShell lint job. The CI test inventory passes: 1,317
executed tests, 19 explicitly recorded live/platform exclusions.

An intermediate combined run exposed an asynchronous fixture-server teardown
failure. Its dedicated server thread now shuts down independently of blocked
thread-pool continuations; the final 163-check run passed. A separate installer
automation attempt saw an enumerated window disappear during inspection; its
harness now retries only `ElementNotAvailableException` within the existing
process-ownership and overall-timeout boundaries.

## CI and reproduction

The ownership follow-up passed **268 native checks** locally. The generated
Sandbox handoff is also exercised through its actual copied driver: **31
document/JSON/save checks passed** against the packaged app. The previous kit
omitted `DocumentSaveDialogTests.cs`; regenerating it from the earlier source
reproduced a compiler failure before UI launch. The corrected copy list and
copied-driver CI execution prevent that dependency gap. The guest driver now
runs real chat and document/save qualification against the same model cache,
recording both phases separately. These host-side handoff checks do not claim
that a fresh Sandbox guest has run.

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

## Clean Windows qualification and remaining gates

The October 23 clean Sandbox run completed **real Setup, missing-WebView2
installation, first model acquisition, nine chat checks and eleven document
checks**, using the downloaded CI installer for head `79adbb765` and the
reviewed QA helpers committed at `a9d9267f6`. The Setup SHA-256 was
`818f61b2888cf9caa1d5bfbcad6ea00666af78c6ddf82fd9bb57862a3312d8af`.
Its frozen installer/QA inputs and WSB mappings remained unchanged throughout
the run. The final guest receipt reported both model phases passed, and the
two native runner receipts recorded exit zero with empty stderr. Both actual
WebView screenshots were inspected; the README images now come from this run.

The guest reported Windows 10 Enterprise x64 build 19041, PowerShell
5.1.19041.6456 and an elevated WDAGUtilityAccount token. Git, Node, Docker and
Python were absent. The inbox `wsl` command was present; optional-feature
inventory was unavailable, so this is not a claim that every Windows feature
was enumerated or absent. The app used its included static CPU engine and
downloaded the pinned 1.28 GB model inside the guest without a host cache.
The real Microsoft bootstrapper installed WebView2 **156.0.4314.42**; Setup
registered ODS per user in HKCU, created its Start Menu entry, and Finish
launched the exact installed executable without a reboot.

Portal became ready in **106.1 seconds** including acquisition/startup on this
one guest. Real checks covered a math response, a Vietnamese turn, Stop and
subsequent recovery, over-context history fitting, oversized-prompt refusal,
the next successful request and exact-child closure with an unrelated listener
preserved. The actual document tool/result/final answer produced **511 bytes**,
SHA-256 `ed570043d80a96b0f021364415eb0f8058629d54ef27955f600e3be05a627ca4`.
The native picker first cancelled without writing, then saved the exact bytes
and hash to the chosen fixture path. Document inference ended idle and its
owned model child closed. This is one CPU/Windows 10 qualification, not a
hardware benchmark or Windows 11/fleet approval.

Private qualification evidence hashes all eleven guest output files, both
runner receipts and the frozen input manifest. The initial automatic host
observer refused an early process-image observation; that failure was retained.
The main session independently verified the exact live launcher/client,
continued observing that same guest and checked its completed evidence. No
observer timeout or failure was used to infer guest termination or restart it.

### QA recovery evidence

The first Windows 10 Sandbox attempt after the authorized October 23 host
reboot reached the guest desktop and the exact mapped build receipt was
readable. Private PowerShell engine logs show the QA script starting and
exiting before Setup; the correlated WMI event identifies
`Win32_OptionalFeature` failing with `0x80070422`. That optional diagnostic ran
before the transcript and result finalizer, leaving no guest log/results. This
was a QA bootstrap failure; no clean installation/model pass was claimed.

The handoff now starts its transcript after the unchanged mandatory guest guard
and before inventory. An optional-feature provider failure is recorded as
`unavailable` with its error, then the actual application checks continue.
Unknown inventory is never represented as verified absence of Windows
features. OS identification, clean-tool refusal, installer integrity, actual
Setup, model and save checks remain mandatory failures. Phase markers locate
later failures, and Finish-launched cleanup requires the retained process,
creation time, exact path and build-receipt application hash. Isolated bootstrap
contracts passed **seven cases each** through the actual script in inbox
PowerShell 5.1 and 7, including the private catch-to-throw negative control.
Both interpreter runs are wired into native CI. Their private dummy payload
refuses before compilation or installation; these tests are not live guest
application acceptance.

The corrected handoff for head `79adbb765fecf1e87b7f2e95d35d8a8a7322bd07`
then ran in a fresh Windows 10 Enterprise x64 Sandbox (reported build 19041,
PowerShell 5.1.19041.6456, actual token elevated). The exact downloaded
[CI artifact](https://github.com/Osmantic/ODS/actions/runs/39900623705/artifacts/12478532856)
Setup SHA-256 was
`818f61b2888cf9caa1d5bfbcad6ea00666af78c6ddf82fd9bb57862a3312d8af`.
All 75 checks passed on that head. The guest reported Git, Node, Docker and
Python absent; the inbox `wsl` command was present, which does not establish
an installed distribution or application dependence on it. Optional-feature
inventory was explicitly unavailable. Setup encountered missing WebView2,
accepted the actual component prompt, ran Microsoft's checked bootstrapper,
installed per user into HKCU, created the Start Menu shortcut and launched the
exact installed application after Finish. Setup logged success without a
restart. These are real clean-guest installation results, not publisher stubs.

That attempt failed in `chat_model` before model/chat/document acceptance.
The native UI runner returned failure after its five-minute startup wait, but
its stdout/stderr were missing from the PowerShell transcript; the generic
wrapper exception was not enough to establish the root cause. The preserved
guest and failed receipt remain separate from the completed Setup result.
An optional elevated HCS diagnostic was cancelled before execution, so no
guest TLS probe result is claimed.

Review found that this runner constructs `ChatWindow` directly and bypasses
the application's `App.Main` TLS 1.2 initialization. The test entry point now
uses that same policy. Its shared startup wait follows only the current
startup window's Portal, logs actual status/detail and reports a terminal
production recovery message immediately; both live waits retain 300,000 ms.
The actual entry-point policy and a genuine missing-Portal-package error
passed two isolated startup contracts locally. Private source mutants using
TLS 1.0 or removing terminal-error handling both failed at their intended
boundaries. A package without a checked Portal is refused before the startup
fixture can attempt any model acquisition. A separate x64 console probe,
compiled with the same inbox compiler on the development host, selected
`Ssl3, Tls` by default and failed a HEAD request to the pinned model URL with
"Could not create SSL/TLS secure channel"; selecting TLS 1.2 in that process
returned HTTP 200. This is an actual host reproduction, not a guest probe.
This repairs a demonstrated harness discrepancy and diagnostic
gap; it does not retrospectively prove the failed guest's download cause or
turn that failed attempt into a model pass. The subsequent successful clean
run is recorded above.

The Portal driver now retains UTF-8 runner stdout, stderr and exact exit status
outside its disposable fixture when `DiagnosticsDirectory` is supplied. Both
streams are drained concurrently before the native exit is checked, and
stderr is also reported through the PowerShell success stream so it survives
the guest transcript. Nonzero exit still fails the test; empty stderr is not
treated as evidence of success. Chat and document guests use separate output
subdirectories. The native workflow runs the driver contracts in both inbox
PowerShell and PowerShell 7, executes the actual startup contracts with the
built Portal package, and uploads only controlled stdout/stderr/runner JSON
diagnostics even when a test fails. Incomplete installers remain unpublished.

All six copied-driver console cases passed locally in PowerShell 5.1 and 7:
empty streams, simultaneous 128 KiB UTF-8 streams with exit 17, startup-mode
dispatch, an empty screenshot argument, mode rejection and a lost-stderr
negative control. Spaced/Unicode/trailing-backslash paths, private working
directory, sentinel preservation and cleanup are checked through the actual
driver. The first PowerShell 5 fixture attempt exposed an ANSI interpretation
of its generated Unicode invoker; UTF-8 BOM and explicit UTF-8 JSON reads fixed
that test-only issue. The real PowerShell 5 driver also passed its two startup
contracts while retaining stdout/stderr/exit-zero evidence. Static analysis
reported no warnings or errors for the driver and its regression. Independent
static review covered the driver, console isolation and startup changes;
the successful live guest is recorded above, while new-head hosted execution
is recorded separately by its own CI result.

The October 23 follow-up adds **14 real-Setup prerequisite checks** using a
private publisher executable and a fresh GUID-scoped registry producer. Only
the fixture's registry-key literal and publisher input differ from production
Setup. The suite covers silent refusal, declining the real prompt, publisher
failure, successful exit without a compatible runtime, a below-floor version,
failure to execute the publisher, and recovery in the same wizard. It verifies
no premature application/registration/shortcut, preserved saved bytes, exact
Finish-launched application ownership and normal removal. The existing **23
lifecycle + 2 history checks** also passed with the updated UI driver. Both
suites execute in the Windows native workflow. This is isolated publisher
contract coverage; actual Microsoft acquisition in a clean guest remains a
separate gate below.

Intermediate fixture runs exposed legacy Inno controls reported as `Pane`, an
incorrect fake-publisher basename and a cleanup race with Inno's self-deleting
uninstaller. The final driver recognizes the known native control classes,
posts clicks only to verified process-owned controls, handles bounded ordinary
cancellation and never invokes the completed uninstaller twice. Failed attempts
were retained; the corrected complete runs exited successfully.

The preceding Windows analyzer job imported PSScriptAnalyzer successfully but
failed at the Actions footer with a stale native NuGet exit code. The bootstrap
now declares success only after verifying the imported module and its analyzer
command. **Six isolated child-PowerShell contracts** execute the actual workflow
block with the Actions footer: handled native failure succeeds, while install,
import, missing-module and missing-command failures still fail. The old-block
negative control retains the failure. These contracts run on both lint hosts;
new-head hosted dependency acquisition is qualified by its own CI result.

On head `70557fca1`, both hosted analyzer jobs passed, as did **281 native**
and **11 packaged-EXE** checks. The subsequent WebView test timed out during
initialization before installer tests or artifact publication; the cause was
not established. The UI harness now logs available/selected runtime and the
initialization exception, and immediately reports production recovery text.
Its readiness condition and 30-second deadline are unchanged. The updated
harness passed all **11 Portal checks** locally; that does not erase the
hosted failure or qualify a new artifact.

- **Additional clean-environment gates:** a non-elevated fresh account, actual
  Microsoft acquisition cancellation/network failure, and offline/proxy
  recovery. The real Windows 10 Sandbox install/model/chat/document run above
  passed; its token was elevated. The fourteen isolated publisher contracts
  cover prerequisite refusal/failure/recovery but do not replace these wider
  live environments. Kit generation alone is not guest acceptance.
- Windows 11 and wider hardware qualification, accessibility and independent
  human review. A single non-elevated development host does not establish these.
- Signed public website release, SmartScreen reputation, authenticated update
  delivery/rollback and fleet acceptance. Current repair/manual-upgrade/removal
  tests do not qualify automatic updates. The application's idle probe is
  advisory, not an atomic update lock.
- Full OpenClaw/Pixel agents, host tools, workspace editing/delivery, voice, images,
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
