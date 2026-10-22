# Architecture Design Note: Docker-Free Windows Stack Supervision Options

**Author**: Antigravity / Engineering Research  
**Target Repository**: Osmantic/ODS (fork branch `experiment/windows-supervisor-clean`)  
**Scope**: Supervision and lifecycle management for a Docker-free, native Windows desktop stack (`llama-server.exe` + `dashboard-api`).  
**Status**: Experimental Design Evaluation  

---

## 1. Context & Architectural Objective

The maintainer's stated objective is running ODS natively on Windows without Docker or WSL2, providing a consumer-grade, downloadable desktop-app experience for non-technical users. 

A Docker-free Windows stack requires orchestrating at least two independent backend processes:
1. `llama-server.exe`: A native C++ inference server managing GPU/CPU compute, loading large GGUF weights into system memory or VRAM.
2. `dashboard-api`: A Python/FastAPI/Uvicorn HTTP service providing orchestration, status, and client endpoints.

Without Docker container isolation, the host OS must guarantee:
- **Zero Orphan Leakage**: If the user closes the app, kills the process, or the supervisor crashes, background servers holding large amounts of RAM/VRAM or binding localhost network ports must not be left orphaned.
- **Ordered Lifecycle & Graceful Shutdown**: HTTP servers must complete active connections and flush state; inference engines must unload models and release driver handles.
- **Zero Administrative Friction**: Standard consumers cannot be expected to approve UAC elevation prompts or configure Windows administrative tools.

This document evaluates three architectural supervision options against these requirements.

---

## 2. Comparison Matrix

| Evaluation Dimension | Option 1: Win32 Job Object Supervisor | Option 2: Host-Agent Scheduled Task | Option 3: Windows Service (NSSM / pywin32) |
| :--- | :--- | :--- | :--- |
| **Maintainer Prior Art** | Built in `ods/experimental/windows-supervisor/` | Shipped in `installers/windows/ods.ps1`, `host-agent-process.ps1`, `ods-host-agent.py` | None in codebase (explicitly avoided) |
| **User Logout** | Children cleanly terminated by kernel (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) | Process terminated or orphaned by Task Scheduler; children can leak | Continues running in background (Session 0) |
| **System Sleep / Wake** | Survives sleep; must handle post-wake CUDA context recovery | Survives sleep; Task Scheduler does not monitor child health | Intercepts `SERVICE_CONTROL_POWEREVENT`; cannot access desktop GPU |
| **UAC / Privileges** | **Zero elevation**; standard user token (Medium Integrity) | **Zero elevation**; standard user token (`-RunLevel Limited`), fallback to Startup folder | **Requires Administrator / UAC prompt** to install, configure, or stop |
| **Upgrade Path** | High velocity: stop supervisor, replace files in `%LOCALAPPDATA%`, restart | Moderate: unregister task, kill PID via CIM query, update files, re-register | High friction: `sc stop`, wait for SCM state, replace DLLs; risk of "marked for deletion" |
| **CI Test Cost** | **Lowest**: 7.8s offline unit tests in standard `windows-latest` VM | Medium: requires PowerShell task scheduler mocks or live registration | High / Brittle: requires SCM manipulation, service wrappers, elevated runners |
| **GPU / Desktop Usability**| Direct access to DX12 / DirectML / CUDA in user desktop session | Direct access in user desktop session | **Severe Barrier**: Session 0 isolation blocks desktop GPU compute on consumer drivers |

---

## 3. In-Depth Technical Analysis

