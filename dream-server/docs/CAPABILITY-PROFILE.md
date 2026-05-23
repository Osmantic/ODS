# Capability Profile Contract

Dream Server now exposes a normalized installer capability profile so platform and hardware decisions are not scattered through installer code.

## Contract

- Schema: `config/capability-profile.schema.json`
- Generator: `scripts/build-capability-profile.sh`
- Default output: `.capabilities.json` in repo root
- Installer runtime output: `/tmp/dream-server-capabilities.json` (override with `CAPABILITY_PROFILE_FILE`)

## Generate

```bash
scripts/build-capability-profile.sh --output /tmp/dream-server-capabilities.json
```

For shell-driven installers:

```bash
source lib/safe-env.sh
CAPABILITY_ENV="$(scripts/build-capability-profile.sh --env)"
load_env_from_output <<< "$CAPABILITY_ENV"
```

This exports:

- `CAP_PLATFORM_ID`, `CAP_PLATFORM_FAMILY`
- `CAP_GPU_VENDOR`, `CAP_GPU_NAME`, `CAP_GPU_MEMORY_TYPE`, `CAP_GPU_COUNT`, `CAP_GPU_VRAM_MB`
- `CAP_LLM_BACKEND`, `CAP_LLM_HEALTH_URL`, `CAP_LLM_API_PORT`
- `CAP_RECOMMENDED_TIER`
- `CAP_COMPOSE_OVERLAYS`
- `CAP_HARDWARE_CLASS_ID`, `CAP_HARDWARE_CLASS_LABEL`

## Current installer use

`install-core.sh` now consumes this profile for:

- tier recommendation normalization (`T1..T4`, `SH_*`)
- backend/memory overrides (`nvidia` vs `amd`)
- compose overlay selection (`base+nvidia` or `base+amd`) with legacy fallback
- LLM health endpoint selection for AMD paths
- installer preflight evaluation via `scripts/preflight-engine.sh`
