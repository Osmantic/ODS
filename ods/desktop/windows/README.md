# ODS Windows native Portal preview

Install a Windows application, open ODS Portal, and talk to a local CPU model.
The preview reuses Portal's composer, message rendering, conversation library,
saved history and Stop flow inside a native WPF/WebView2 window. Running it
requires no Docker, WSL, Git, Node, Python, terminal or administrator account.

**Supported flow:** install → launch → download the checked model → Portal →
local text answer → another turn → Stop → continue → reopen saved history.
OpenClaw/Pixel agents, host tools, workspace artifacts, voice, images,
extensions and GPU routing remain outside this preview. Their controls are
hidden and the native API refuses unsupported operations. Existing ODS
installations and the supported `install.ps1` path are separate.

See [validation](VALIDATION.md) for executed checks and release gates.

## Install and use

Download **ods-windows-native-preview-<run id>** from the PR's **Lint
PowerShell** workflow Artifacts section (GitHub sign-in required). Check
`SHA256SUMS.txt` against the downloaded files. `INSTALLER-BUILD-INFO.json`
identifies installer/application/runtime hashes; the portable ZIP's
`BUILD-INFO.json` records the checkout and PR head separately.

1. Double-click **ODS-Windows-Preview-Setup.exe** in a normal Windows session.
2. Follow the wizard. Its default destination is
   `%LOCALAPPDATA%\Programs\ODS Windows Preview`. It creates a Start Menu entry
   and a current-user Apps & features uninstall entry.
3. If Microsoft WebView2 Runtime 155 or newer is missing, Setup asks before
   running the pinned, Microsoft-signed Evergreen bootstrapper. An Internet
   connection is needed. Cancelling or failed prerequisite setup stops ODS setup.
4. Choose **Open ODS Windows Preview** on Finish, then **Download & start
   (1.28 GB)**. Wait for model verification and startup, type a message in Portal
   and press Enter. Later launches use **Start local AI** when the model is cached.
5. Use **New chat**, select a saved conversation, or press **Stop** during a
   response. Once Stop is acknowledged, another turn can continue.

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

## Repair, upgrades and removal

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

The optional `ODS-Windows-Native-Preview.zip` is a portable build of the same
application: extract all files together and open **ODS Windows Preview.exe**.
Its files can be removed after closing ODS; it has no uninstall registration.

## Conversations, privacy and recovery

The installer includes a checked native CPU engine. The application downloads
the catalog model from Hugging Face/CDNs on the owner's request and verifies
its size and SHA-256 before launch. After the model and Windows prerequisites
are available, inference works offline.

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
  remain recoverable; deletion markers reject stale writers.

There is one model request at a time, a 4096-token context and a 512-token
answer limit. The native adapter consumes authenticated model SSE internally;
Portal receives the bounded completed response, without incremental frontend
tokens. Stop disconnects active inference and verifies the actual model slot
before acknowledgement. If idle inspection fails, recovery stops only the owned
model. **Restart local AI** keeps saved history. Cancelling model acquisition
does not replace a verified cache. Damaged bundled runtime files refuse launch
and ask for repair rather than deleting or silently replacing them.

Result storage is limited to 256 files; completed results are not silently
evicted. Export important conversations before manually clearing state. Invalid
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
  -OutputDirectory C:\temp\ods-setup -ToolsCache C:\temp\ods-build-tools -Version 0.1.0.0
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
.\tests\test-windows-native-installer.ps1 -Package C:\temp\ods-preview -ToolsCache C:\temp\ods-build-tools
.\tests\test-windows-native-portal.ps1 -Bundle C:\temp\ods-preview `
  -LiveState C:\temp\ods-model-qa -Screenshot C:\temp\portal.png
.\desktop\windows\qa\new-sandbox-kit.ps1 -InstallerDirectory C:\temp\ods-setup -OutputDirectory C:\temp\ods-clean-qa
```

CI executes native regressions, actual EXE, real WebView2 fixture and real
graphical installer lifecycle. The optional live probe uses the real catalog
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
