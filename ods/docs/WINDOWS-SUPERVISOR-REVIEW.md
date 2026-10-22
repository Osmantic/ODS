# Maintainer Decision Record: Windows Native Supervisor Architecture

**Document ID**: `ODS-MDR-2026-WIN01`
**Status**: Experimental Proposal / Pending Maintainer Review
**Author**: Antigravity (AI Contributor for `vaibhavsrv`)
**Target Upstream Repository**: `Osmantic/ODS`
**Branch**: `experiment/windows-supervisor-clean`
**Verified Code Commit**: `0ef5cc0afe69403a49af3aa89c258a91a27e8577` (`0ef5cc0af`)
**Verified CI Telemetry**: Offline Run 39751880189 & Manual Heavy Run 39750717938

---

## Executive Recommendation

> **Recommendation**: Retain the Windows Native Supervisor as an **experimental, opt-in prototype** (`ods/experimental/windows-supervisor/`) until physical hardware qualification (consumer GPU Session 0 behavior, Modern Standby sleep/wake, and Windows Defender real-time scan latency) and installer integration gates are formally approved by maintainers.

This proposal provides the maintainer team with all verified evidence, architectural trade-offs, and decision gates necessary to evaluate adopting native Win32 Job Object supervision for a Docker-free Windows stack.

---

## 1. The Problem Being Solved

To run ODS on consumer Windows desktop installations without requiring Docker Desktop, WSL2, or administrative UAC elevation, ODS must orchestrate two decoupled native processes:
1. `llama-server.exe`: A native C++ inference server allocating GPU VRAM or system RAM to serve model weights.
2. `dashboard-api`: A Python/FastAPI/Uvicorn HTTP service exposing management endpoints on localhost.

### Operational Hazards in Baseline Windows Supervision
Without container isolation, unmanaged native background processes on Windows introduce several severe failure modes:
- **Orphaned Processes & Port Locking** `[Verified repository behavior]`: If a parent script, terminal, or wrapper crashes, child processes continue running in the background. Subsequent launches fail with port collisions (`WSAEADDRINUSE`, exit code 3) or VRAM starvation.
- **Fragile Process Scraping** `[Verified repository code]`: The existing PowerShell installer (`ods/installers/windows/lib/host-agent-process.ps1:16-35`) terminates processes by executing CIM/WMI queries against `Win32_Process` and parsing command lines with regular expressions. This is vulnerable to process ID recycling and timing races.
- **Silent Signal Termination & Masqueraded Exits** `[Verified CI behavior]`: Windows lacks standard POSIX `SIGTERM`. Processes receiving break events frequently terminate with MSVCRT exit code `3` or unhandled NTSTATUS exceptions, masking abnormal termination as successful execution or vice versa.

---

## 2. Proposed Architecture & Process Ownership

The proposed architecture introduces a self-contained, unprivileged Python supervisor (`ods/experimental/windows-supervisor/supervisor.py`) built directly on the Windows NT kernel API using standard library `ctypes`.

```
+-------------------------------------------------------------------------+
|                        User Desktop Session (Session 1+)                |
|                                                                         |
|  [Windows Scheduled Task / Startup Shortcut]                           |
|         | launches unprivileged (RunLevel Limited)                      |
|         v                                                               |
|  +-------------------------------------------------------------------+  |
|  |             ODS Supervisor (supervisor.py)                        |  |
|  |  - Owns Win32 Job Object (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)     |  |
|  |  - Pre-flight Port Check (127.0.0.1 loopback)                     |  |
|  |  - Synchronous stdout/stderr file redirection                     |  |
|  |  - Pre-registered SIGBREAK Lifespan Handler                       |  |
|  |  - Ordered Stop Dispatch & Honest Exit Classification             |  |
|  +-------------------------------------------------------------------+  |
|         |                                            |                  |
|         | AssignProcessToJobObject                   | AssignProcess    |
|         v                                            v                  |
|  +-------------------------+              +-------------------------+   |
|  |    llama-server.exe     |              |     dashboard-api       |   |
|  |   (Inference Engine)    |              |   (Uvicorn / FastAPI)   |   |
|  +-------------------------+              +-------------------------+   |
|                                                                         |
|  ==================== NT KERNEL JOB OBJECT ===========================  |
|  If supervisor dies for ANY reason -> Kernel kills ALL children instantly |
+-------------------------------------------------------------------------+
```

