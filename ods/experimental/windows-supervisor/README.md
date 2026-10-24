# Experimental Windows Native Supervisor for ODS

This directory contains an experimental, opt-in native process supervisor for running ODS on Windows without Docker or WSL.

## Architecture

This prototype manages the core inference runtime (`llama-server.exe`) and the control plane (`dashboard-api`) as supervised child processes inside a single **Windows NT Job Object**:

1. **Win32 Job Object Supervision**:
   - Initializes a Win32 Job Object with limit flag `0x2000` (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`).
   - Assigns both child process handles (`llama-server.exe` and `dashboard-api`) to the Job Object.
   - If the supervisor process exits, crashes, or is terminated (`taskkill /F`), the Windows NT kernel immediately terminates all child processes. Zero orphaned processes can linger.

2. **Cryptographic Pinned Verification**:
   - Pinned llama.cpp runtime: b9014 CPU x64 (archive SHA-256 `3503cc30154266ac0fbedd38e39e35a313a13412415738ae8f9e23e4fcb44da2`, executable SHA-256 `fe91d802f890c05b97b79fabdf7002abc8be4872f239ef21ff55d7d6c3d8c591`).
   - Pinned model: SmolLM2 135M Instruct Q4_K_M (SHA-256 `ed5fa30c487b282ec156c29062f1222e5c20875a944ac98289dbd242e947f747`, HF commit `9e6855bc4be717fca1ef21360a1db4b29d5c559a`).
   - Downloads write atomically to temporary `.part` files and rename only on complete download.

3. **Stop Mechanism ("CTRL_BREAK, wait, then kill")**:
   - Supervised child processes are spawned in new process groups (`CREATE_NEW_PROCESS_GROUP`).
   - On shutdown or `--stop-after-ready`, the supervisor sends `CTRL_BREAK_EVENT` to the child process groups.
   - **Exit Code 3 Root Cause & Fix**: Standard Uvicorn captures `SIGBREAK`, completes ASGI lifespan shutdown, restores the default handler (`signal.SIG_DFL`), and re-raises `SIGBREAK`. Under MSVCRT, unhandled `SIGBREAK` terminates with exit code 3. The supervisor pre-registers a `SIGBREAK` handler (`lambda s, f: sys.exit(0)`) prior to `uvicorn.run()`. When Uvicorn re-raises `SIGBREAK` after lifespan shutdown, the restored custom handler exits cleanly with code 0.
   - **Stop Mode Classification**:
     - `GRACEFUL`: All processes exited cooperatively with code 0 before timeout.
     - `ABNORMAL`: One or more processes exited with a non-zero return code (supervisor exits with code 7 on `--stop-after-ready`).
     - `FORCED`: Child did not exit before `--stop-timeout` and was terminated forcefully (supervisor exits with code 7 on `--stop-after-ready`).

4. **Failure Safeguards & Exit Codes**:
   - **Exit code 0**: Successful execution, probe completion, or clean graceful stop.
   - **Exit code 2**: SHA-256 hash mismatch on runtime archive, extracted executable, or GGUF model file.
   - **Exit code 3**: Port collision on loopback interface (pre-flight check).
   - **Exit code 5**: Child health probe timeout during startup.
   - **Exit code 6**: Supervised child process died unexpectedly mid-run.
   - **Exit code 7**: Stop path experienced abnormal child exit code or forced timeout termination.

---

## Supervision Options Comparison

- **Option 1 (This Prototype: Win32 Job Object)**: Assigns child processes to a kernel Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. The Windows kernel guarantees child process termination on supervisor exit or crash. Runs in the interactive user session (Session 1+) without UAC elevation. Workloads terminate on logout.
- **Option 2 (Current Production: Scheduled Task)**: `ods.ps1` registers an `-AtLogOn` task launching a PowerShell process wrapper. Child processes are spawned detached; termination relies on CIM/WMI process queries.
- **Option 3 (Windows NT Service)**: Runs in Session 0. Persists across logout, but Session 0 isolates desktop display drivers, restricting consumer GPU compute contexts (DirectX12/Vulkan/WDDM CUDA), and requires Administrator UAC elevation.

---

## Test Suite (`test_supervisor.py`)

The offline stub test suite runs against lightweight stub child processes (`python -c ...`) with zero network access in under 9 seconds:
1. **Port Collision Rejection** (Assert Exit 3): Binds a blocking socket to loopback; asserts supervisor exits 3 on port collision.
2. **Mid-Run Child Death Detection** (Assert Exit 6): Kills stub child llama PID mid-run; asserts supervisor detects death and exits 6.
3. **Supervisor Killed / Zero Orphans**: Forcefully kills supervisor by PID; asserts child processes terminate (kernel Job Object on Windows; explicit SKIP on POSIX).
4. **Graceful Stop Path** (Assert Exit 0): Runs `--stop-after-ready --stop-timeout 5.0`; asserts exit 0 when children exit cleanly before timeout.
5. **Abnormal Child Exit on Stop Path** (Assert Exit 7): Configures stub child to return code 3 on break; asserts supervisor detects abnormal stop and exits 7.
6. **Forced Stop Path** (Assert Exit 7): Configures stub child to ignore break; asserts supervisor triggers timeout (0.001s), kills child, and exits 7.
7. **Job Assignment Failure Compensation**: Simulates `AssignProcessToJobObject` failure (e.g. error 5) on second child; asserts supervisor triggers immediate compensation kill and leaves zero orphans.
8. **Secret File ACLs & Credential Elimination**: Asserts `pids.json` contains zero credentials (`api_key` omitted), and asserts secret files (`api_key.txt`, `llama_key.txt`) are restricted owner-only (verified via `icacls` on Windows, `chmod 0600` on POSIX).

---

## Explicit Limits & Boundaries

- **Python Version**: Tested on host Python 3.12 in CI; offline suite verified passing 6/6 by community review on a physical Windows 11 PC under Python 3.10.
- **CPU Execution**: CPU-only execution tested; consumer GPU behavior in Session 0/1 remains unverified without physical hardware.
- **Offline Stub Coverage**: Automated offline tests use stub child processes (`python -c ...`); full model download, GPU offloading, and real-network chat routes are isolated in manual `workflow_dispatch` jobs.
- **Port Helper Concurrency**: Dynamic free-port discovery (`get_free_port`) has a known race window between port release and supervisor binding.
- **Loopback Secret Confinement**: API keys for `dashboard-api` and `llama-server` are written owner-only (`icacls /inheritance:r /grant:r %USERNAME%:F`) and passed via `--api-key-file` and environment variables, keeping plaintext credentials out of process argv and `pids.json`.
- **Cross-Platform Control**: The Linux CI control run validates process management and signal handling logic; Win32 Job Object limits apply exclusively on Windows.
- **Production Integration**: This prototype is strictly isolated under `ods/experimental/windows-supervisor/`; it is not integrated into `ods.ps1` or `ods-host-agent.py`.

---

## Safe Teardown & Cleanup

### Stopping a Running Supervisor
- **Interactive Console**: Send `Ctrl+Break` or `Ctrl+C` in the console window.
- **Forceful Kill**:
  ```cmd
  taskkill /F /PID <supervisor_pid> /T
  ```
  Kernel closes Job Object handles, automatically terminating all child processes.

### Cleaning Temporary Test Artifacts
```bash
# Windows (cmd):
rmdir /s /q test_data supervisor_data

# Windows (PowerShell) / Unix:
rm -rf test_data supervisor_data
```

---

## Running

```bash
# Run fast offline stub tests (zero network, ~8s):
python ods/experimental/windows-supervisor/test_supervisor.py
```
