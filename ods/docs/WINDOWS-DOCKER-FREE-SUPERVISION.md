# Architecture Design Note: Docker-Free Windows Stack Supervision Options

**Target Repository**: Osmantic/ODS (fork branch `experiment/windows-supervisor-clean`)
**Scope**: Supervision, lifecycle management, and failure recovery for a Docker-free, native Windows desktop stack (`llama-server.exe` + `dashboard-api`).
**Status**: Experimental Design Evaluation & Comparative Audit

---

## 1. Context & Architectural Objective

The maintainer's stated objective is running ODS natively on Windows without Docker or WSL2, delivering a consumer-grade, downloadable desktop-app experience for non-technical users.

In a Docker-free Windows stack, the system orchestrates two decoupled processes:
1. `llama-server.exe`: A native C++ inference server managing GPU/CPU compute, loading large GGUF model weights into system memory or VRAM.
2. `dashboard-api`: A Python/FastAPI/Uvicorn HTTP service providing orchestration, status, and client endpoints.

Without Docker container isolation, process supervision must handle:
- **Process Tree Lifetime**: Preventing orphaned background processes holding memory, VRAM, or binding localhost network ports when the supervisor crashes or is terminated.
- **Ordered Lifecycle & Graceful Shutdown**: Allowing HTTP servers to complete active requests and flush state, while prompting inference engines to unload models and release driver handles.
- **Zero Administrative Friction**: Enabling unprivileged installation and operation without recurring UAC elevation prompts.

This document evaluates three supervision options across nine operational dimensions, distinguishing verified repository facts from engineering inferences and hardware-dependent questions.

---

## 2. Nine-Dimension Comparative Evaluation

