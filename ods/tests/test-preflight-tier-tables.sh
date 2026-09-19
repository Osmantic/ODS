#!/usr/bin/env bash
# Copyright (C) 2026 Lingga Louis Channels
# Licensed under the GNU Affero General Public License v3.0 (AGPLv3)
#
# Regression test: scripts/preflight-engine.sh must recognize every tier
# the installer can detect. NV_ULTRA/ARC/ARC_LITE were missing from its
# tables and SH_LARGE was ranked 4 instead of 5, so an NV_ULTRA machine
# fell back to tier-1 defaults: a 64GB box "met" a 16GB RAM floor that
# phase 04 sets at 96GB, and the nvidia VRAM gate (tier_rank >= 2) was
# skipped.
#
# Canonical sources:
#   installers/lib/detection.sh        tier_rank()
#   installers/phases/04-requirements.sh  MIN_RAM case table

set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_ROOT="$(cd "${TEST_DIR}/.." && pwd)"
ENGINE="${ODS_ROOT}/scripts/preflight-engine.sh"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

# run_engine <name> <tier> <ram_gb> <disk_gb> <vram_mb>
run_engine() {
    local name="$1" tier="$2" ram="$3" disk="$4" vram="$5"
    local report="$TMP_ROOT/${name}.json"
    bash "$ENGINE" \
        --report "$report" \
        --tier "$tier" \
        --ram-gb "$ram" \
        --disk-gb "$disk" \
        --gpu-backend nvidia \
        --gpu-vram-mb "$vram" \
        --gpu-name "Test GPU" \
        --platform-id linux \
        --script-dir "$ODS_ROOT" >/dev/null
    python3 - "$report" <<'PY'
import json, sys
report = json.load(open(sys.argv[1]))
for c in report["checks"]:
    print(f"{c['id']}={c['status']}")
PY
}

status() { grep "^$1=" | cut -d= -f2; }

echo "=== preflight-engine tier table coverage ==="

# Case 1: NV_ULTRA with 64GB RAM — below the 96GB floor that phase 04
# applies, so the memory check must warn, not pass at the tier-1 floor.
run_engine nvu64 NV_ULTRA 64 500 96000 >"$TMP_ROOT/c1.txt"
[[ "$(status memory <"$TMP_ROOT/c1.txt")" == "warn" ]] \
    || fail "NV_ULTRA/64GB memory check = $(status memory <"$TMP_ROOT/c1.txt"), want warn (floor is 96GB)"
echo "PASS: NV_ULTRA RAM floor is 96GB"

# Case 2: NV_ULTRA with 128GB RAM — meets the floor, must pass.
run_engine nvu128 NV_ULTRA 128 500 96000 >"$TMP_ROOT/c2.txt"
[[ "$(status memory <"$TMP_ROOT/c2.txt")" == "pass" ]] \
    || fail "NV_ULTRA/128GB memory check = $(status memory <"$TMP_ROOT/c2.txt"), want pass"
echo "PASS: NV_ULTRA with adequate RAM passes"

# Case 3: NV_ULTRA rank >= 2 reaches the nvidia VRAM gate — 8GB VRAM
# under a rank-5 tier must warn, not report a clean pass.
run_engine nvu-vram NV_ULTRA 128 500 8192 >"$TMP_ROOT/c3.txt"
[[ "$(status gpu-vram <"$TMP_ROOT/c3.txt")" == "warn" ]] \
    || fail "NV_ULTRA/8GB-VRAM check = $(status gpu-vram <"$TMP_ROOT/c3.txt"), want warn"
echo "PASS: NV_ULTRA hits the nvidia VRAM gate"

# Case 4: ordinary tiers unchanged — tier 2 with 20GB warns at the 32GB
# floor, tier 1 with 16GB passes.
run_engine t2-low 2 20 500 16384 >"$TMP_ROOT/c4.txt"
[[ "$(status memory <"$TMP_ROOT/c4.txt")" == "warn" ]] \
    || fail "tier 2/20GB memory check = $(status memory <"$TMP_ROOT/c4.txt"), want warn"
run_engine t1-ok 1 16 500 8192 >"$TMP_ROOT/c5.txt"
[[ "$(status memory <"$TMP_ROOT/c5.txt")" == "pass" ]] \
    || fail "tier 1/16GB memory check = $(status memory <"$TMP_ROOT/c5.txt"), want pass"
echo "PASS: numeric tiers unchanged"

echo "PASS: all preflight tier-table cases"
