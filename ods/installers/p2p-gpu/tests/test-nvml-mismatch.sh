#!/usr/bin/env bash
# Regression: ensure NVML mismatch repair path is reachable under set -e.
set -euo pipefail

P2P_GPU_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGFILE="$(mktemp -t p2p-gpu-nvml.XXXXXX)"
STUB_DIR="$(mktemp -d -t p2p-gpu-stub.XXXXXX)"
APT_CALLED_FILE="${STUB_DIR}/apt-called"
trap 'rm -f "$LOGFILE"; rm -rf "$STUB_DIR"' EXIT

# Minimal logging functions expected by environment.sh
log() { :; }
warn() { :; }
err() { :; }
step() { :; }

assert_no_apt_call() {
  if [[ -e "$APT_CALLED_FILE" ]]; then
    echo "Expected repair path to skip apt-get" >&2
    exit 1
  fi
}

# shellcheck source=../lib/environment.sh
source "${P2P_GPU_DIR}/lib/environment.sh"

# Force mismatch status to validate repair path.
detect_nvml_mismatch() {
  return 1
}

export PATH="${STUB_DIR}:${PATH}"
export APT_CALLED_FILE

cat >"${STUB_DIR}/apt-get" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "called" >> "${APT_CALLED_FILE}"
exit 0
EOF

cat >"${STUB_DIR}/systemctl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exit 0
EOF

cat >"${STUB_DIR}/service" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exit 0
EOF

chmod +x "${STUB_DIR}/apt-get" "${STUB_DIR}/systemctl" "${STUB_DIR}/service"

sleep() { :; }

if repair_nvml_mismatch; then
  repair_status=0
else
  repair_status=$?
fi

if [[ "$repair_status" -ne 1 ]]; then
  echo "Expected repair_nvml_mismatch to return 1 when mismatch persists" >&2
  exit 1
fi

if [[ ! -s "$APT_CALLED_FILE" ]]; then
  echo "Expected repair path to invoke apt-get for NVML mismatch" >&2
  exit 1
fi

rm -f "$APT_CALLED_FILE"

detect_nvml_mismatch() {
  return 2
}

if repair_nvml_mismatch; then
  repair_status=0
else
  repair_status=$?
fi

if [[ "$repair_status" -ne 1 ]]; then
  echo "Expected repair_nvml_mismatch to return 1 when detection fails" >&2
  exit 1
fi

assert_no_apt_call
