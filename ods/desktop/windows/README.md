# ODS Windows native Portal preview

Open a Windows EXE, start local AI, and use the existing ODS Portal conversation
interface with a native llama.cpp CPU backend. The preview reuses Portal's
composer, message rendering, conversation library, saved history and Stop flow.
Docker, WSL, Python, Node, Git and an administrator account are not required
to run it. WebView2 renders the interface inside the desktop window.

**Supported flow:** setup → Portal → local text response → another turn → Stop
→ continue → close/reopen a saved conversation. This preview does not run the
OpenClaw/Pixel agent, host tools, voice, images, extensions or GPU routing.
Those controls are hidden; the native API refuses unsupported capabilities.
It does not replace `install.ps1` or modify an existing ODS installation.

See [validation and an actual Portal screenshot](VALIDATION.md) for executed
checks, the precise integration boundary and remaining release work.

## Open the preview

Download **ods-windows-native-preview-<run id>** from the Artifacts section of
the PR's **Lint PowerShell** workflow run (GitHub sign-in required). Extract the
artifact, then extract `ODS-Windows-Native-Preview.zip`. `SHA256SUMS.txt` checks
the ZIP; `BUILD-INFO.json` identifies the tested checkout, PR head and EXE hash.
This is an unsigned review build; website delivery and an installer remain work.

1. Extract the complete ZIP into a normal folder. Keep its files together.
2. Double-click **ODS Windows Preview.exe**.
3. Choose **Download & start (1.30 GB)**. After verification and model startup,
   the setup window opens Portal. Type a message and press Enter.
4. Use **New chat** or select an earlier conversation in the sidebar. **Stop**
   ends the current inference; after acknowledgement the conversation can continue.

A model-seeded bundle or existing cache shows **Start local AI**; it may still
need the runtime download. Required components are Windows 10/11 x64, .NET
Framework 4.8, Microsoft Visual C++ v14 x64 runtime (14.44 or later), and Microsoft
WebView2 Runtime. Setup checks prerequisites before large downloads and offers
the corresponding official Microsoft download page. Installing prerequisites
may require administrator permission; running ODS does not. Allow about 2 GB of
disk space and at least 4 GB of available RAM. These are preview estimates, not
hardware or performance qualification. Clean-machine and accessibility review,
signing, SmartScreen reputation, updates and website installation remain open.

## Conversations, privacy and recovery

First setup acquires the checked runtime directly from ggml-org on GitHub and
the catalog model from Hugging Face, including their CDNs. Runtime and weights
are SHA-256 verified before launch. Subsequent starts work offline after these
files and the Windows prerequisites are available.

Portal assets are served through an in-process WebView2 request adapter at
`https://ods-desktop.local`; it does not expose a Portal TCP server. The model
uses a separate authenticated loopback endpoint. Its API key stays in native
code. The document cannot navigate away, call external APIs, use host objects
or acquire browser permissions. User-clicked external links may open the
default browser.

State is separate from existing installations, under
`%LOCALAPPDATA%\ODS Desktop Preview`:

- `models/` and `native/`: checked acquisition caches. A seeded bundle uses its
  checked model directly.
- `portal/browser/`: private WebView2 profile containing the existing Portal
  local conversation library. Conversations persist across app restarts.
- `portal/results/`: owner-private, atomic result receipts keyed by chat/request.
  Reopening retrieves a retained result without repeating inference. An unfinished
  request becomes interrupted after a restart. A deletion marker rejects stale
  writers without deleting another conversation's results.

There is one model request at a time, a 4096-token context and a 512-token answer
limit. The native model adapter streams internally so Stop can disconnect an
active inference, then inspects its actual slot before acknowledgement. The
Portal receives the bounded completed response; this preview does not deliver
incremental model tokens. If idle inspection fails, recovery stops only the
owned model. Use **Restart local AI** to start it again with saved history kept.
**Cancel** during setup aborts acquisition without replacing a verified cache.