### Process Ownership Contract
1. **Kernel-Guaranteed Teardown**: The supervisor configures `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (`0x2000`). If the supervisor process terminates, crashes, or is killed via Task Manager, the Windows NT kernel immediately terminates all child processes. Zero orphans can linger.
2. **Process Group Isolation**: Children are spawned with `CREATE_NEW_PROCESS_GROUP`, allowing targeted signal delivery.
3. **Dedicated File Redirection**: Child process output streams are redirected to synchronous log files (`llama.log`, `dashboard.log`), avoiding pipe buffer deadlocks.
4. **Pre-flight Port Validation**: Validates loopback ports prior to spawning children, exiting immediately with exit code `3` on collision.

---

## 3. Evidence Supporting the Exit-Code Fix

### The Root Cause of Uvicorn Exit Code 3
When Uvicorn runs under Python on Windows and receives `CTRL_BREAK_EVENT` (`SIGBREAK`), Uvicorn’s internal signal handler executes the following sequence (`uvicorn/server.py:310-330`):
1. Intercepts `SIGBREAK`, sets `self.should_exit = True`, and awaits ASGI lifespan shutdown.
2. Lifespan shutdown completes cleanly, writing the durable marker `LIFESPAN_COMPLETED`.
3. Uvicorn restores the previous signal handler (`signal.signal(signal.SIGBREAK, signal.SIG_DFL)`) and re-raises `SIGBREAK` via `signal.raise_signal()`.
4. Under the Microsoft Visual C Runtime (MSVCRT), the default `SIG_DFL` handler for `SIGBREAK` terminates the process by calling `_exit(3)` (exit code **3**).

### The Pre-Registered Handler Solution
By pre-registering a custom signal handler prior to invoking Uvicorn:
```python
signal.signal(signal.SIGBREAK, lambda signum, frame: sys.exit(0))
```
When Uvicorn completes its ASGI lifespan shutdown and restores the pre-existing handler, it restores this custom handler rather than `SIG_DFL`. Re-raising `SIGBREAK` therefore invokes `sys.exit(0)`, terminating cleanly with exit code **0**.

### Exit Classification Matrix
The supervisor truthfully classifies child process termination:
- **Clean Cooperative Exit (`GRACEFUL`)**: Child exits with code `0`.
- **Native Console Break Exit (`GRACEFUL`)**: Child exits with NTSTATUS `STATUS_CONTROL_C_EXIT` (`0xC000013A` / `3221225786` / `-1073741510`), recognized as standard Win32 console break termination `[Documented Windows behavior]`.
- **Abnormal Exit (`ABNORMAL`)**: Child exits with code `3` or any unexpected non-zero code. Supervisor fails with exit code **`7`** (`EXIT_CHILD_STOP_FAILED`) `[Verified repository behavior]`.
- **Forced Kill (`FORCED`)**: Child fails to exit within `--stop-timeout` and requires kernel termination. Supervisor fails with exit code **`7`** `[Verified repository behavior]`.

### Mutation Experiment Verification
To guarantee that the abnormal exit test (Test 5 in `test_supervisor.py`) cannot produce a false positive, the supervisor was deliberately mutated to treat exit code 3 as normal:
```text
AssertionError: Expected supervisor exit code 7 on abnormal child exit, got 0
```
The test failed immediately, proving the test harness actively enforces this guarantee `[Locally verified]`.

---

## 4. Offline Test Coverage & CI Telemetry

All automated PR verification runs against an offline stub test suite that requires zero network access, zero model downloads, and executes in seconds.

### Verified CI Runs

| Metric | Offline Suite Run | Heavy Real-Download Run |
|---|---|---|
| **Run Link** | Offline Run 39751880189 | Manual Run 39750717938 |
| **Tested Commit** | `0ef5cc0afe69403a49af3aa89c258a91a27e8577` | `3a215fd7066914594c965e6488d0113f972bfe9a` |
| **Windows Job** | `test-offline-windows`: **Passed** (23s) | `test-real-downloads`: **Passed** (1m 8s) |
| **Ubuntu Job** | `test-offline-ubuntu`: **Passed** (16s) | N/A (Linux control runs offline suite) |
| **CTRL_BREAK Job** | `reproduce-ctrl-break`: **Passed** (38s) | Included in offline run |
| **Real Downloads** | **Skipped** (`run_real_downloads: false`) | **Executed** (Pinned llama-server + SmolLM2) |

### Test Suite Summary
1. **`test_port_conflict`**: Asserts immediate rejection with exit code 3 when target port is bound.
2. **`test_corrupt_model_hash`**: Asserts exit code 2 when model bytes fail SHA-256 verification.
3. **`test_child_death`**: Kills child process strictly by PID; asserts supervisor exits with code 6 within 400ms.
4. **`test_zero_orphans`**: Forcefully kills supervisor by PID; queries `tasklist` to verify kernel terminated all child PIDs.
5. **`test_abnormal_stop_detection`**: Stubs child to exit code 3 on break; asserts supervisor detects abnormal stop and exits 7.
6. **`test_timeout_handling`**: Stubs child to ignore break; asserts supervisor forces kill after timeout and exits 7.

---

## 5. Isolation of Real Model Downloads

The CI workflow (`.github/workflows/experiment-windows-supervisor.yml`) strictly separates fast offline verification from heavy network operations:
- **Default Behavior**: Pull requests and default workflow dispatches execute only the offline matrix (`test-offline-windows`, `test-offline-ubuntu`, `reproduce-ctrl-break`).
- **Opt-In Guard**: The heavy download job is guarded by `inputs.run_real_downloads` with `default: false`.
- **Rationale**: Downloading 2GB GGUF models and 20MB binaries incurs network latency, GitHub runner bandwidth consumption, and external dependencies on Hugging Face and GitHub releases. Fast offline stubs keep developer feedback loops fast (<30s) and 100% deterministic.

---

## 6. Comparison with Existing Scheduled Task / Host-Agent Design

| Responsibility | Existing Production Installer (`ods.ps1`) | Proposed Job Object Supervisor (`supervisor.py`) | Recommended Hybrid Design |
|---|---|---|---|
| **Auto-Start at Login** | `Register-ScheduledTask -Trigger AtLogOn` (`ods.ps1:2921`) | None (Prototype CLI only) | **Retain Scheduled Task** solely as logon launcher |
| **Process Ownership** | PowerShell `Start-Process` (unmanaged tree) | `ctypes` Win32 Job Object (`0x2000`) | **Adopt Job Object** for all child processes |
| **Process Teardown** | CIM `Win32_Process` query + regex matching | Kernel-enforced Job Object destruction | **Adopt Job Object**; eliminate CIM regex parsing |
| **Graceful Stop** | Process kill / taskkill | `CTRL_BREAK_EVENT` + Lifespan validation | **Adopt CTRL_BREAK** with lifespan verification |
| **Port Conflict Check** | TCP test socket in PowerShell loop | Pre-flight loopback socket bind (Exit 3) | **Adopt Supervisor Pre-flight** check |
| **Child Failure Detection** | Periodic PowerShell health poll | Active PID polling (`poll()` every 100ms) | **Adopt Supervisor PID polling** (Exit 6) |

---

## 7. Operational Consequences of User Logout

- **Interactive User Session (Session 1+)** `[Documented Windows behavior]`:
  The supervisor runs inside the user's interactive logon session (Medium Integrity Level). When the user logs out, Windows terminates the logon session. The kernel closes the supervisor's Job Object handle, terminating `llama-server.exe` and `dashboard-api`.
- **Comparison with Windows Services (Session 0)** `[Documented Windows behavior]`:
  A Windows NT Service persists across user logouts. However, Session 0 isolation restricts access to consumer display driver compute contexts (DirectX12, Vulkan, WDDM CUDA).
- **Architectural Conclusion**: For a consumer desktop application running on GeForce/Radeon laptops and desktops, executing within the user's desktop session is the only viable path to ensure full GPU compute availability. Background persistence across logout is an anti-feature for consumer desktop LLM workloads that consume heavy VRAM.

---

## 8. Remaining GPU, Sleep/Wake, and Hardware-Dependent Risks

The following questions **cannot be tested in virtualized CI** and represent mandatory physical hardware acceptance gates:

1. **Consumer GPU WDDM Driver Session Contexts** `[Hardware-dependent question]`:
   - Consumer GeForce/Radeon drivers in WDDM mode require active desktop sessions to allocate compute contexts. Verification is required across NVIDIA (RTX 30xx/40xx), AMD (Radeon RX 6xxx/7xxx), and Intel Arc GPUs.
2. **Modern Standby (S3 / S0ix) Sleep/Resume** `[Hardware-dependent question]`:
   - When a Windows 11 laptop enters Modern Standby, PCIe power states can depower the GPU. Does `llama-server.exe` recover its CUDA context upon resume, or does it throw `cudaErrorDevicesUnavailable` (CUDA error 46)?
3. **Microsoft Defender Real-Time Scan Latency** `[Hardware-dependent question]`:
   - Microsoft Defender Antivirus (`MsMpEng.exe`) scans newly extracted `.exe` and `.gguf` files. On consumer drives, real-time scanning can lock files for 500ms–2000ms (`ERROR_SHARING_VIOLATION`), requiring retry backoff during startup.
4. **Windows 11 EcoQoS Efficiency Core Throttling** `[Hardware-dependent question]`:
   - Windows 11 automatically applies EcoQoS power throttling to background processes without windows. Does inference performance degrade unless `SetProcessInformation(ProcessPowerThrottling)` is called?

---

## 9. Security & Privilege Implications

- **Standard User Execution**: The supervisor, `llama-server.exe`, and `dashboard-api` execute under standard user privileges (`-RunLevel Limited`). No administrative elevation (UAC prompt) is required during installation or execution.
- **Cryptographic Pinned Verification**:
  - Pinned `llama-server.exe` b9014 archive SHA-256: `3503cc30154266ac0fbedd38e39e35a313a13412415738ae8f9e23e4fcb44da2`.
  - Pinned SmolLM2 model SHA-256: `ed5fa30c487b282ec156c29062f1222e5c20875a944ac98289dbd242e947f747` (HF commit `9e6855bc4be717fca1ef21360a1db4b29d5c559a`).
- **Network Surface**: Pre-flight socket binds and child listeners bind strictly to `127.0.0.1` (loopback). No firewall rule modifications or public interface bindings are created.

---

## 10. Staged Adoption Plan & Acceptance Gates

```
[ Stage 1: Experimental Prototype ]  <--- CURRENT STATE (Passed CI & Audit)
  - Isolated under ods/experimental/windows-supervisor/
  - Zero production code touched
  - Deterministic offline tests pass in CI (Windows & Ubuntu)
          |
          v