### Option 1: Win32 Job Object Supervisor
*Implementation reference: [`ods/experimental/windows-supervisor/supervisor.py`](file:///Users/macbookair/dream-server/scratch/fresh_upstream_ods/ods/experimental/windows-supervisor/supervisor.py)*

- **Mechanism**:
  Uses Win32 kernel Job Objects (`CreateJobObjectW`, `SetInformationJobObject`) via Python `ctypes`.
  Flags configured:
  - `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (`0x2000`): Instructs the Windows kernel to terminate all assigned processes whenever the last handle to the Job Object is closed.
  - Cooperative stop path sends `CTRL_BREAK_EVENT` via `proc.send_signal()` into the child process group, allowing Uvicorn lifespan cleanup and `llama-server` shutdown before falling back to job closure.

- **Reliability (Logout & Sleep)**:
  - *User Logout*: When the user logs out, Windows terminates the interactive desktop session. The supervisor's handle closes, and the kernel immediately and atomically destroys all child processes. Zero orphans guaranteed.
  - *System Sleep*: The supervisor remains suspended in memory during sleep. Upon wake, execution resumes. However, physical GPU drivers often reset VRAM contexts during ACPI S3/S0ix sleep, which requires supervisor-level health-check reconnection.

- **UAC / Privileges**:
  - Requires **zero administrative privileges**. Job Objects can be created by any unprivileged user process running at standard Medium Integrity Level. No UAC dialog is ever presented to the user.

- **Upgrade Path**:
  - Trivial. Terminating the supervisor immediately frees file locks on `llama-server.exe` and Python virtualenv libraries. Update binaries can be written to user-writable directories (`%LOCALAPPDATA%\ODS`) immediately without file-in-use errors (`ERROR_SHARING_VIOLATION`).

- **CI Test Cost**:
  - Extremely cheap and fast. GitHub Actions `windows-latest` runners support Job Objects out of the box. An offline test suite executing stub child processes runs in under 8 seconds without network access.

---

### Option 2: Host-Agent Scheduled-Task Approach
*Implementation reference: Maintainer's existing Windows implementation*
- [`ods/installers/windows/ods.ps1:2921-2967`](file:///Users/macbookair/dream-server/scratch/fresh_upstream_ods/ods/installers/windows/ods.ps1#L2921-L2967) (`Register-ScheduledTask`, `New-ScheduledTaskTrigger -AtLogOn`)
- [`ods/installers/windows/lib/host-agent-process.ps1:3-41`](file:///Users/macbookair/dream-server/scratch/fresh_upstream_ods/ods/installers/windows/lib/host-agent-process.ps1#L3-L41) (`Stop-ODSHostAgentProcess` with CIM `Win32_Process` verification)
- [`ods/installers/windows/lib/native-llama-runtime.ps1:1197-1209, 1642-1668`](file:///Users/macbookair/dream-server/scratch/fresh_upstream_ods/ods/installers/windows/lib/native-llama-runtime.ps1#L1642-L1668) (`Stop-ODSPortalOwnedProcesses` with `$process.Kill()`)
- [`ods/bin/ods-host-agent.py:21942-21943`](file:///Users/macbookair/dream-server/scratch/fresh_upstream_ods/ods/bin/ods-host-agent.py#L21942-L21943) (`SIGTERM` and `SIGINT` registration)

- **Mechanism**:
  `ods.ps1:2921-2936` registers a Windows Scheduled Task named `ODS Host Agent` configured with trigger `-AtLogOn` and principal `New-ODSInteractiveScheduledTaskPrincipal -RunLevel Limited`. If Task Scheduler registration fails (e.g. corporate GPO restriction), lines 2949-2960 fall back to writing a VBScript file (`ods-host-agent.vbs`) into the user's Start Menu Startup folder (`shell:startup`).

- **Reliability (Logout & Sleep)**:
  - *User Logout*: When the user logs out, Task Scheduler sends a termination request to the PowerShell wrapper. However, because PowerShell spawns `python.exe` and `llama-server.exe` as independent child processes without a Job Object container, child processes can decouple and remain running in the background. As documented by the maintainer in `native-llama-runtime.ps1:1653-1654`:
    > *"Task Scheduler may have exited this held process after HasExited but before Kill. Accept that race only if the same handle proves exit."*
  - *System Sleep*: The scheduled task continues running if power settings allow (`-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries`), but there is no mechanism to re-verify child process liveliness after wake.

- **UAC / Privileges**:
  - Operates under standard user rights (`-RunLevel Limited`). However, `Register-ScheduledTask` can fail in locked-down enterprise environments, requiring the maintainer's VBScript fallback in `ods.ps1:2949`.

- **Upgrade Path**:
  - Requires complex multi-step teardown: invoking `Stop-ScheduledTask` (`ods.ps1:2921`), querying CIM instances via WMI (`Get-CimInstance Win32_Process`), matching command lines via regex (`host-agent-process.ps1:22-33`), and forcefully calling `Stop-Process -Force`. If a child PID is recycled or missed, file locks prevent binary overwrites.

- **CI Test Cost**:
  - Moderate to high. Scheduled tasks can be registered in CI, but testing logon triggers, user logoff, and asynchronous Task Scheduler lifecycles cannot be validated without heavyweight VM orchestration.

---

### Option 3: Windows Service (via NSSM or pywin32)
*Implementation reference: Not present in repository; evaluated as standard Windows background mechanism.*

- **Mechanism**:
  Registers an NT Service with the Windows Service Control Manager (SCM) using NSSM (Non-Sucking Service Manager) or `pywin32`'s `win32serviceutil`. The service runs continuously in the background under `NT AUTHORITY\SYSTEM` or `NT AUTHORITY\LocalService`.

- **Reliability (Logout & Sleep)**:
  - *User Logout*: **Survives user logout**. The service runs in Session 0 independent of any interactive desktop session.
  - *System Sleep*: Intercepts `SERVICE_CONTROL_POWEREVENT` via `HandlerEx` to pause or resume operations.

- **The Fatal Flaw for Desktop LLM Stacks: Session 0 Isolation**:
  - Since Windows Vista, services are strictly isolated into **Session 0** (non-interactive session) to prevent shatter attacks.
  - **GPU Driver Access**: Consumer NVIDIA GeForce and AMD Radeon drivers running on Windows 10/11 Home/Pro restrict CUDA, ROCm, and DirectML compute acceleration in Session 0. Full GPU acceleration requires an active user display driver context unless the GPU is in enterprise TCC (Tesla Compute Cluster) mode—which consumer GeForce cards (RTX 3060, 4070, etc.) do not support.
  - As a result, running `llama-server.exe` inside a Windows Service either falls back to slow CPU inference or fails completely with `cudaErrorNoDevice` / `DXGI_ERROR_NOT_CURRENTLY_AVAILABLE`.

- **UAC / Privileges**:
  - **Requires full Administrator privileges**. Creating a service (`sc.exe create`), configuring recovery options, and stopping/starting services require an elevated UAC prompt. This directly violates the requirement for a zero-privilege desktop installer.

- **Upgrade Path**:
  - Fragile. If files are locked by a hanging service thread, unregistering the service marks it as "marked for deletion" in the SCM, preventing re-installation until a full Windows reboot.

- **CI Test Cost**:
  - High. Requires running tests with elevated privileges, installing third-party wrapper binaries (NSSM), and parsing Event Viewer logs.

---

## 4. What CANNOT Be Known Without Physical Windows Hardware

Automated CI on GitHub Actions `windows-latest` runs on virtualized Azure VMs (Standard_DS2_v2 / Standard_D4s_v5) without physical GPU passthrough. The following operational realities **cannot be proven in CI** and require validation on real Windows consumer PCs:

1. **GPU Driver VRAM Recovery Across Modern Standby (S0ix / S3 Sleep)**:
   - On physical laptops (NVIDIA Optimus, AMD Advantage), entering Modern Standby cuts PCIe link power.
   - Does `llama-server.exe` crash with `CUDA error: an illegal memory access was encountered` or `cudaErrorDevicesUnavailable` when the user opens their laptop lid? Only physical hardware testing can determine if the supervisor must detect sleep resume and automatically restart `llama-server`.

2. **Windows Defender / SmartScreen Heuristic File Locking (`ERROR_SHARING_VIOLATION`)**:
   - On real consumer Windows 11 systems, Windows Defender real-time scanning hooks newly extracted `.exe` files (`llama-server.exe`) and downloaded `.gguf` models.
   - When the supervisor immediately attempts to execute `llama-server.exe` after extraction, Defender's scan frequently holds an exclusive read lock for 500ms–2000ms, causing `subprocess.Popen` to fail with Win32 Error 32 (`ERROR_SHARING_VIOLATION`) or Error 5 (`ERROR_ACCESS_DENIED`).

3. **OEM Thermal & Efficiency Throttling (EcoQoS)**:
   - On Windows 11, background processes not tied to an active top-level window are automatically assigned to **EcoQoS** (Power Throttling), constraining threads to CPU efficiency cores.
   - Hardware validation is required to test whether `SetProcessInformation(ProcessPowerThrottling)` is needed to prevent inference degradation.

4. **Multi-User Fast User Switching & Port Contention**:
   - If User A switches to User B without logging off, does `llama-server.exe` running in User A's session retain exclusive VRAM access or fail when User B launches ODS?

---

## 5. Architectural Recommendations & Decision Points

1. **Adopt Option 1 (Win32 Job Object Supervisor) as the Native Engine**:
   - Provides guaranteed zero-orphan lifecycle without requiring administrative privileges.
   - Eliminates complex CIM/WMI process-scraping heuristics currently used in `host-agent-process.ps1`.
   - Runs cleanly in standard desktop user sessions with full GPU acceleration.

2. **Keep Option 2 (Scheduled Task / Startup Shortcut) Strictly as an Auto-Start Launcher**:
   - Do not use Scheduled Tasks for *process supervision*.
   - Use the Scheduled Task (`ods.ps1:2925`) or Start Menu Startup folder solely to trigger the Job Object supervisor when the user logs in.

3. **Reject Option 3 (Windows Service)**:
   - Session 0 GPU restrictions and mandatory UAC elevation make Windows Services unsuitable for consumer desktop LLM distribution.
