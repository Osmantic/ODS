#!/usr/bin/env bash
# Regression: amd-topo.sh read sysfs attributes and tool output verbatim.
# `$(cat ...)` strips the trailing newline but keeps a stray CR or padding
# (CRLF-producing tooling, rocm-smi lines with trailing spaces), so values
# like "gfx942\r" or "0x1234\r\n" leaked into GPU ids, gfx versions, and
# TSV/JSON rows — breaking downstream comparisons and column alignment.
# Every public getter must emit CR/whitespace-trimmed values.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB="$ROOT_DIR/installers/lib/amd-topo.sh"

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fakebin="$tmp/bin"
mkdir -p "$fakebin"

card="$tmp/card0"
mkdir -p "$card/ip_discovery/die/0/GC/0"

run() {
    PATH="$fakebin:$PATH" bash -c '
        set -euo pipefail
        . "$1"
        shift
        "$@"
    ' _ "$LIB" "$@"
}

# ── sysfs unique_id with CRLF → trimmed GPU id ─────────────────────────────
printf '0x00abcdef12\r\n' > "$card/unique_id"
out="$(run amd_gpu_id "$card" 0)"
[[ "$out" == "AMD-UID-0x00abcdef12" ]] \
    || fail "amd_gpu_id kept CR/padding in unique_id: [$out]"
pass "amd_gpu_id trims sysfs unique_id"

# ── ip_discovery fields with CR → clean gfx string ──────────────────────────
printf '9\r\n'  > "$card/ip_discovery/die/0/GC/0/major"
printf '4\r\n'  > "$card/ip_discovery/die/0/GC/0/minor"
printf '2\r\n'  > "$card/ip_discovery/die/0/GC/0/revision"
out="$(run amd_gfx_version "$card" 0)"
[[ "$out" == "gfx942" ]] \
    || fail "amd_gfx_version kept CR in ip_discovery fields: [$out]"
pass "amd_gfx_version trims ip_discovery fields"

# ── rocm-smi output with trailing spaces/CR → trimmed ───────────────────────
rm -rf "$card/ip_discovery"
cat > "$fakebin/rocm-smi" <<'EOF'
#!/usr/bin/env bash
printf 'GPU[0]          GFX Version: gfx1100 \r\n'
EOF
chmod +x "$fakebin/rocm-smi"
out="$(run amd_gfx_version "$card" 0)"
[[ "$out" == "gfx1100" ]] \
    || fail "amd_gfx_version kept padding in rocm-smi output: [$out]"
pass "amd_gfx_version trims rocm-smi output"

# ── product_name with trailing whitespace → trimmed ────────────────────────
printf 'AMD Radeon RX 7900 XTX  \r\n' > "$card/product_name"
out="$(run amd_gpu_name "$card" 74a1)"
[[ "$out" == "AMD Radeon RX 7900 XTX" ]] \
    || fail "amd_gpu_name kept trailing whitespace: [$out]"
pass "amd_gpu_name trims sysfs product_name"

# ── device/subsystem ids with CR → clean composite id ──────────────────────
rm -f "$card/unique_id" "$card/product_name"
printf '74a1\r\n' > "$card/device"
printf '1649\r\n' > "$card/subsystem_device"
out="$(run amd_gpu_id "$card" 0)"
[[ "$out" =~ ^AMD-[a-zA-Z0-9:.]+-74a1-1649$ ]] \
    || fail "amd_gpu_id kept CR in composite id: [$out]"
pass "amd_gpu_id trims sysfs device/subsystem ids"

echo "All amd-topo whitespace-trim checks passed."