| Dimension | Option 1: Win32 Job Object Supervisor | Option 2: Host-Agent Scheduled Task | Option 3: Windows Service (NSSM / pywin32) | Evidence Status |
| :--- | :--- | :--- | :--- | :--- |
| **1. User Logout** | Interactive session ends; kernel destroys child processes via `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. Workloads terminate cleanly. | Session ends; Task Scheduler attempts stop, but unmanaged child processes (`Start-Process`) can decouple and leak. | Service runs in Session 0 independent of user login state; inference workloads can continue in background. | *Documented Windows behavior & verified repo code* |
| **2. Sleep and Wake** | Suspends with OS; resumes upon wake. Requires supervisor health checks to detect lost CUDA/DirectML driver contexts. | Continues running if configured with `-AllowStartIfOnBatteries`, but lacks child process re-verification upon wake. | Receives `SERVICE_CONTROL_POWEREVENT` via `HandlerEx` to pause/resume worker loops. | *Documented Windows behavior & hardware-dependent* |
| **3. Privileges and UAC** | **Zero elevation**; standard user token (Medium Integrity Level). No UAC prompt at install or runtime. | **Zero elevation**; standard user token (`-RunLevel Limited`), with fallback to Start Menu Startup folder. | **Requires Administrator / UAC prompt** to install, configure, start, stop, or remove via SCM. | *Verified repo code & Windows SCM contract* |
| **4. GPU Compatibility** | Direct access to DirectX12, DirectML, Vulkan, and CUDA in interactive user desktop session (Session 1+). | Direct access in interactive user desktop session (Session 1+). | **High Risk**: Session 0 isolation restricts desktop display driver GPU contexts on consumer WDDM drivers without TCC mode. | *Documented WDDM architecture & engineering inference* |
| **5. Crash Handling** | Supervises child PIDs; detects child death via `poll()` within 400ms and exits code 6. Kernel cleans descendants on supervisor death. | Parent monitoring via CIM queries; crashes leave orphaned child processes unless manually reconciled. | SCM automatically restarts crashed services based on configured recovery actions (`failure` actions). | *Verified CI behavior & Windows SCM contract* |
| **6. Upgrade & Rollback** | Terminating supervisor frees file locks immediately; replaces binaries in user app directory; no reboot needed. | Complex: must unregister task, query CIM `Win32_Process` by regex, terminate handles, overwrite, and re-register. | High friction: must stop service, await `STOPPED` state; file locks risk service name marked for deletion until reboot. | *Verified repo code & Windows SCM contract* |
| **7. CI Test Cost** | **Lowest**: Fast offline stub suite runs in 5.3s on `windows-latest` without network or elevation. | Medium: Requires testing PowerShell task registration, CIM queries, and Windows event loops in CI. | High / Brittle: Requires elevated runner permissions, SCM manipulation, third-party wrappers, and slow teardowns. | *Verified CI behavior (Run 39748850531)* |
| **8. Existing Implementation** | Experimental opt-in prototype in `ods/experimental/windows-supervisor/`. | Shipped in production: `installers/windows/ods.ps1`, `host-agent-process.ps1`, `ods-host-agent.py`. | None in codebase; explicitly avoided by maintainers. | *Verified repository codebase* |
| **9. Operational Ownership** | Python supervisor owns child lifecycles, health checks, signal dispatch, and ordered shutdown. | Scheduled Task starts wrapper script; PowerShell attempts process management; `ods-host-agent.py` manages extensions. | Windows SCM owns service process; wrapper (NSSM) forwards signals to worker processes. | *Verified repository code & Windows SCM contract* |

---

## 3. In-Depth Technical Analysis

### Option 1: Win32 Job Object Supervisor
*Implementation Reference: [`ods/experimental/windows-supervisor/supervisor.py`](../experimental/windows-supervisor/supervisor.py)*

- **Mechanism & Process Lifetime** `[Verified repository behavior]`:
  The supervisor utilizes Win32 kernel Job Objects (`CreateJobObjectW`, `SetInformationJobObject`) via Python `ctypes`.
  By assigning child process handles to a Job Object configured with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (`0x2000`), the Windows kernel guarantees that all processes associated with the job terminate when the last supervisor handle closes.
- **Graceful Shutdown & Signal Handling** `[Verified CI behavior]`:
  Child processes are created in separate process groups (`CREATE_NEW_PROCESS_GROUP`).
  On stop, the supervisor dispatches `CTRL_BREAK_EVENT` into child process groups:
  1. `dashboard-api` (Uvicorn): Intercepts `SIGBREAK`, completes in-flight HTTP requests, executes ASGI lifespan shutdown handlers, and exits with code 0 (enabled by a pre-registered signal handler).
  2. `llama-server.exe`: Responds to `CTRL_BREAK_EVENT` via standard Win32 console handler, terminating with NTSTATUS `STATUS_CONTROL_C_EXIT` (`0xC000013A` / `3221225786`), which the supervisor recognizes as a clean signal termination.
  3. If processes do not exit before `--stop-timeout`, the supervisor terminates them forcefully and closes the Job Object.
- **Privilege & Deployment Model** `[Documented Windows behavior]`:
  Job Objects require no elevated administrative privileges and operate inside standard user tokens (Medium Integrity Level). Binaries reside in `%LOCALAPPDATA%`, avoiding UAC prompts during installation and updates.
- **Limitations & Known Risks** `[Engineering inference]`:
  Workloads cannot continue running in the background after the user logs out. Modern Standby (sleep) can reset physical GPU contexts, requiring supervisor health monitoring to restart failed inference workers.

---

### Option 2: Host-Agent Scheduled-Task Approach
*Implementation References in Existing Codebase:*
- [`ods/installers/windows/ods.ps1:2921-2967`](../installers/windows/ods.ps1): `Register-ScheduledTask`, `New-ScheduledTaskTrigger -AtLogOn`, and Start Menu Startup folder fallback.
- [`ods/installers/windows/lib/host-agent-process.ps1:3-41`](../installers/windows/lib/host-agent-process.ps1): `Stop-ODSHostAgentProcess` CIM `Win32_Process` query and regex command-line validation.
- [`ods/installers/windows/lib/native-llama-runtime.ps1:1197-1209, 1642-1668`](../installers/windows/lib/native-llama-runtime.ps1): `Stop-ODSPortalOwnedProcesses` with `$process.Kill()` and `WaitForExit(60000)`.
- [`ods/bin/ods-host-agent.py:21942-21943`](../bin/ods-host-agent.py): `signal.signal(signal.SIGTERM)` and `signal.signal(signal.SIGINT)` registration.

- **Mechanism & Process Lifetime** `[Verified repository behavior]`:
  `ods.ps1:2921-2936` registers a Windows Scheduled Task named `ODS Host Agent` using trigger `-AtLogOn` and principal `New-ODSInteractiveScheduledTaskPrincipal -RunLevel Limited`. If registration fails (e.g. corporate group policy restriction), lines 2949-2960 fall back to writing a VBScript launcher (`ods-host-agent.vbs`) into the user's Start Menu Startup folder (`shell:startup`).
- **Orphan & Teardown Challenges** `[Verified repository behavior & code comments]`:
  PowerShell launches Python and native inference processes via `Start-Process` without Job Object boundaries. As documented by the maintainer in `native-llama-runtime.ps1:1653-1654`:
  > *"Task Scheduler may have exited this held process after HasExited but before Kill. Accept that race only if the same handle proves exit."*
  To terminate running agents, `host-agent-process.ps1:16-35` queries CIM `Win32_Process`, parses the command line with regular expressions to confirm script ownership, and executes `Stop-Process -Force`. If a child PID is recycled or missed by the query, background processes remain active and lock ports.
- **Privilege & Upgrade Complexity** `[Verified repository behavior]`:
  While running unprivileged (`-RunLevel Limited`), upgrades require orchestrating task deregistration, CIM process termination, file replacement, and task re-registration.

---

### Option 3: Windows Service (via NSSM or pywin32)
*Implementation Reference: Evaluated as architectural alternative (not present in ODS codebase).*

- **Mechanism & Process Lifetime** `[Documented Windows behavior]`:
  Registers an NT Service managed by the Windows Service Control Manager (SCM). The service executes in **Session 0** under `NT AUTHORITY\SYSTEM` or a dedicated service account, allowing workloads to persist across user logouts.
- **GPU Compatibility & Session 0 Isolation Risks** `[Engineering inference & driver documentation]`:
  - Windows strictly isolates services into Session 0 to prevent interactive window exploits.
  - While compute APIs (CUDA, DirectML, Vulkan) are not universally disabled in Session 0, consumer NVIDIA GeForce and AMD Radeon drivers operating under Windows Display Driver Model (WDDM) require an active desktop session to enumerate display adapters and allocate device contexts.
  - Enterprise GPUs supporting TCC (Tesla Compute Cluster) mode operate cleanly in Session 0, but consumer desktop GPUs (RTX 3060, 4070, Radeon RX) do not support TCC and frequently fail with `cudaErrorNoDevice` or `DXGI_ERROR_NOT_CURRENTLY_AVAILABLE` when executed from Session 0.
- **Privilege & Administrative Friction** `[Documented Windows behavior]`:
  Installing, configuring, or removing a Windows Service requires mandatory Administrator elevation (`sc.exe create` or NSSM). Standard desktop users cannot install the application without UAC approval, conflicting with the goal of a friction-free consumer desktop experience.
- **Upgrade Fragility** `[Documented Windows behavior]`:
  If a service fails to stop cleanly during an upgrade, the SCM marks the service as "marked for deletion", locking the service name and associated files until a full operating system reboot.

---

## 4. What CANNOT Be Known Without Physical Windows Hardware

Automated CI on GitHub Actions `windows-latest` executes inside virtualized Azure hypervisors without physical consumer GPU hardware. The following critical behaviors **cannot be established in CI** and require validation on real Windows 11 hardware:

1. **GPU Context Recovery Across Modern Standby (S0ix / S3 Sleep)** `[Hardware-dependent question]`:
   - On physical laptops with NVIDIA Optimus or AMD Advantage hybrid graphics, Modern Standby depowers PCIe lanes.
   - Does `llama-server.exe` retain its VRAM allocations upon resume, or does CUDA throw `cudaErrorDevicesUnavailable` (CUDA error 46), requiring the supervisor to detect wake events and restart the worker?
2. **Windows Defender / SmartScreen File Locking Latency** `[Hardware-dependent question]`:
   - Real-time antivirus scanning on consumer Windows 11 inspects newly extracted `.exe` files and downloaded `.gguf` models.
   - Defender frequently holds exclusive read locks (`ERROR_SHARING_VIOLATION`, Win32 error 32) for 500ms–2000ms after extraction, causing immediate `subprocess.Popen` calls to fail unless retry backoff is implemented.
3. **EcoQoS & Efficiency Core Power Throttling** `[Hardware-dependent question]`:
   - Windows 11 automatically assigns background processes without a visible window to **EcoQoS**, throttling CPU threads to efficiency cores.
   - Physical hardware testing is required to measure whether `SetProcessInformation(ProcessPowerThrottling)` is necessary to prevent severe token generation latency degradation.
4. **Multi-User Fast User Switching & VRAM Contention** `[Hardware-dependent question]`:
   - When User A locks their session and User B logs in, how does the GPU driver arbitrate VRAM between multiple user sessions?

---

## 5. Architectural Recommendations & Decision Points

1. **Adopt the Win32 Job Object Supervisor for Process Lifetime Management**:
   - Replaces fragile CIM/WMI process-scraping heuristics with kernel-guaranteed cleanup (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`).
   - Requires zero administrative elevation, matching consumer desktop distribution goals.
2. **Retain Scheduled Tasks Strictly as a Session Auto-Start Trigger**:
   - Use the Scheduled Task (`ods.ps1:2921`) or Start Menu Startup folder solely to launch the supervisor upon user login, delegating all child process orchestration to the Job Object.
3. **Reject Windows Services for Consumer Distribution**:
   - Session 0 driver limitations on consumer GPUs and mandatory UAC elevation make NT Services unsuitable for consumer desktop LLM workloads.
4. **Maintainer Review Decision Record**:
   - For the complete evaluation, staged adoption gates, and required maintainer decisions, see [`ods/docs/WINDOWS-SUPERVISOR-REVIEW.md`](WINDOWS-SUPERVISOR-REVIEW.md).
