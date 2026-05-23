# Dream Doctor

Diagnostics command for DreamServer installation and runtime health checks.

## Usage

### Via dream-cli (Recommended)

```bash
# Run diagnostics with operator-friendly output
dream doctor

# Get raw JSON report
dream doctor --json

# Save report to custom location
dream doctor --report /path/to/report.json
```

### Direct Script Invocation

```bash
scripts/dream-doctor.sh
scripts/dream-doctor.sh /tmp/custom-dream-doctor.json
```

## Output

### Operator-Friendly Mode (default)

Displays color-coded diagnostics:
- ✓ Green: Passing checks
- ⚠ Yellow: Warnings
- ✗ Red: Failures/blockers
- Diagnosis entries: evidence-ranked root causes with the file/command that
  supports the conclusion and the next concrete recovery step.

Example output:
```
━━━ Dream Server Diagnostics ━━━

Runtime Environment:
  ✓ Docker CLI
  ✓ Docker Daemon
  ✓ Docker Compose
  ✗ Dashboard HTTP
  ✗ WebUI HTTP
  ⚠ DGX Spark llama-server CUDA arch: DGX Spark detected, but llama-server reports CUDA archs '500,610,700,750,800,860,890,1200' without sm_121.

Preflight Checks:
  ✓ RAM: 16GB available
  ⚠ Disk: 50GB available (recommended: 100GB)
  ✓ GPU: NVIDIA RTX 4090 detected

Diagnoses:
  BLOCKER DS-DOCKER-IMAGE-UNRESOLVED: Docker image reference could not be resolved (high confidence)
    evidence: install-report-2026-05-20-120000.txt — Failed image: ghcr.io/example/missing:v0
    next: Check whether `ghcr.io/example/missing:v0` exists in the registry.

Summary:
  ⚠ 1 warning(s) found

Suggested Fixes:
  1. Free up disk space or add external storage
```

### JSON Mode

Raw machine-readable report for automation:
```bash
dream doctor --json > report.json
```

## Report Contents

- **capability_profile**: Hardware detection snapshot
- **preflight**: Blocker/warning analysis
- **install_artifacts**: Presence and paths for installer evidence such as
  `.env`, `.compose-flags`, `logs/compose-launch.txt`, `logs/compose-up.log`,
  and the latest `install-report-*.txt`.
- **diagnoses**: Stable, evidence-ranked install/runtime root-cause diagnoses.
  Each item includes:
  - `id`: stable issue code, for example `DS-COMPOSE-CWD-MISMATCH`
  - `severity`: `blocker`, `warn`, or `info`
  - `confidence`: evidence confidence
  - `evidence`: source file/command and observed detail
  - `impact`: why the issue matters
  - `next_steps`: concrete recovery actions
- **runtime**: Docker/Compose/UI reachability checks
- **runtime.amd_runtime**: Explicit AMD inference runtime diagnostics from
  installer-written env state. Reports runtime (`lemonade` or `llama-server`),
  host/container location, selected backend, supported backends, DreamServer
  management state, and health endpoint reachability.
- **runtime.dgx_spark_cuda_arch_check**: Warns when a DGX Spark / GB10
  machine is running a llama.cpp CUDA binary that does not report `sm_121`
  support in `llama-server` logs.
- **summary**: Aggregate status (blockers, warnings, runtime_ready)
- **autofix_hints**: Prioritized remediation actions

## Evidence-Based Install Diagnoses

Dream Doctor intentionally distinguishes a failing symptom from the evidence
that supports a root cause. For example, a generic `docker compose up` failure
can mean a missing image, wrong working directory, missing `.env`, or a resolver
dependency problem. The `diagnoses` array records the specific cause only when
the saved install artifacts support it.

Current install diagnoses include:

- `DS-INSTALL-ENV-MISSING`: an installed-looking tree is missing its generated
  `.env`.
- `DS-COMPOSE-CWD-MISMATCH`: `logs/compose-launch.txt` says compose was launched
  from a different directory than the Doctor root.
- `DS-DOCKER-IMAGE-UNRESOLVED`: saved install logs show an image tag that Docker
  could not resolve.
- `DS-COMPOSE-ZERO-CONTAINERS`: compose completed but no managed DreamServer
  containers were created.
- `DS-PYTHON-PYYAML-MISSING`: the selected installer Python could not import
  PyYAML.
- `DS-WINDOWS-FILE-SHARING-PROBE-IMAGE`: Windows file-sharing probe evidence is
  mixed with an Alpine probe image pull failure, so the report calls out both
  prerequisites.

These diagnoses are additive. Existing `autofix_hints`, `runtime`, and
`preflight` fields remain available for scripts that already consume Doctor
JSON.

## Exit Codes

- `0`: All checks passed (or warnings only)
- `1`: Blockers found or runtime failures detected

Use in scripts:
```bash
if dream doctor; then
    echo "System healthy"
else
    echo "Issues detected, check output"
fi
```

## Integration

The doctor command integrates with:
- `scripts/build-capability-profile.sh` - Hardware detection
- `scripts/preflight-engine.sh` - Requirement validation
- Service registry - Port resolution
- AMD runtime contract - ROCm on Linux container installs, Vulkan on Windows
  host-managed installs. Modern Lemonade CLI/port 13305 is tracked as a
  follow-up and is not auto-detected from inside dashboard containers.

## Default Report Path

`/tmp/dream-doctor-report.json`
