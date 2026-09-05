#!/bin/bash
# =============================================================================
# Test: WSL NVIDIA detection in the canonical hardware capability profiler
# =============================================================================
# WSL2 exposes the host NVIDIA GPU through nvidia-smi without a corresponding
# /sys/class/drm vendor node. Verify that a successful query is accepted only
# on WSL and that an installed-but-broken nvidia-smi cannot become a false
# hardware witness.
#
# Run: bash tests/test-wsl-nvidia-detection.sh
# =============================================================================

set -uo pipefail

ODS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DETECT_SCRIPT="$ODS_ROOT/scripts/detect-hardware.sh"
FIXTURE_DIR="$(mktemp -d -t ods-wsl-nvidia-fixture-XXXXXX)"
trap 'rm -rf "$FIXTURE_DIR"' EXIT

PASS=0
FAIL=0

assert_match() {
    local label="$1" pattern="$2" actual="$3"
    if [[ "$actual" =~ $pattern ]]; then
        echo "  PASS: $label"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $label (expected /$pattern/)"
        echo "        output: $actual"
        FAIL=$((FAIL + 1))
    fi
}

assert_no_match() {
    local label="$1" pattern="$2" actual="$3"
    if [[ "$actual" =~ $pattern ]]; then
        echo "  FAIL: $label (unexpected /$pattern/)"
        echo "        output: $actual"
        FAIL=$((FAIL + 1))
    else
        echo "  PASS: $label"
        PASS=$((PASS + 1))
    fi
}

mkdir -p "$FIXTURE_DIR/empty-drm" "$FIXTURE_DIR/good-bin" "$FIXTURE_DIR/broken-bin"
printf '%s\n' 'Linux version 6.6.87.2-microsoft-standard-WSL2' > "$FIXTURE_DIR/proc-version-wsl"
printf '%s\n' 'Linux version 6.8.0-generic' > "$FIXTURE_DIR/proc-version-native"

cat > "$FIXTURE_DIR/good-bin/nvidia-smi" <<'STUB'
#!/bin/bash
case "$*" in
    *"--query-gpu=name,memory.total"*)
        printf '%s\n' 'NVIDIA GeForce RTX 5070 Laptop GPU, 8151'
        ;;
    *"--query-gpu=pci.device_id"*)
        printf '%s\n' '0x2F1810DE'
        ;;
    *"--query-gpu=name"*)
        printf '%s\n' 'NVIDIA GeForce RTX 5070 Laptop GPU'
        ;;
    *)
        exit 0
        ;;
esac
STUB
chmod +x "$FIXTURE_DIR/good-bin/nvidia-smi"

cat > "$FIXTURE_DIR/broken-bin/nvidia-smi" <<'STUB'
#!/bin/bash
exit 1
STUB
chmod +x "$FIXTURE_DIR/broken-bin/nvidia-smi"

echo "=== WSL2 accepts a successful nvidia-smi query without DRM sysfs ==="
WSL_JSON=$(
    PATH="$FIXTURE_DIR/good-bin:$PATH" \
    ODS_PROC_VERSION_FILE="$FIXTURE_DIR/proc-version-wsl" \
    ODS_DRM_SYS="$FIXTURE_DIR/empty-drm" \
        bash "$DETECT_SCRIPT" --json-compact 2>/dev/null
)
assert_match "platform is WSL2" '"platform":[[:space:]]*"wsl2"' "$WSL_JSON"
assert_match "GPU type is NVIDIA" '"type":[[:space:]]*"nvidia"' "$WSL_JSON"
assert_match "GPU name is preserved" 'NVIDIA GeForce RTX 5070 Laptop GPU' "$WSL_JSON"
assert_match "VRAM is parsed" '"vram_mb":[[:space:]]*8151' "$WSL_JSON"
assert_match "GPU count is parsed" '"count":[[:space:]]*1' "$WSL_JSON"

echo ""
echo "=== WSL2 rejects an installed but nonfunctional nvidia-smi ==="
BROKEN_JSON=$(
    PATH="$FIXTURE_DIR/broken-bin:$PATH" \
    ODS_PROC_VERSION_FILE="$FIXTURE_DIR/proc-version-wsl" \
    ODS_DRM_SYS="$FIXTURE_DIR/empty-drm" \
        bash "$DETECT_SCRIPT" --json-compact 2>/dev/null
)
assert_no_match "broken bridge is not NVIDIA hardware" '"type":[[:space:]]*"nvidia"' "$BROKEN_JSON"

echo ""
echo "=== Native Linux still requires the sysfs hardware witness ==="
NATIVE_JSON=$(
    PATH="$FIXTURE_DIR/good-bin:$PATH" \
    ODS_PROC_VERSION_FILE="$FIXTURE_DIR/proc-version-native" \
    ODS_DRM_SYS="$FIXTURE_DIR/empty-drm" \
        bash "$DETECT_SCRIPT" --json-compact 2>/dev/null
)
assert_no_match "native empty DRM is not NVIDIA hardware" '"type":[[:space:]]*"nvidia"' "$NATIVE_JSON"

echo ""
echo "=== Summary ==="
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
[[ "$FAIL" -eq 0 ]]
