# ODS Doctor

Diagnostics command for ODS installation and runtime health checks.

## Usage

### Via ods-cli (Recommended)

```bash
# Run diagnostics with operator-friendly output
ods doctor

# Get raw JSON report
ods doctor --json

# Save report to custom location
ods doctor --report /path/to/report.json
```

### Direct Script Invocation

```bash
scripts/ods-doctor.sh
scripts/ods-doctor.sh /tmp/custom-ods-doctor.json
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
━━━ ODS Diagnostics ━━━

Runtime Environment:
  ✓ Docker CLI
  ✓ Docker Daemon
  ✓ Docker Compose
  ✗ Dashboard HTTP
  ✗ WebUI HTTP
  ✓ Inference contract: mode=local, owner=ods, gateway=llama-server
  ⚠ DGX Spark llama-server CUDA arch: DGX Spark detected, but llama-server reports CUDA archs '500,610,700,750,800,860,890,1200' without sm_121.

Preflight Checks:
  ✓ RAM: 16GB available
  ⚠ Disk: 50GB available (recommended: 100GB)
  ✓ GPU: NVIDIA RTX 4090 detected

Diagnoses:
  BLOCKER ODS-DOCKER-IMAGE-UNRESOLVED: Docker image reference could not be resolved (high confidence)
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
ods doctor --json > report.json
```

## Report Contents

- **capability_profile**: Hardware detection snapshot
- **preflight**: Blocker/warning analysis
  - Installer preflight requires the full tier disk floor before images and
    models are downloaded.
  - Doctor evaluates an already-installed runtime: falling below the tier's
    install recommendation is a warning, while less than 10GB free remains a
    blocker so updates, logs, and container writes cannot silently exhaust the
    filesystem.
- **install_artifacts**: Presence and paths for installer evidence such as
  `.env`, `.compose-flags`, `logs/compose-launch.txt`, `logs/compose-up.log`,
  and the latest `install-report-*.txt`.
- **diagnoses**: Stable, evidence-ranked install/runtime root-cause diagnoses.
  Each item includes:
  - `id`: stable issue code, for example `ODS-COMPOSE-CWD-MISMATCH`
  - `severity`: `blocker`, `warn`, or `info`
  - `confidence`: evidence confidence
  - `evidence`: source file/command and observed detail
  - `impact`: why the issue matters
  - `next_steps`: concrete recovery actions
- **runtime**: Docker/Compose/UI reachability checks
- **runtime.inference_contract**: A compact routing contract for the active
  inference mode. It records `ODS_MODE`, expected inference owner
  (`ods` vs `external`), expected gateway (`llama-server` vs
  `litellm`), key LLM URLs, resolved compose files, and stable mismatch IDs.
- **runtime.amd_runtime**: Explicit AMD inference runtime diagnostics from
  installer-written env state. Reports runtime (`llama-server`),
  host/container location, selected backend, supported backends, ODS
  management state, and health endpoint reachability. An `.env` that still
  names the retired `lemonade` runtime is reported as retired.
- **runtime.dgx_spark_cuda_arch_check**: Warns when a DGX Spark / GB10
  machine is running a llama.cpp CUDA binary that does not report `sm_121`
  support in `llama-server` logs.
- **summary**: Aggregate status (blockers, warnings, runtime_ready)
- **autofix_hints**: Prioritized remediation actions

## Evidence-Based Install Diagnoses

ODS Doctor intentionally distinguishes a failing symptom from the evidence
that supports a root cause. For example, a generic `docker compose up` failure
can mean a missing image, wrong working directory, missing `.env`, or a resolver
dependency problem. The `diagnoses` array records the specific cause only when
the saved install artifacts support it.

Current install diagnoses include:

- `ODS-INSTALL-ENV-MISSING`: an installed-looking tree is missing its generated
  `.env`.
- `ODS-COMPOSE-CWD-MISMATCH`: `logs/compose-launch.txt` says compose was launched
  from a different directory than the Doctor root.
- `ODS-DOCKER-IMAGE-UNRESOLVED`: saved install logs show an image tag that Docker
  could not resolve.
- `ODS-COMPOSE-ZERO-CONTAINERS`: compose completed but no managed ODS
  containers were created.
- `ODS-PYTHON-PYYAML-MISSING`: the selected installer Python could not import
  PyYAML.
- `ODS-WINDOWS-FILE-SHARING-PROBE-IMAGE`: Windows file-sharing probe evidence is
  mixed with an Alpine probe image pull failure, so the report calls out both
  prerequisites.