[ Stage 2: Physical Hardware Qualification ]  <--- NEXT STEP
  - Test on physical Windows 11 hardware with NVIDIA GeForce & AMD Radeon
  - Measure Defender file-lock latency & EcoQoS thread scheduling
  - Validate Modern Standby sleep/resume behavior
          |
          v
[ Stage 3: Installer Integration ]
  - Update ods.ps1 Scheduled Task to invoke supervisor.py
  - Remove fragile CIM process scraping from host-agent-process.ps1
  - Retain Scheduled Task as logon trigger
          |
          v
[ Stage 4: Production Release ]
  - Promote out of experimental status
  - Enable telemetry for process crash and restart events
```

---

## 11. Rollback Strategy

Because all experimental code is strictly isolated under `ods/experimental/windows-supervisor/` and `ods/docs/`, rollback requires zero state migration:
1. Revert or delete the directory `ods/experimental/windows-supervisor/`.
2. Delete `.github/workflows/experiment-windows-supervisor.yml`.
3. The production Windows installer (`ods.ps1`) and host agent (`ods-host-agent.py`) remain completely intact and functional in their baseline state.

---

## 12. Precise Decisions Required from Maintainers

The maintainer team is requested to evaluate and decide upon the following four questions:

1. **Adoption of Win32 Job Objects**: Does the team approve replacing PowerShell CIM query/regex process scraping with kernel-enforced Win32 Job Object supervision?
2. **Hybrid Architecture**: Does the team approve the hybrid design: retaining the Windows Scheduled Task (`ods.ps1:2921`) solely as the logon launcher while delegating all process supervision and teardown to `supervisor.py`?
3. **Session 0 vs User Session**: Does the team confirm that user-session execution (terminating on logout) is the acceptable trade-off to ensure full consumer GPU compute support?
4. **Hardware Qualification Partner**: Who on the team or community can provide physical Windows 11 hardware with consumer NVIDIA/AMD GPUs to execute the Stage 2 qualification gates?
