#!/usr/bin/env bash
# Run the real CLI with vendor tools installed for every configured backend.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT_DIR/ods-cli"
FIXTURE=$(mktemp -d)
trap 'rm -rf "$FIXTURE"' EXIT
mkdir -p "$FIXTURE/install" "$FIXTURE/bin"
: > "$FIXTURE/install/docker-compose.base.yml"
export NVIDIA_CALLS="$FIXTURE/nvidia-calls"

cat > "$FIXTURE/bin/nvidia-smi" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$NVIDIA_CALLS"
case "$*" in
    --list-gpus) echo 'GPU 0: Test NVIDIA GPU' ;;
    *query-gpu=index,name*) echo '0, Test NVIDIA GPU, 1024, 8192, 25, 50, 75' ;;
    *) echo 'Test NVIDIA GPU, 25, 1024, 8192, 50' ;;
esac
STUB
cat > "$FIXTURE/bin/docker" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
cat > "$FIXTURE/bin/curl" <<'STUB'
#!/usr/bin/env bash
exit 1
STUB
cat > "$FIXTURE/bin/sysctl" <<'STUB'
#!/usr/bin/env bash
case "$*" in
    *hw.memsize*) echo 34359738368 ;;
    *machdep.cpu.brand_string*) echo 'Apple Test Chip' ;;
esac
STUB
cat > "$FIXTURE/bin/system_profiler" <<'STUB'
#!/usr/bin/env bash
[[ "${FAIL_APPLE_PROBE:-0}" == 0 ]] || exit 1
echo '{"SPDisplaysDataType":[{"sppci_cores":"16"}]}'
STUB
chmod +x "$FIXTURE/bin/"*

fail() { echo "[FAIL] $*" >&2; exit 1; }
run_cli() {
    INSTALL_DIR="$FIXTURE/install" PATH="$FIXTURE/bin:$PATH" bash "$CLI" "$@"
}

for backend in amd apple cpu intel arc; do
    printf 'GPU_BACKEND=%s\n' "$backend" > "$FIXTURE/install/.env"
    : > "$NVIDIA_CALLS"
    output=$(run_cli status)
    if [[ "$backend" == amd ]]; then
        [[ "$output" == *'VRAM Used/Total'* ]] || fail 'AMD status did not show its GPU report'
    elif [[ "$backend" == apple ]]; then
        [[ "$output" == *'Apple Test Chip'* ]] || fail 'Apple status did not show its GPU report'
    fi
    summary=$(run_cli status --json)
    if [[ "$backend" == apple ]]; then
        jq -e '.gpu.backend == "apple" and .gpu.unified_memory_gb == 32' <<< "$summary" >/dev/null
    else
        jq -e '.gpu == null' <<< "$summary" >/dev/null
    fi
    if [[ "$backend" == amd || "$backend" == apple ]]; then
        run_cli gpu status > "$FIXTURE/gpu-status"
    fi
    [[ ! -s "$NVIDIA_CALLS" ]] || fail "$backend invoked nvidia-smi: $(cat "$NVIDIA_CALLS")"
    echo "[PASS] $backend status avoids NVIDIA tooling"
done

printf 'GPU_BACKEND=apple\n' > "$FIXTURE/install/.env"
: > "$NVIDIA_CALLS"
output=$(FAIL_APPLE_PROBE=1 run_cli status)
[[ "$output" == *'Apple Test Chip'* && "$output" == *'GPU cores:        ?'* ]] \
    || fail 'Apple status did not degrade gracefully when its GPU probe failed'
[[ ! -s "$NVIDIA_CALLS" ]] || fail 'Apple probe failure invoked NVIDIA tooling'
echo '[PASS] Apple status survives an unavailable GPU probe'

# Exercise the production AMD reporter with a private DRM tree. Intercept
# reads after the existence checks to model a disappearing device or sensor.
mkdir -p "$FIXTURE/drm/card0/device/hwmon/hwmon0" "$FIXTURE/drm/card1/device"
printf '0x1002\n' > "$FIXTURE/drm/card0/device/vendor"
printf '0x8086\n' > "$FIXTURE/drm/card1/device/vendor"
printf 'Test AMD GPU\n' > "$FIXTURE/drm/card0/device/product_name"
printf '8589934592\n' > "$FIXTURE/drm/card0/device/mem_info_vram_total"
printf '1073741824\n' > "$FIXTURE/drm/card0/device/mem_info_vram_used"
printf '25\n' > "$FIXTURE/drm/card0/device/gpu_busy_percent"
printf 'junction\n' > "$FIXTURE/drm/card0/device/hwmon/hwmon0/temp1_label"
printf '45000\n' > "$FIXTURE/drm/card0/device/hwmon/hwmon0/temp1_input"
sed -n '/^_gpu_status() {/,/^}/p' "$CLI" \
    | sed "s|/sys/class/drm|$FIXTURE/drm|g" > "$FIXTURE/amd-reporter.sh"
for probe in normal vendor sensor; do
    rm -f "$FIXTURE/vendor-read"
    (
        set -euo pipefail
        BLUE='' NC='' GPU_BACKEND=amd
        check_install() { :; }
        load_env() { :; }
        cat() {
            if [[ "$probe" == vendor && "$1" == "$FIXTURE/drm/card0/device/vendor" ]]; then
                [[ ! -e "$FIXTURE/vendor-read" ]] || return 1
                : > "$FIXTURE/vendor-read"
            elif [[ "$probe" == sensor && "$1" == */temp1_label ]]; then
                return 1
            fi
            command cat "$@"
        }
        source "$FIXTURE/amd-reporter.sh"
        _gpu_status
    ) > "$FIXTURE/amd-output"
    grep -q 'GPU Status (1 GPU)' "$FIXTURE/amd-output" || fail 'AMD count included a non-AMD device'
    if [[ "$probe" != vendor ]]; then
        grep -q 'Test AMD GPU.*1.0 / 8.0 GB' "$FIXTURE/amd-output" || fail 'AMD metrics were lost'
    fi
    echo "[PASS] AMD reporting tolerates $probe probe state"
done

printf 'GPU_BACKEND=nvidia\n' > "$FIXTURE/install/.env"
output=$(run_cli status)
[[ "$output" == *'Test NVIDIA GPU: 25% GPU | 1024MB/8192MB VRAM | 50°C'* ]] \
    || fail 'NVIDIA status did not format GPU metrics correctly'
output=$(run_cli gpu status)
[[ "$output" == *'GPU Status (1 GPU)'* && "$output" == *'1.0 / 8.0 GB'* ]] \
    || fail 'NVIDIA GPU status did not count or convert VRAM correctly'
echo '[PASS] NVIDIA status retains GPU metrics and device counting'