These diagnoses are additive. Existing `autofix_hints`, `runtime`, and
`preflight` fields remain available for scripts that already consume Doctor
JSON.

## Inference Contract Diagnoses

ODS supports several deployment shapes, but support cases often fail
when the install metadata and runtime routing disagree. For example, cloud mode
should not start or target ODS's managed `llama-server`, and the Windows
Portal's host-native `llama-server.exe` should stay outside the stack while ODS
keeps LiteLLM and model-router available for clients, Pixel, and model
switching.

ODS Doctor records those expectations under `runtime.inference_contract` and
adds diagnoses when the evidence contradicts the selected mode:

- `ODS-RUNTIME-LEMONADE-RETIRED`: `.env` still selects the retired Lemonade
  runtime (`ODS_MODE=lemonade`, `LLM_BACKEND=lemonade`,
  `LEMONADE_EXTERNAL=true` or `AMD_INFERENCE_RUNTIME=lemonade`). Rerun the
  installer to move the settings to llama.cpp; see
  [AMD GPUs now run on llama.cpp](MIGRATION-LEMONADE-TO-LLAMACPP.md).
- `ODS-RUNTIME-MODE-UNKNOWN`: `.env` contains an unrecognized `ODS_MODE`.
- `ODS-RUNTIME-CLOUD-OVERLAY-MISSING`: `ODS_MODE=cloud` but cached
  `.compose-flags` does not include `docker-compose.cloud.yml`.
- `ODS-RUNTIME-CLOUD-LLM-LOCAL-ROUTE`: cloud mode still has `LLM_API_URL`
  pointing at local `llama-server`.
- `ODS-RUNTIME-CLOUD-HERMES-LOCAL-ROUTE`: cloud mode still has Hermes pointing
  at local `llama-server`.
- `ODS-RUNTIME-CLOUD-GATEWAY-BYPASS`: cloud mode points ODS services somewhere
  other than the LiteLLM gateway.
- `ODS-RUNTIME-HOST-NATIVE-CLOUD-OVERLAY-CONFLICT`: a host-native llama-server
  (`NATIVE_LLM_BASE_URL`) is configured while cached `.compose-flags` includes
  the cloud overlay, which incorrectly profiles out model-router.
  `docker-compose.host-native-llm.yml` profiles out only the in-stack
  `llama-server`.
- `ODS-RUNTIME-HOST-NATIVE-OVERLAY-MISSING`: a host-native llama-server is
  configured while cached `.compose-flags` lacks
  `docker-compose.host-native-llm.yml`.
- `ODS-RUNTIME-HOST-NATIVE-LOCAL-ROUTE`: a host-native llama-server is
  configured but `LLM_API_URL` still points at the in-stack `llama-server`.
- `ODS-RUNTIME-HOST-NATIVE-KEY-MISSING`: `LLAMA_SERVER_API_KEY` is empty, so
  LiteLLM and model-router send no key to the Windows llama-server, which
  requires one.
- `ODS-RUNTIME-LOCAL-CLOUD-OVERLAY`: local mode still has the cloud overlay in
  cached `.compose-flags`.
- `ODS-RUNTIME-LOCAL-LITELLM-ROUTE`: managed local mode unexpectedly routes
  through LiteLLM; every GPU, AMD included, routes directly to `llama-server`.
  External (`LLM_BACKEND=external` / `EXTERNAL_LLM_URL`) and host-native
  (`NATIVE_LLM_BASE_URL`) installs expect LiteLLM and do not emit this
  warning.

The support bundle embeds the same contract evidence in
`manifest/evidence.json`. Its Compose validation resolves the stack with the
recorded `ODS_MODE`, host-native llama-server origin (`NATIVE_LLM_BASE_URL`)
and AMD image choice (`AMD_INFERENCE_BACKEND`), and records AMD runtime
ownership, so Linux, WSL, and macOS support cases show the stack the install
actually intended to run. Bundle metadata also records whether the Linux host appears to
be WSL and which Bash executable was selected for nested diagnostics.

## Exit Codes

- `0`: All checks passed (or warnings only)
- `1`: Blockers found or runtime failures detected

Use in scripts:
```bash
if ods doctor; then
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
- AMD runtime contract - llama-server on Linux container installs (Vulkan by
  default, ROCm optional) and `llama-server.exe` (Vulkan) on Windows
  host-managed installs.

## Default Report Path

`/tmp/ods-doctor-report.json`
