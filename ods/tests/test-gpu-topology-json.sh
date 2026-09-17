#!/usr/bin/env bash
# Public CLI contract for machine-readable NVIDIA, AMD, and Apple topology.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

INSTALL_DIR="$TMP_DIR/install"
STUB_BIN="$TMP_DIR/bin"
mkdir -p "$INSTALL_DIR/config" "$STUB_BIN"
touch "$INSTALL_DIR/docker-compose.base.yml"

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
pass() { printf 'PASS: %s\n' "$1"; }

cat > "$STUB_BIN/nvidia-smi" <<'STUB'
#!/usr/bin/env bash
if [[ "${1:-}" == "--list-gpus" ]]; then
    printf '%s\n' 'GPU 0: Example NVIDIA GPU (UUID: GPU-example)'
fi
STUB

cat > "$STUB_BIN/sysctl" <<'STUB'
#!/usr/bin/env bash
case "$*" in
    *machdep.cpu.brand_string*) printf '%s\n' 'Apple M3 Max' ;;
    *hw.memsize*) printf '%s\n' '68719476736' ;;
esac
STUB

cat > "$STUB_BIN/system_profiler" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' '{"SPDisplaysDataType":[{"sppci_cores":"40"}]}'
STUB
chmod +x "$STUB_BIN"/*

run_cli() {
    ODS_HOME="$INSTALL_DIR" PATH="$STUB_BIN:$PATH" \
        bash "$ROOT_DIR/ods-cli" gpu topology --json
}

cat > "$INSTALL_DIR/config/gpu-topology.json" <<'JSON'
{"gpu_count":1,"driver_version":"600.1","gpus":[{"index":0,"name":"NVIDIA Test"}],"links":[],"numa":{"nodes":1}}
JSON
printf '%s\n' 'GPU_BACKEND=nvidia' > "$INSTALL_DIR/.env"
nvidia_json=$(run_cli)
jq -e '
    .schema_version == "ods.gpu-topology.v1" and
    .backend == "nvidia" and
    .source == "cache" and
    .gpu_count == 1 and
    .gpus[0].name == "NVIDIA Test"
' <<< "$nvidia_json" >/dev/null || fail "NVIDIA topology JSON contract changed"
pass "NVIDIA cached topology is exposed as JSON"

cat > "$INSTALL_DIR/config/gpu-topology.json" <<'JSON'
{"vendor":"amd","gpu_count":2,"driver_version":"7.0","gpus":[{"index":0,"name":"AMD A"},{"index":1,"name":"AMD B"}],"links":[],"numa":{"nodes":1}}
JSON
printf '%s\n' 'GPU_BACKEND=amd' > "$INSTALL_DIR/.env"
amd_json=$(run_cli)
jq -e '
    .schema_version == "ods.gpu-topology.v1" and
    .backend == "amd" and
    .source == "cache" and
    .gpu_count == 2 and
    [.gpus[].name] == ["AMD A", "AMD B"]
' <<< "$amd_json" >/dev/null || fail "AMD topology JSON contract changed"
pass "AMD cached topology is exposed as JSON"

printf '%s\n' 'GPU_BACKEND=apple' > "$INSTALL_DIR/.env"
apple_json=$(run_cli)
jq -e '
    .schema_version == "ods.gpu-topology.v1" and
    .backend == "apple" and
    .source == "live" and
    .integrated == true and
    .gpu_count == 1 and
    .gpus[0].name == "Apple M3 Max" and
    .gpus[0].memory_bytes == 68719476736 and
    .gpus[0].gpu_cores == 40 and
    .links == [] and
    .numa.nodes == 1
' <<< "$apple_json" >/dev/null || fail "Apple topology JSON contract changed"
pass "Apple integrated topology is exposed as JSON"

cat > "$STUB_BIN/nvidia-smi" <<'STUB'
#!/usr/bin/env bash
exit 1
STUB
chmod +x "$STUB_BIN/nvidia-smi"
rm -f "$INSTALL_DIR/config/gpu-topology.json"
printf '%s\n' 'GPU_BACKEND=nvidia' > "$INSTALL_DIR/.env"
if broken_json=$(run_cli 2>/dev/null); then
    fail "JSON mode succeeded without the NVIDIA telemetry boundary"
fi
[[ -z "$broken_json" ]] || fail "JSON failure polluted stdout with non-JSON output"
pass "topology JSON fails cleanly when live NVIDIA detection is unavailable"
