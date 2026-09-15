#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

# Prefer jq; fall back to Python so minimal dev images can run contracts.
json_summary_blockers() {
  local f="$1"
  if command -v jq >/dev/null 2>&1; then
    jq -r '.summary.blockers' "$f"
  else
    python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["summary"]["blockers"])' "$f"
  fi
}

assert_eq() {
  local got="$1"
  local expected="$2"
  local msg="$3"
  if [[ "$got" != "$expected" ]]; then
    echo "[FAIL] $msg (expected=$expected got=$got)"
    exit 1
  fi
}

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

echo "[contract] preflight fixture: linux-nvidia-good"
scripts/preflight-engine.sh \
  --report "$tmpdir/linux-nvidia-good.json" \
  --tier T2 \
  --ram-gb 64 \
  --disk-gb 200 \
  --gpu-backend nvidia \
  --gpu-vram-mb 24576 \
  --gpu-name "RTX 4090" \
  --platform-id linux \
  --compose-overlays docker-compose.base.yml,docker-compose.nvidia.yml \
  --script-dir "$ROOT_DIR" \
  --env >/dev/null
blockers="$(json_summary_blockers "$tmpdir/linux-nvidia-good.json")"
assert_eq "$blockers" "0" "linux-nvidia-good blockers"

echo "[contract] preflight fixture: windows-mvp-good"
scripts/preflight-engine.sh \
  --report "$tmpdir/windows-mvp-good.json" \
  --tier T1 \
  --ram-gb 16 \
  --disk-gb 120 \
  --gpu-backend nvidia \
  --gpu-vram-mb 12288 \
  --gpu-name "RTX 3060" \
  --platform-id windows \
  --compose-overlays docker-compose.base.yml,docker-compose.nvidia.yml \
  --script-dir "$ROOT_DIR" \
  --env >/dev/null
blockers="$(json_summary_blockers "$tmpdir/windows-mvp-good.json")"
assert_eq "$blockers" "0" "windows-mvp-good blockers"

# macOS: the installer requires Apple Silicon. An empty --host-arch (what the
# Linux CI simulation of installers/macos.sh passes) must not add a blocker.
for macos_case in "macos-arm64-good:arm64:0" "macos-intel-blocked:x86_64:1" "macos-arch-unknown::0"; do
  IFS=: read -r case_name host_arch expected <<<"$macos_case"
  echo "[contract] preflight fixture: $case_name"
  scripts/preflight-engine.sh \
    --report "$tmpdir/$case_name.json" \
    --tier T1 \
    --ram-gb 16 \
    --disk-gb 120 \
    --gpu-backend apple \
    --gpu-vram-mb 0 \
    --gpu-name "Apple Silicon" \
    --platform-id macos \
    --host-arch "$host_arch" \
    --compose-overlays docker-compose.base.yml,docker-compose.amd.yml \
    --script-dir "$ROOT_DIR" \
    --env >/dev/null
  blockers="$(json_summary_blockers "$tmpdir/$case_name.json")"
  assert_eq "$blockers" "$expected" "$case_name blockers"
done

echo "[contract] preflight fixture: macos-mvp-good"
scripts/preflight-engine.sh \
  --report "$tmpdir/macos-mvp-good.json" \
  --tier T1 \
  --ram-gb 16 \
  --disk-gb 80 \
  --gpu-backend apple \
  --gpu-vram-mb 16384 \
  --gpu-name "Apple Silicon" \
  --platform-id macos \
  --compose-overlays docker-compose.base.yml,docker-compose.amd.yml \
  --script-dir "$ROOT_DIR" \
  --env >/dev/null
blockers="$(json_summary_blockers "$tmpdir/macos-mvp-good.json")"
assert_eq "$blockers" "0" "macos-mvp-good blockers"

echo "[contract] preflight fixture: disk-blocker"
scripts/preflight-engine.sh \
  --report "$tmpdir/disk-blocker.json" \
  --tier T3 \
  --ram-gb 64 \
  --disk-gb 20 \
  --gpu-backend nvidia \
  --gpu-vram-mb 24576 \
  --gpu-name "RTX 4090" \
  --platform-id linux \
  --compose-overlays docker-compose.base.yml,docker-compose.nvidia.yml \
  --script-dir "$ROOT_DIR" \
  --env >/dev/null
blockers="$(json_summary_blockers "$tmpdir/disk-blocker.json")"
if [[ "$blockers" -lt 1 ]]; then
  echo "[FAIL] disk-blocker expected >=1 blocker, got $blockers"
  exit 1
fi

echo "[contract] preflight fixture: cloud-low-storage-good"
scripts/preflight-engine.sh \
  --report "$tmpdir/cloud-low-storage-good.json" \
  --tier CLOUD \
  --ram-gb 8 \
  --disk-gb 44 \
  --gpu-backend cpu \
  --gpu-vram-mb 0 \
  --gpu-name "None" \
  --platform-id linux \
  --compose-overlays docker-compose.base.yml \
  --script-dir "$ROOT_DIR" \
  --env >/dev/null
blockers="$(json_summary_blockers "$tmpdir/cloud-low-storage-good.json")"
assert_eq "$blockers" "0" "cloud-low-storage-good blockers"

echo "[contract] preflight fixture: cloud-disk-blocker"
scripts/preflight-engine.sh \
  --report "$tmpdir/cloud-disk-blocker.json" \
  --tier CLOUD \
  --ram-gb 8 \
  --disk-gb 20 \
  --gpu-backend cpu \
  --gpu-vram-mb 0 \
  --gpu-name "None" \
  --platform-id linux \
  --compose-overlays docker-compose.base.yml \
  --script-dir "$ROOT_DIR" \
  --env >/dev/null
blockers="$(json_summary_blockers "$tmpdir/cloud-disk-blocker.json")"
if [[ "$blockers" -lt 1 ]]; then
  echo "[FAIL] cloud-disk-blocker expected >=1 blocker, got $blockers"
  exit 1
fi

echo "[PASS] preflight fixture contracts"
