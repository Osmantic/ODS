# Windows-native ODS plan

Status: in progress. Milestone 1 is being built on `feat/windows-native`.

## Goal

ODS installs and runs on Windows like any other desktop application:

- downloaded from a normal website as one installer;
- no Docker, no WSL, no administrator rights, no command line;
- a Start menu entry opens a working local chat and the ODS dashboard;
- uninstall removes everything it added.

The audience is people with no GitHub or CLI experience.

## Where Windows stands today

`.\install.ps1` prepares WSL2, Ubuntu and Docker Desktop, then runs the Linux
installer inside Ubuntu ([Windows quickstart](WINDOWS-QUICKSTART.md),
[support matrix](SUPPORT-MATRIX.md)). Some pieces already run natively and are
reused:

| Piece | Native today | Where |
|---|---|---|
| llama.cpp `llama-server.exe` | AMD GPUs (Vulkan), NVIDIA on Windows on Arm (CUDA) | `installers/windows/lib/native-llama-runtime.ps1`, `config/backends/*.json` |
| Pinned, hash-checked downloads | yes, per-file hashes re-checked at launch | same |
| Host agent (Python) | yes | `installers/windows/lib/host-agent-process.ps1` |
| Owner-only secret files | yes | `installers/windows/lib/private-file.ps1` |
| Sign-in start (Task Scheduler, Startup fallback) | yes | `installers/windows/ods.ps1`, `lib/opencode-startup.ps1` |

No platform runs the dashboard natively yet; macOS runs only llama-server
natively.

## Architecture

```
Start menu "ODS"  ──>  supervisor (bundled Python, at sign-in)
                         ├─ llama-server.exe   127.0.0.1  pinned llama.cpp build, built-in chat UI
                         └─ dashboard-api      127.0.0.1  FastAPI, serves the dashboard and replaces nginx's gate
```

- **Supervisor.** One stdlib Python process starts the services, keeps them in
  a Windows Job Object so none outlives it, restarts a crashed service a
  bounded number of times, then exits so Task Scheduler restarts it.
- **Model runtime.** The existing pinned llama.cpp downloader, extended with a
  CPU build (x64, Arm64) and Vulkan for NVIDIA/Intel GPUs. The API key stays in
  an owner-only file passed with `--api-key-file`.
- **Chat.** llama-server's built-in web UI for milestone 1. It adds no Python
  dependencies and no download.
- **Dashboard.** dashboard-api runs as a plain process in a native mode, serves
  the built dashboard, and reproduces the nginx gate: no sign-in only for a
  loopback request with a loopback Host and no forwarding headers; the API key
  is added server-side and never reaches the browser.
- **Python.** One python.org embeddable runtime per release, with locked wheels
  installed at build time (`--require-hashes`). Users never install Python.
- **Installer.** Inno Setup, per-user (`%LOCALAPPDATA%\Programs\ODS`), Start
  menu entry, uninstall entry, silent install for CI. State lives in
  `%LOCALAPPDATA%\ODS`.

## Milestones

| # | Scope | Proven by |
|---|---|---|
| 1 | Supervisor, CPU/Vulkan llama-server with built-in chat, native dashboard-api serving the dashboard, Inno Setup installer | `windows-native` workflow on `windows-latest`: build the installer, silent install, start, chat with a small model, kill and restart a service, check loopback-only listeners and secret ACLs, silent uninstall, nothing left behind |
| 2 | Model choice and switching from the dashboard; NVIDIA CUDA build; first-run model download with progress | Windows CI plus a real NVIDIA and AMD laptop |
| 3 | ODS Talk with native Hermes; voice (faster-whisper, kokoro-onnx) | Windows CI plus a real laptop |
| 4 | Code signing, auto-update check, website download page | Signed release on a clean Windows install |

## Out of scope for milestone 1

Docker-only features stay off in native mode: extensions installs, Portal
(Pixel needs a Linux sandbox), Open WebUI, Tailscale and Wi-Fi setup.

## Risks

- **Unsigned binaries.** llama.cpp release builds are unsigned, and Smart App
  Control can block them. The runtime already detects that and reports it.
- **SmartScreen.** Until releases are code-signed, Windows warns on download.
- **No GPU in CI.** Windows CI proves the CPU route only. GPU routes need real
  hardware.

## Lessons from the removed Tauri installer

It was never built by a workflow GitHub ran, never shipped, and added a second
dependency ecosystem (PR #7359). The native installer is built and tested by a
root workflow from its first commit, calls the real install logic, and adds no
new language toolchain.
