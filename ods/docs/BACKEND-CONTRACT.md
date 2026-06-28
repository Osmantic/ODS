# Backend Runtime Contract

ODS now defines backend runtime behavior in contract files instead of hardcoded installer branches.

## Contract Files

- `config/backends/amd.json`
- `config/backends/nvidia.json`
- `config/backends/cpu.json`
- `config/backends/apple.json`

Each contract defines:

- LLM engine/service name
- public API port and health URL
- OpenClaw provider name + internal provider URL

## Loader

- `scripts/load-backend-contract.sh`

Example:

```bash
source lib/safe-env.sh
BACKEND_ENV="$(scripts/load-backend-contract.sh --backend amd --env)"
load_env_from_output <<< "$BACKEND_ENV"
echo "$BACKEND_PUBLIC_HEALTH_URL $BACKEND_PROVIDER_URL"
```

## Installer Integration

The modular installer loads backend contracts in `installers/lib/detection.sh` via `load_backend_contract()`. Contract values drive:

- runtime health-check endpoint selection (`installers/phases/12-health.sh`)
- OpenClaw provider wiring (`installers/phases/06-directories.sh`)
- LLM API summary endpoint (`installers/phases/13-summary.sh`)

See [INSTALLER-ARCHITECTURE.md](INSTALLER-ARCHITECTURE.md) for the full module map.
