# ODS Windows native Portal preview

Install a Windows application, open ODS Portal, and talk to a local CPU model
or create a short Markdown document.
The preview reuses Portal's composer, message rendering, conversation library,
saved history and Stop flow inside a native WPF/WebView2 window. Running it
requires no Docker, WSL, Git, Node, Python or terminal. ODS installs and runs
for the current Windows account.

**Supported flow:** install → launch → download the checked model → Portal →
local answer → explicit Create document → verified Download → Windows save
dialog → reopen saved history. Stop and another turn remain available.
Full OpenClaw/Pixel agents, host tools, workspace editing, voice, images,
extensions and model switching remain outside this preview. Their controls are
hidden and the native API refuses unsupported operations. Existing ODS
installations and the supported `install.ps1` path are separate.

See [validation](VALIDATION.md) for executed checks and release gates.

## Optional NVIDIA integration candidate

The isolated integration build combines this desktop preview with the reviewed
x64 NVIDIA pin from #8014. Build with `-IncludeCudaPreview` to include the checked
CUDA metadata. Ordinary builds retain the CPU default and need no GPU driver.

In an integration package, choose **NVIDIA CUDA (opt-in)** before Start. The app
checks the Windows driver and compiled compute targets before downloading two
official ggml-org CUDA archives (about 606 MB combined). It verifies both archives
and the complete extracted file census, then checks all runtime/model bytes again
before launching. The same 2B model and 4K context run inside the application's
Windows Job; this route uses no service, scheduled task, Docker or WSL.

CUDA requires the Microsoft Visual C++ v14.44+ x64 runtime, a compatible NVIDIA
driver (551.78+), and this preview's 4 GB VRAM admission floor. Only listed compute
targets are accepted; the pinned CUDA 12.4 engine excludes RTX 50-series GPUs.
The actual tested GPU is RTX 3060 12 GB; wider hardware remains a release gate.
Allow about 6 GB free disk for first acquisition/cache. GPU failure explains how
to retry or explicitly choose CPU; it never silently switches backends.
The private choice survives restart and leaves saved conversations intact.

This remains an unsigned integration candidate, separate from the qualified CPU
artifact described below. It does not make the full ODS agents stack native.

[Watch the actual two-minute CUDA demo](docs/native-cuda-demo.mp4). It shows
automated live QA with cached runtime/model bytes, recorded cuts, real Vietnamese
chat, Stop/continue, document creation and the native Windows Save picker.
The picker navigation is cropped to omit unrelated local folders.

## Install and use

Download **ods-windows-native-preview-<run id>** from the PR's **Windows native
preview** workflow Artifacts section (GitHub sign-in required). Check
`SHA256SUMS.txt` against the downloaded files. `INSTALLER-BUILD-INFO.json`
identifies installer/application/runtime hashes; the portable ZIP's
`BUILD-INFO.json` records the checkout and PR head separately.

1. Double-click **ODS-Windows-Preview-Setup.exe** in a normal Windows session.
2. Follow the wizard. Its default destination is
   `%LOCALAPPDATA%\Programs\ODS Windows Preview`. It creates a Start Menu entry
   and a current-user Apps & features uninstall entry.
3. If Microsoft WebView2 Runtime 154.0.4258.31 or newer is missing, Setup asks before
   running the pinned, Microsoft-signed Evergreen bootstrapper. An Internet
   connection is needed. Cancelling or failed prerequisite setup stops ODS setup.
4. Choose **Open ODS Windows Preview** on Finish, then **Download & start
   (1.28 GB)**. Wait for model verification and startup, type a message in Portal
   and press Enter. Later launches use **Start local AI** when the model is cached.
5. Use **New chat**, select a saved conversation, or press **Stop** during a
   response. Once Stop is acknowledged, another turn can continue.
6. To request a short file, select **Create document**, describe it and send.
   A completed result shows **Download document.md**. Download verifies the
   saved bytes, then Windows asks where to save them. Cancelling saves nothing.
   Chat is the default again when you reopen or select another conversation.

This is an **unsigned development preview**, not an official website release.
Signing, SmartScreen reputation and public website distribution remain release
work. The source-built CPU engine is included; no MSVC/OpenMP installer is
needed. Requirements are Windows 10 build 19041 or newer/Windows 11 **x64**,
.NET Framework 4.8 and WebView2 Runtime. ARM64 is not qualified. Allow about
3 GB free disk and at least 4 GB available RAM; these are preview estimates.

