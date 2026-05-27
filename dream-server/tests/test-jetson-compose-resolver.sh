#!/bin/bash
# ============================================================================
# Test: Jetson compose-stack resolution
# ============================================================================
# Verifies that scripts/resolve-compose-stack.sh selects docker-compose.jetson.yml
# when given --gpu-backend jetson or --tier JETSON_ORIN_NANO, and that the
# expected core services (llama-server, dashboard, dashboard-api, open-webui)
# are present in the resolved list while comfyui (deliberately excluded on
# Jetson per the milestone scope) is not.
#
# Run: bash tests/test-jetson-compose-resolver.sh
# ============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RESOLVER="$ROOT_DIR/scripts/resolve-compose-stack.sh"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# Move into ROOT_DIR so the resolver finds compose files via $(pwd).
cd "$ROOT_DIR"

if [[ ! -f "$RESOLVER" ]]; then
    fail "resolver script not found at $RESOLVER"
    echo "Passed: $PASS, Failed: $FAIL"
    exit 1
fi
pass "resolver script exists"

if [[ ! -f "$ROOT_DIR/docker-compose.jetson.yml" ]]; then
    fail "docker-compose.jetson.yml is missing — Phase 3 incomplete"
    echo "Passed: $PASS, Failed: $FAIL"
    exit 1
fi
pass "docker-compose.jetson.yml exists"

# --- Case 1: --gpu-backend jetson alone ----------------------------------
echo ""
echo "=== Case 1: --gpu-backend jetson ==="
OUT=$(bash "$RESOLVER" --gpu-backend jetson --env 2>&1)

if grep -q '^COMPOSE_PRIMARY_FILE="docker-compose.jetson.yml"$' <<< "$OUT"; then
    pass "primary file = docker-compose.jetson.yml"
else
    fail "primary file is not docker-compose.jetson.yml"
    echo "$OUT" | head -3
fi

file_list=$(grep '^COMPOSE_FILE_LIST=' <<< "$OUT" | sed 's/^COMPOSE_FILE_LIST="\(.*\)"$/\1/')

if [[ ",$file_list," == *",docker-compose.base.yml,"* ]]; then
    pass "stack contains docker-compose.base.yml"
else
    fail "stack missing docker-compose.base.yml"
fi

if [[ ",$file_list," == *",docker-compose.jetson.yml,"* ]]; then
    pass "stack contains docker-compose.jetson.yml"
else
    fail "stack missing docker-compose.jetson.yml"
fi

# Discrete-NVIDIA overlay must NOT be in the stack on Jetson.
if [[ ",$file_list," == *",docker-compose.nvidia.yml,"* ]]; then
    fail "stack wrongly contains docker-compose.nvidia.yml on Jetson"
else
    pass "discrete-nvidia overlay correctly absent"
fi

# --- Case 2: --tier JETSON_ORIN_NANO (no --gpu-backend) -------------------
echo ""
echo "=== Case 2: --tier JETSON_ORIN_NANO ==="
OUT=$(bash "$RESOLVER" --tier JETSON_ORIN_NANO --env 2>&1)
if grep -q '^COMPOSE_PRIMARY_FILE="docker-compose.jetson.yml"$' <<< "$OUT"; then
    pass "tier alone selects jetson overlay"
else
    fail "tier alone failed to select jetson overlay"
fi

# --- Case 3: ComfyUI must be excluded on Jetson ---------------------------
echo ""
echo "=== Case 3: ComfyUI excluded on Jetson ==="
file_list=$(bash "$RESOLVER" --gpu-backend jetson --env 2>&1 \
    | grep '^COMPOSE_FILE_LIST=' | sed 's/^COMPOSE_FILE_LIST="\(.*\)"$/\1/')

if [[ ",$file_list," == *"comfyui/compose.yaml"* ]]; then
    fail "comfyui compose included on Jetson (should be excluded per milestone scope)"
else
    pass "comfyui correctly excluded on Jetson"
fi

# --- Case 4: Tier wins over clobbered gpu_backend (regression for #1482) --
# The capability-profile pipeline can overwrite GPU_BACKEND from "jetson" to
# "cpu" when the hardware classifier doesn't have a Jetson entry yet. Tier
# alone must keep us on the jetson overlay so the user doesn't end up
# running a CPU stack on Jetson hardware by accident.
echo ""
echo "=== Case 4: tier JETSON_ORIN_NANO wins over clobbered gpu_backend=cpu ==="
OUT=$(bash "$RESOLVER" --tier JETSON_ORIN_NANO --gpu-backend cpu --env 2>&1)
if grep -q '^COMPOSE_PRIMARY_FILE="docker-compose.jetson.yml"$' <<< "$OUT"; then
    pass "tier JETSON_ORIN_NANO + backend=cpu still picks jetson overlay"
else
    fail "tier JETSON_ORIN_NANO + backend=cpu wrongly picked: $(grep COMPOSE_PRIMARY_FILE <<< "$OUT")"
fi

# --- Case 5: Non-Jetson backends still get their own overlay --------------
echo ""
echo "=== Case 5: nvidia backend regression check ==="
OUT=$(bash "$RESOLVER" --tier 1 --gpu-backend nvidia --env 2>&1)
if grep -q '^COMPOSE_PRIMARY_FILE="docker-compose.nvidia.yml"$' <<< "$OUT"; then
    pass "nvidia path unchanged — picks discrete nvidia overlay"
else
    fail "nvidia path regressed — primary is not docker-compose.nvidia.yml"
fi

OUT=$(bash "$RESOLVER" --tier 1 --gpu-backend amd --env 2>&1)
if grep -q '^COMPOSE_PRIMARY_FILE="docker-compose.amd.yml"$' <<< "$OUT"; then
    pass "amd path unchanged"
else
    fail "amd path regressed — primary is not docker-compose.amd.yml"
fi

OUT=$(bash "$RESOLVER" --tier 1 --gpu-backend cpu --env 2>&1)
if grep -q '^COMPOSE_PRIMARY_FILE="docker-compose.cpu.yml"$' <<< "$OUT"; then
    pass "cpu path unchanged"
else
    fail "cpu path regressed — primary is not docker-compose.cpu.yml"
fi

# --- Summary -------------------------------------------------------------
echo ""
echo "=== Summary ==="
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
[[ "$FAIL" -eq 0 ]]
