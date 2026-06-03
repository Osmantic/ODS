#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_CMD="python3"
if [[ -f "$ROOT_DIR/lib/python-cmd.sh" ]]; then
  . "$ROOT_DIR/lib/python-cmd.sh"
  PYTHON_CMD="$(ds_detect_python_cmd)"
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD="python"
fi

echo "[gate] shell syntax"
mapfile -t sh_files < <(git ls-files '*.sh')
for f in "${sh_files[@]}"; do
  bash -n "$f"
done

echo "[gate] compatibility + claims"
bash scripts/check-compatibility.sh
"$PYTHON_CMD" scripts/check-version-consistency.py
bash scripts/check-release-claims.sh
"$PYTHON_CMD" scripts/validate-golden-paths.py
"$PYTHON_CMD" scripts/validate-generated-configs.py
"$PYTHON_CMD" scripts/check-dependency-pins.py

echo "[gate] contracts"
bash tests/contracts/test-installer-contracts.sh
bash tests/contracts/test-preflight-fixtures.sh
bash tests/contracts/test-installer-hardening.sh
bash tests/test-uninstall-compose-flags.sh
bash tests/test-windows-missing-service-hints.sh
"$PYTHON_CMD" tests/contracts/test-network-exposure-contracts.py

echo "[gate] smoke"
bash tests/smoke/linux-amd.sh
bash tests/smoke/linux-nvidia.sh
bash tests/smoke/wsl-logic.sh
bash tests/smoke/macos-dispatch.sh

echo "[gate] installer simulation"
bash scripts/simulate-installers.sh
"$PYTHON_CMD" scripts/validate-sim-summary.py artifacts/installer-sim/summary.json

echo "[gate] update rollback"
bash tests/test-update-rollback-contract.sh

echo "[PASS] release gate"