Saved-result storage is limited to 256 files in this preview; completed results
are not silently evicted. Export important conversations from their sidebar
menus before manually clearing this separate preview state. Unsupported or
damaged receipts refuse startup and preserve their bytes for recovery. Clearing
`portal/` removes both saved conversations and receipts; back it up first.

Closing the app stops its child through a retained Windows Job handle. It never
kills processes by name. The owner-private session key is removed on normal
shutdown; after a hard crash a small session directory may remain, while the Job
terminates the model. Uninstall by closing the app and deleting its extracted
folder and, optionally, its separate state directory. No service, scheduled task
or registry entry is installed.

## Build from source

Use Windows' .NET Framework C# compiler, Node 22 for building the frontend, the
official [llama.cpp b9014 CPU x64 archive](https://github.com/ggml-org/llama.cpp/releases/download/b9014/llama-b9014-bin-win-cpu-x64.zip),
and [Microsoft.Web.WebView2 1.0.4258.31](https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.4258.31).
The scripts verify exact archive sizes/SHA-256 values. Model pins come from
`config/model-library.json`. Commands below run from `ods/`:

```powershell
Push-Location extensions/services/dashboard
npm ci --no-audit --no-fund
npm run build:native
Pop-Location
.\desktop\windows\build.ps1 -OutputDirectory C:\temp\ods-preview `
  -RuntimeArchive C:\downloads\llama-b9014-bin-win-cpu-x64.zip `
  -PortalAssets .\extensions\services\dashboard\dist\native `
  -WebViewArchive C:\downloads\microsoft.web.webview2.1.0.4258.31.nupkg
```

The complete output contains the ODS EXE, `OdsPortal.dll`, three WebView2 SDK
DLLs, checked frontend assets, manifest and licences. `portal.json` pins these
files; `bundle.json` pins that manifest. **No llama runtime executable or DLL is
redistributed.** The installed WebView2 Runtime is a separate prerequisite.
Dependency notices are exported from the SDK, frontend lockfile and checked
llama executable. Keep all notices in the ZIP. `-IncludeModel -ModelPath <GGUF>`
can seed the verified model. Always use a new output directory.

Omitting `-PortalAssets` builds the earlier standalone WPF chat fixture/profile;
the CI review artifact includes Portal. No frontend dependency or runtime is
needed for the default download-free native regression suite.

```powershell
.\tests\test-windows-desktop-preview.ps1
.\tests\test-windows-desktop-preview.ps1 -PackageBundle C:\temp\ods-preview
.\tests\test-windows-native-portal.ps1 -Bundle C:\temp\ods-preview
# Optional real model: setup hands off to Portal, then exercises send/Stop/close.
.\tests\test-windows-native-portal.ps1 -Bundle C:\temp\ods-preview `
  -LiveState C:\temp\ods-preview-qa -Screenshot C:\temp\portal.png
```

CI executes the native process/adapter regressions, actual EXE entrypoint checks
and production Portal UI inside real WebView2 with a deterministic model fixture.
The optional live probe uses the exact official CPU runtime and catalog model.
Neither establishes full agent/tool compatibility or release acceptance. The
underlying contracts are [llama.cpp b9014 server documentation](https://github.com/ggml-org/llama.cpp/blob/b9014/tools/server/README.md)
and the existing Portal request/history/result/cancellation consumers.

## Runtime distribution gate

The official b9014 Windows archive contains Microsoft's
`libomp140.x86_64.dll`. [Microsoft's OpenMP documentation](https://learn.microsoft.com/en-us/cpp/build/reference/openmp-enable-openmp-2-0-support?view=msvc-170)
says the required LLVM OpenMP DLLs are not redistributable. This package excludes
**all llama runtime binaries**; the owner acquires the checked official archive
directly. This does not establish the licensing status of that upstream archive.
Before an all-in-one offline installer or runtime mirror, qualify a
redistributable native runtime, retain complete notices, and rerun inference and
performance checks. Copying a generic LLVM licence or swapping an ABI dependency
does not establish that qualification.