The WebView2 bootstrapper is invoked unelevated. Microsoft may promote its
installation to machine servicing when a shared Edge updater is present; ODS
does not promise exclusive per-user ownership of that component and never
uninstalls it. See [Microsoft's distribution contract](https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution).
If an older shared runtime cannot be serviced from this account, ask the device
administrator to update or repair Microsoft WebView2 before retrying ODS Setup.

## Repair, upgrades and removal

Version 0.3 adds the optional NVIDIA choice in metadata-enabled packages and
preserves existing text/document histories. CPU remains the default.

Version 0.2 adds saved Markdown documents and reads existing text histories.
Earlier previews cannot open histories containing documents. Back up `portal/`
before changing versions and use 0.2 or newer. Setup refuses an older version
over an existing 0.2 installation. This guard cannot cover old portable EXEs or
reinstallation after uninstall: retained document histories still require 0.2+.

Close ODS, then rerun the same installer to repair its owned files, or a newer
installer to upgrade. Setup refuses downgrades, unrecognized installation
receipts, nonempty foreign destinations, links and failed folder inspection.
It checks the current Windows account's running application without terminating
it. The idle check is advisory; it does not establish an atomic cross-process
upgrade lock. Setup never forces application shutdown or a Windows restart.

Remove **ODS Windows Preview** in Apps & features. The uninstaller removes its
logged binaries, shortcut and registration. It preserves foreign files and all
saved conversations/model files, which live outside the installation folder.
It refuses a running app or uninspectable/substituted installation tree.
The confirmation identifies `%LOCALAPPDATA%\ODS Desktop Preview` as retained
data, including the 1.28 GB model. Back up conversations before removing that
separate folder after closing ODS. Shared Microsoft WebView2 is retained.

The optional `ODS-Windows-Native-Preview.zip` is a portable build of the same
application: extract all files together and open **ODS Windows Preview.exe**.
Its files can be removed after closing ODS; it has no uninstall registration.

## Conversations, privacy and recovery

The installer includes a checked native CPU engine. The application downloads
the catalog model from Hugging Face/CDNs on the owner's request and verifies
its size and SHA-256 before launch. After the model and Windows prerequisites
are available, inference works offline.
Progressing model downloads have no total-duration limit. A 30-second network
inactivity limit and Cancel bound stalled headers/body reads. A failed or
cancelled replacement preserves the old cache; partial downloads are removed.

Portal uses an in-process WebView2 request adapter at
`https://ods-desktop.local`, with no Portal TCP listener. The model has a
separate authenticated loopback endpoint whose API key remains in native code.
The document cannot navigate away, call external APIs, acquire browser
permissions or use host objects. User-clicked external links may open the
default browser.

State lives under `%LOCALAPPDATA%\ODS Desktop Preview`, separately from the
application and existing ODS stacks:

- `models/`: verified model cache. A model-seeded build uses its checked weights.
- `portal/browser/`: private WebView2 profile holding Portal's conversation library.
- `portal/results/`: owner-private atomic results keyed by chat/request. Reopening
  retrieves a retained result without replaying inference. Interrupted requests
  remain recoverable; deletion markers reject stale writers. Completed document
  results include their immutable Markdown snapshot in the same saved receipt.

Create document enables one bounded local `create_markdown` tool, with the fixed
name `document.md` and at most 64 KiB of strict UTF-8 text. It cannot read host
files, choose host paths, run commands, fetch URLs or edit a workspace. The model
receives the validated tool result before its final reply. Only durable completed
results grant download access: stopped, incomplete or failed turns publish no
file. Download verifies length and SHA-256; it does not verify the model's claims
or the document's factual accuracy. Short generation limits can refuse a longer
document request; ask for a shorter document and try again.

Documents reopen with their saved conversation. Deleting a conversation removes
its document access; an identical document retained by another conversation
remains available. Deletion never removes a copy you saved to another folder.
**Export** keeps the complete conversation as JSON, including larger histories,
and uses its own Windows save dialog.

There is one model request at a time, a 4096-token context and a 512-token
answer limit for Chat. Before inference, the pinned server renders and tokenizes the
prompt. ODS removes whole older turns until the recent context fits, reserving
512 answer tokens; the full saved conversation is unchanged. A current message
that cannot fit is refused with shorten/new-chat guidance before inference.
The native adapter consumes authenticated model SSE internally;
Portal receives the bounded completed response, without incremental frontend
tokens. Stop disconnects active inference and verifies the actual model slot
before acknowledgement. If idle inspection fails, recovery stops only the owned
model. **Restart local AI** keeps saved history. Cancelling model acquisition
does not replace a verified cache. Damaged bundled runtime files refuse launch
and ask for repair rather than deleting or silently replacing them.
Document creation budgets the actual tool schema and tool result in both model
rounds, with up to 1024 tokens for the tool call, a 384-token tool-result reserve
and up to 128 tokens for the final summary. Older complete turns may be removed
from inference context; full saved history stays unchanged.

New saved results have a 4096-receipt/64 MiB admission budget; deletion markers
and partial files do not consume receipt slots. Existing results remain readable,
recoverable and deletable even in older oversized stores. Full storage explains
how to export/delete conversations and refuses the new request before inference.
Up to 4096 durable deletion markers independently protect against stale writers.
Completed results are not silently evicted. Invalid
receipts refuse startup and preserve their bytes. Back up `portal/` before
removing it, because it contains both conversation history and results.

Closing ODS stops its exact child through a retained Windows Job handle, without
killing by process name. The session key is removed on normal shutdown. After
a hard crash a small session directory may remain while the Job stops the model.

## Build from source

Build tools require Windows x64, PowerShell 7 and Node 22. They are not app
runtime requirements. Commands below run from `ods/`:

```powershell
Push-Location extensions/services/dashboard
npm ci --no-audit --no-fund
npm run build:native
Pop-Location
.\desktop\windows\build-native-runtime.ps1 -OutputDirectory C:\temp\ods-engine -ToolsCache C:\temp\ods-build-tools
.\desktop\windows\build.ps1 -OutputDirectory C:\temp\ods-preview `
  -NativeRuntimeDirectory C:\temp\ods-engine `
  -PortalAssets .\extensions\services\dashboard\dist\native `
  -WebViewArchive C:\downloads\microsoft.web.webview2.1.0.4258.31.nupkg
.\desktop\windows\build-installer.ps1 -PackageDirectory C:\temp\ods-preview `
  -OutputDirectory C:\temp\ods-setup -ToolsCache C:\temp\ods-build-tools -Version 0.2.0.0
```

Obtain [WebView2 SDK 1.0.4258.31](https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.4258.31)
from NuGet. The builder checks its exact size/hash. `native-build-pins.json`
pins every build tool and the engine source; downloads fail closed if publisher
bytes change. Tools are extracted into the private cache without global
installation, PATH or registry changes. Failed extractions are retained.

The engine builds llama.cpp b9014 (`d4b0c22f9e67f0295e91dc1ab4f17c0fb2557fa4`)
using LLVM-MinGW UCRT, static linking, baseline x64 CPU, no OpenMP, dynamic
backend loading, embedded WebUI or server TLS. An exact, recorded portability
patch adds the missing `<algorithm>` include in `common/ngram-mod.cpp`.
The PE import gate permits only Windows system DLLs. HTTPS acquisition uses
.NET; the model server remains authenticated loopback-only.

Generation and prompt processing use explicit matching thread budgets, capped
at eight, while retaining b9014's lower-core MinGW default. This prevents the
scalar preview from oversubscribing large Windows hosts. It is a conservative
default, not per-machine tuning or a universal performance guarantee.

The package retains engine, toolchain, frontend and SDK notices. Keep them with
the binaries. `native-runtime.json`, `portal.json` and `bundle.json` record
provenance and runtime asset hashes. `-IncludeModel -ModelPath <GGUF>`
can seed verified weights. Use new output directories to retain prior builds.

For comparison, `build.ps1 -RuntimeArchive <official b9014 ZIP>` retains the
earlier direct-upstream-acquisition profile. That profile needs MSVC and does
not redistribute the archive's runtime binaries. It cannot build this installer.
Omitting `-PortalAssets` builds the standalone WPF fixture/profile.

## Validation and clean Windows handoff

```powershell
.\tests\test-windows-desktop-preview.ps1
.\tests\test-windows-desktop-preview.ps1 -PackageBundle C:\temp\ods-preview
.\tests\test-windows-native-portal.ps1 -Bundle C:\temp\ods-preview
.\tests\test-windows-native-portal.ps1 -Bundle C:\temp\ods-preview -DocumentFixture
.\tests\test-windows-native-installer.ps1 -Package C:\temp\ods-preview -ToolsCache C:\temp\ods-build-tools
.\tests\test-windows-native-portal.ps1 -Bundle C:\temp\ods-preview `
  -LiveState C:\temp\ods-model-qa -Screenshot C:\temp\portal.png
.\tests\test-windows-native-portal.ps1 -Bundle C:\temp\ods-preview `
  -DocumentLive -LiveState C:\temp\ods-document-qa -Screenshot C:\temp\document.png
.\desktop\windows\qa\new-sandbox-kit.ps1 -InstallerDirectory C:\temp\ods-setup -OutputDirectory C:\temp\ods-clean-qa
```

CI executes native regressions, actual EXE, real WebView2 fixture and real
graphical installer lifecycle, verified document delivery and actual save/cancel
dialogs for Markdown and conversation JSON. The optional live probe uses the real catalog
model. The Sandbox kit maps only reviewed input read-only and a dedicated
results directory writable; Internet is enabled for Microsoft/model acquisition.
After moving/downloading a kit, regenerate it so its absolute host mappings are
correct, then open its `.wsb` on a host with Sandbox already enabled. No script
enables Windows features or reboots the host. See [Microsoft's configuration contract](https://learn.microsoft.com/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file).

Guest QA records its OS, actual security token, available developer commands,
installer hash, first-use model/Portal results and screenshot. It refuses to
run outside the expected Sandbox account/VM/mapping. Sandbox execution is a
separate gate; generating the kit is not a passing clean-machine result, and an
elevated guest is not proof of standard-user operation. Full ODS agent/tool
compatibility and independent release acceptance remain separate.
