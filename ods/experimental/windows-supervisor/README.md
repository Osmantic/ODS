# Experimental Windows Native Supervisor for ODS

This directory contains an experimental, opt-in native process supervisor for running ODS on Windows without Docker or WSL.

## Architecture

Unlike containerized installations or external service wrappers, this supervisor manages the core inference runtime (`llama-server.exe`) and the control plane (`dashboard-api`) as supervised native child processes under a single **Windows NT Job Object**:

1. **Win32 Job Object Supervision**:
   - Initializes a Win32 Job Object with limit flag `0x2000` (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`).
   - Assigns both `llama-server.exe` and `uvicorn` (dashboard-api) to the same job object.
   - If the supervisor process crashes, is killed (`taskkill /F`), or receives an unexpected exit, the Windows NT kernel immediately and atomically terminates all child processes. Zero orphaned processes or held socket listeners can linger.

2. **Cryptographic Pinned Verification**:
   - Pinned llama.cpp runtime: b9014 CPU x64 (archive SHA-256 `3503cc30154266ac0fbedd38e39e35a313a13412415738ae8f9e23e4fcb44da2`, executable SHA-256 `fe91d802d244958dfbca5f12e2f3d5369fcbb883b68f9a9c228229b471cb3cb6`).
   - Pinned model: SmolLM2 135M Instruct Q4_K_M (SHA-256 `ed5fa30c487b282ec156c29062f1222e5c20875a944ac98289dbd242e947f747`, HF commit `9e6855bc4be717fca1ef21360a1db4b29d5c559a`).
   - Downloads write atomically to temporary `.part` files and rename only on complete download.
   - On cached hash mismatch, attempts a single automatic re-download; if persistent, refuses startup with exit code 2.

3. **Stop Mechanism (CTRL_BREAK, Lifespan Shutdown, Honest Relabeling)**:
   - Supervised child processes are spawned in new process groups (`CREATE_NEW_PROCESS_GROUP`).
   - On shutdown or `--stop-after-ready`, the supervisor sends `CTRL_BREAK_EVENT` to the child process groups.
   - **Exit Code 3 Root Cause & Fix**: Standard Uvicorn captures `SIGBREAK`, runs full lifespan shutdown, restores the pre-existing handler (`signal.SIG_DFL`), and re-raises `SIGBREAK`. Under MSVCRT, unhandled `SIGBREAK` terminates with exit code 3. The supervisor fixes this by pre-registering a `SIGBREAK` handler (`lambda s, f: sys.exit(0)`) prior to `uvicorn.run()`. When Uvicorn re-raises `SIGBREAK` after lifespan shutdown, the restored custom handler exits cleanly with code 0.
   - **Honest Status Relabeling**: The supervisor reports stop mode truthfully:
     - `GRACEFUL`: All processes exited cooperatively with code 0 before timeout.
     - `ABNORMAL`: One or more processes exited with a non-zero return code (supervisor exits with code 7 on `--stop-after-ready`).
     - `FORCED`: Child did not exit before `--stop-timeout` and required forceful kill (supervisor exits with code 7 on `--stop-after-ready`).

4. **Failure Safeguards & Exit Codes**:
   - **Exit code 0**: Successful execution, probe completion, or clean graceful stop.
   - **Exit code 2**: SHA-256 hash mismatch on runtime archive, extracted executable, or GGUF model file.
   - **Exit code 3**: Port collision on loopback interface (pre-flight check).
   - **Exit code 5**: Child health probe timeout during startup.
   - **Exit code 6**: Supervised child process died unexpectedly mid-run.
   - **Exit code 7**: Stop path experienced abnormal child exit code or forced timeout termination.

---

## Test Suites

The test harness is cleanly split between fast offline tests and heavy network tests:

### 1. Offline Stub Test Suite (`test_supervisor.py`)
Runs against lightweight stub child processes (`python -c ...`) with zero network access and completes in under 8 seconds. Exercises:
1. **Port Collision Rejection** (Exit Code 3): Binds a blocking socket and asserts immediate pre-flight rejection.
2. **Mid-Run Child Death Detection** (Exit Code 6): Kills child strictly by spawned PID and asserts supervisor exits with strictly code 6.
3. **Supervisor Killed / Zero Orphans**: Forcefully kills supervisor by PID and verifies all child PIDs are terminated by kernel Job Object.
4. **Graceful Stop Path (Clean Exit 0)**: Asserts all processes exit cooperatively with return code 0.
5. **Abnormal Child Exit on Stop Path (Assert Exit 7)**: Configures stub child to return code 3 on break, asserting supervisor detects abnormal stop and exits 7.
6. **Forced Stop Path (Timeout Fallback, Exit 7)**: Configures stub child to ignore break signal, asserting supervisor triggers timeout, forceful kill, and exits 7.

### 2. Real Download & Live Chat Suite (`test_real_downloads.py`)
Executed via manual `workflow_dispatch` job in CI:
1. **Live Happy Path Probe**: Downloads pinned llama-server and SmolLM2 GGUF, launches real services, probes `/health` and live `/api/chat` through dashboard-api.
2. **Cached Hash Healing**: Pre-populates corrupted cached archive, verifies automatic re-download healing to `.part`.
3. **Persistent Hash Rejection**: Spins up local HTTP server serving bad bytes via URL override, asserts startup refusal (Exit Code 2).

**CI Opt-in Behavior**: In GitHub Actions (`experiment-windows-supervisor.yml`), `test-real-downloads` is gated behind `inputs.run_real_downloads` with `default: false`. Ordinary pull requests and default workflow dispatches execute only the offline stub suites and skip real downloads entirely.

### 3. Win32 CTRL_BREAK Reproduction (`reproduce_ctrl_break.py`)
Demonstrates direct MSVCRT exit code 3, standard Uvicorn lifespan behavior, and pre-registered handler clean exit code 0.

---

## Production Integration Boundary & Experimental Status

- **Experimental Scope**: This directory is an isolated proof-of-concept prototype. It is **not** currently integrated into the production installer (`ods/installers/windows/ods.ps1`), process management helpers (`ods/installers/windows/lib/host-agent-process.ps1`, `native-llama-runtime.ps1`), or host-agent daemon (`ods/bin/ods-host-agent.py`).
- **Zero Production Changes**: Upstream production code, Docker Compose templates, Linux runtime configurations, and authentication modules (`hermes_auth.py`) remain completely untouched.
- **Review Decision Record**: For maintainer evaluation, architectural trade-offs, and staged adoption gates, see:
  - Architecture Design Note: [`ods/docs/WINDOWS-DOCKER-FREE-SUPERVISION.md`](../docs/WINDOWS-DOCKER-FREE-SUPERVISION.md)
  - Maintainer Review Decision Record: [`ods/docs/WINDOWS-SUPERVISOR-REVIEW.md`](../docs/WINDOWS-SUPERVISOR-REVIEW.md)

---

## Safe Teardown & Temporary Artifact Cleanup

### Stopping a Running Supervisor
- **Interactive Console**: Send `Ctrl+Break` or `Ctrl+C` into the console window. The supervisor intercepts the signal, prompts child processes to exit, and terminates cleanly.
- **Forceful Kill**:
  ```cmd
  taskkill /F /PID <supervisor_pid> /T
  ```
  Because both `llama-server.exe` and `dashboard-api` are bound to the Win32 Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, killing the supervisor automatically and immediately terminates all child processes.
- **Verification**:
  ```cmd
  tasklist | findstr /i "llama-server python uvicorn"
  ```

### Cleaning Temporary Test Artifacts
The test suites generate local state inside git-ignored directories:
- `test_data/`: Temporary models, runtime extracts, and test logs.
- `supervisor_data/`: Default working directory for local runs.

To clean up:
```bash
# Windows (cmd):
rmdir /s /q test_data supervisor_data

# Windows (PowerShell) / Unix:
rm -rf test_data supervisor_data
```

---

## Outstanding Physical Hardware Validation

The following hardware-dependent questions cannot be validated in virtualized CI and require verification on real Windows 11 hardware prior to production release:
1. **Consumer GPU WDDM Driver Contexts in Session 0 vs Session 1**: Consumer GeForce/Radeon drivers require interactive desktop sessions to allocate compute contexts.
2. **Microsoft Defender Real-Time Scanning Latency**: Real-time inspection of 2GB model weights may hold file locks or introduce token latency.
3. **Modern Standby (S3 / S0ix) Sleep/Resume**: Verifying whether CUDA/DirectML contexts recover cleanly or require supervisor restarts upon system wake.
4. **Windows 11 EcoQoS Power Throttling**: Verifying whether background child processes are throttled to Efficiency cores without visible windows.

---

## Running

```bash
# Run fast offline stub tests (zero network, ~8s):
python ods/experimental/windows-supervisor/test_supervisor.py

# Run real download suite (requires internet, ~35s):
python ods/experimental/windows-supervisor/test_real_downloads.py

# Run Win32 CTRL_BREAK reproduction script:
python ods/experimental/windows-supervisor/reproduce_ctrl_break.py
```
