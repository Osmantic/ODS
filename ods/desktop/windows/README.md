# ODS Windows native chat preview

This opt-in prototype runs a native WPF desktop window and a native
llama.cpp CPU process on Windows x64. It does not use Docker, WSL, a browser,
Python, Node, Git or an administrator account at runtime.

**Scope:** local text chat with the catalog's Qwen 3.5 2B Q4 model. It is not
the complete ODS product: Portal/Pixel, tools, voice, extensions, GPU routing,
durable chat history, automatic updates and an installer are not included.
It does not replace `install.ps1` or change an existing ODS installation.

See [validation, actual screenshot and the next integration boundary](VALIDATION.md)
for the exact source/environment, executed checks and remaining product work.

## Open the preview

Reviewers can download **ods-windows-native-preview-<run id>** from the
Artifacts section of the PR's **Lint PowerShell** workflow run (GitHub sign-in
is required). Extract that artifact, then extract its
`ODS-Windows-Native-Preview.zip`. Compare the ZIP with `SHA256SUMS.txt` when
checking a transferred copy; `BUILD-INFO.json` records the tested checkout,
PR head and executable hash. This unsigned review build is not a general
release or a website installer.

1. Extract the complete preview ZIP to a normal folder.
2. Double-click **ODS Windows Preview.exe**.
3. Choose **Download & start (1.30 GB)**. After the checked runtime and model
   downloads, type a message and press Enter. A model-seeded bundle or an existing
   model cache shows **Start local AI**; it may still need the runtime download.

Windows 10/11 x64 with .NET Framework 4.8 and the Microsoft Visual C++ v14
x64 runtime (14.44 or later) is required. If it is missing, the app explains the
step before downloading AI files and offers the official Microsoft download
instructions. Installing that Windows component may require administrator
permission; using this preview does not. Allow about 2 GB of
disk space for the online package/model and at least 4 GB of available RAM
for inference. This is a conservative CPU proof, not a hardware qualification
or a performance promise. The ZIP is unsigned; signing, SmartScreen reputation,
website delivery and clean-machine release qualification remain release work.

First setup downloads the pinned runtime directly from ggml-org on GitHub and
the model from Hugging Face (including their download CDNs).
Chat requests go only to the child server's authenticated `127.0.0.1` endpoint.
The model is cached in `%LOCALAPPDATA%\ODS Desktop Preview\models`. A bundle
containing `models/` uses those checked weights without copying them into that
cache. Once runtime and model have been acquired, subsequent starts work offline.
Models and runtime files are SHA-256 checked before launch. Keep the complete
package together; it verifies its runtime against `bundle.json`.

**Cancel** stops setup or the current answer. Cancelling an answer stops the
owned model and clears that conversation; your unsent message is kept. Click
**Start local AI** to continue. Failed downloads never replace a verified cache.
Use **New chat** to clear context. Chats live only in memory. The preview limits
a conversation to eight turns and a response to 512 tokens; a truncated answer
is labelled. Context overflow is reported with a shorter-message/new-chat step.

Closing the app stops its child using a retained Windows Job handle. It does not
look up or kill other ODS/llama processes. A per-session API key inherits an
owner-only directory ACL and is removed on normal shutdown. A hard crash still
terminates the child; its small session directory can remain. To remove the
preview, close it, delete the extracted folder and, if desired, its separate
`%LOCALAPPDATA%\ODS Desktop Preview` cache. No service, task or registry entry
is installed.

## Build from source

The build uses Windows' .NET Framework C# compiler, with no SDK or NuGet step.
Download the official [llama.cpp b9014 CPU x64 archive](https://github.com/ggml-org/llama.cpp/releases/download/b9014/llama-b9014-bin-win-cpu-x64.zip).
The build refuses any different size or SHA-256. It reads the archive to derive
the exact file manifest, exports its embedded dependency notices and removes its
temporary extraction. **No upstream executable or DLL is included in the output
package.** Model URL, revision, size and
checksum are taken from `config/model-library.json`, not a second catalog.

```powershell
.\desktop\windows\build.ps1 -OutputDirectory C:\temp\ods-preview `
  -RuntimeArchive C:\downloads\llama-b9014-bin-win-cpu-x64.zip
```

For a model-seeded bundle, also supply `-IncludeModel -ModelPath <catalog GGUF>`.
The model is verified before copying. Use a new output directory for each build.
ZIP the complete output directory, retaining all licence files.

```powershell
.\tests\test-windows-desktop-preview.ps1
# Launch the actual built EXE: first open, duplicate, damaged package and reopen.
.\tests\test-windows-desktop-preview.ps1 -PackageBundle C:\temp\ods-preview
# Optional real llama.cpp / catalog-model probe (no fake server):
.\tests\test-windows-desktop-preview.ps1 -LiveBundle C:\temp\ods-preview `
  -ModelPath C:\models\Qwen3.5-2B-Q4_K_M.gguf
# Actual WPF first-run downloads and chat, using a separate QA state folder:
.\tests\test-windows-desktop-preview.ps1 -UiBundle C:\temp\ods-preview `
  -UiState C:\temp\ods-preview-qa -Screenshot C:\temp\ods-preview.png
```

The default suite runs on Windows CI without downloads. It launches real Windows
fixture processes and tests checked downloads, cancellation, auth/model identity,
UTF-8 answers, empty answers, cache recovery, WPF button/input recovery, normal
shutdown and parent-crash cleanup while an unrelated listener remains alive.
It also opens the real EXE through `Main()` from a path with spaces and Unicode,
checks duplicate-instance and incomplete-package dialogs, closes each owned
process normally and reopens after recovery, without starting inference. Close
an existing preview before running these entrypoint tests. A separate Windows
CI step downloads the pinned 16 MB runtime archive for build metadata/notices,
builds the complete package, repeats the entrypoint checks on that EXE and
uploads the review ZIP. It does not download model weights or bundle the runtime.
The optional live probes test the checked CPU binary, catalog model and actual
WPF first-run/chat path. Neither substitutes for
independent human UX review, a clean Windows machine, the ODS Portal/model harness
or release-fleet acceptance of the full product.

The upstream API contract is [llama.cpp b9014 server documentation](https://github.com/ggml-org/llama.cpp/blob/b9014/tools/server/README.md).
The compact model's existing catalog qualification limits continue to apply.

## Runtime distribution gate

The official b9014 Windows archive contains Microsoft's
`libomp140.x86_64.dll`. [Microsoft's current OpenMP documentation](https://learn.microsoft.com/en-us/cpp/build/reference/openmp-enable-openmp-2-0-support?view=msvc-170)
says the required LLVM OpenMP DLLs are not redistributable. This preview's package
therefore excludes **all** upstream runtime binaries; the owner acquires the
exact checked official archive directly. This does not establish the licensing
status of that upstream archive. Before an ODS all-in-one offline installer or
runtime mirror, qualify a redistributable native runtime (for example a pinned
source build with `GGML_OPENMP=OFF`), retain its complete notices and rerun native
inference/performance tests. Copying a generic LLVM licence or swapping an ABI
dependency is not a validated substitute.
