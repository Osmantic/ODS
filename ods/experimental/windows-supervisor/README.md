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

3. **Stop Mechanism (CTRL_BREAK, wait, then kill)**:
   - Supervised child processes are spawned in new process groups (`CREATE_NEW_PROCESS_GROUP`).
   - On shutdown or `--stop-after-ready`, the supervisor sends `CTRL_BREAK_EVENT` to the child process groups.
   - Awaits cooperative termination up to `--stop-timeout` (default 5.0s).
   - If children do not exit before timeout, the supervisor terminates them forcefully and closes the NT Job Object to ensure complete process tree cleanup.

4. **Failure Safeguards & Exit Codes**:
   - **Exit code 0**: Successful execution, probe completion, or cooperative stop.
   - **Exit code 2**: SHA-256 hash mismatch on runtime archive, extracted executable, or GGUF model file.
   - **Exit code 3**: Port collision on loopback interface (pre-flight check).
   - **Exit code 5**: Child health probe timeout during startup.
   - **Exit code 6**: Supervised child process died unexpectedly mid-run.

## Test Suite (`test_supervisor.py`)

The suite exercises 7 end-to-end integration and failure tests on Windows:
1. **Happy Path Probe**: Starts supervisor with dynamically allocated free ports and per-run API key, verifies health probe and `/api/chat` through dashboard-api, then stops (exit code 0).
2. **Port Collision Rejection**: Binds a blocking socket on loopback, verifies supervisor pre-flight detects collision and rejects startup (exit code 3).
3. **Cached Hash Healing**: Pre-populates a corrupted cached archive, verifies supervisor detects hash mismatch, re-downloads cleanly to `.part`, and recovers (exit code 0).
4. **Persistent Hash Rejection**: Spins up a local test HTTP server serving corrupted bytes via `--llama-url` override, verifies persistent mismatch is rejected without test hooks (exit code 2).
5. **Mid-Run Child Death Detection**: Kills child `llama-server.exe` strictly by spawned PID, verifies supervisor detects death and exits with code 6.
6. **Supervisor Forced Kill (Zero Orphans)**: Force-kills the supervisor process strictly by PID, verifies Win32 Job Object kernel cleanup terminates both children and releases ports.
7. **Stop Path Verification**:
   - **Subtest 7A**: Cooperative stop path with `CTRL_BREAK_EVENT` and wait within timeout (exit code 0).
   - **Subtest 7B**: Forced stop path where timeout expires and fallback termination triggers (exit code 0).

## Running

```bash
# Run probe mode (starts, verifies health & chat completion through dashboard-api, then stops):
python ods/experimental/windows-supervisor/supervisor.py --run-probe

# Run persistent supervision:
python ods/experimental/windows-supervisor/supervisor.py

# Run test suite:
python ods/experimental/windows-supervisor/test_supervisor.py
```

## Known Limitations

- **Host Python Required**: Tested and qualified under Python 3.12 on Windows runners (`windows-latest`).
- **CPU Inference Only**: Pinned to CPU backend (`llama-b9014-bin-win-cpu-x64.zip`). Physical GPU acceleration (Vulkan, DirectML, CUDA) requires physical hardware qualification.
- **Single-Machine Loopback**: Binds strictly to `127.0.0.1`.
- **Experimental Boundary**: Operates strictly in isolation under `ods/experimental/windows-supervisor/` without altering default ODS production installer or Compose flows.
