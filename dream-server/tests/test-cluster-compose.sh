#!/bin/bash
# ============================================================================
# LAN Cluster compose integration tests
# ============================================================================
# Tests that cluster overlays are correctly selected by resolve-compose-stack.sh,
# that Dockerfiles are structurally valid, and that compose YAML is parseable.
#
# Usage: ./tests/test-cluster-compose.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }
skip() { echo -e "  ${YELLOW}⊘ SKIP${NC} $1"; }

resolve() {
    bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
        --script-dir "$ROOT_DIR" "$@" 2>/dev/null
}

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   LAN Cluster Compose Integration Tests       ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# ── File existence ──────────────────────────────────────────────

echo "── File existence ──"

for f in \
    docker-compose.cluster.yml \
    docker-compose.cluster-amd.yml \
    images/llama-rpc/Dockerfile.cuda \
    images/llama-rpc/Dockerfile.rocm \
    images/llama-rpc/Dockerfile.rpc-cuda \
    images/llama-rpc/Dockerfile.rpc-rocm \
    scripts/dream-cluster-supervisor.py; do
    if [[ -f "$ROOT_DIR/$f" ]]; then
        pass "$f exists"
    else
        fail "$f missing"
    fi
done

echo ""

# ── Compose resolver: cluster overlay selection ─────────────────

echo "── Compose resolver: cluster overlay selection ──"

# NVIDIA + cluster enabled
output=$(CLUSTER_ENABLED=true resolve --gpu-backend nvidia)
if echo "$output" | grep -q "docker-compose.cluster.yml"; then
    pass "NVIDIA + cluster → includes cluster.yml"
else
    fail "NVIDIA + cluster → missing cluster.yml"
fi
if echo "$output" | grep -q "docker-compose.cluster-amd.yml"; then
    fail "NVIDIA + cluster → should NOT include cluster-amd.yml"
else
    pass "NVIDIA + cluster → excludes cluster-amd.yml"
fi

# AMD + cluster enabled
output=$(CLUSTER_ENABLED=true resolve --gpu-backend amd)
if echo "$output" | grep -q "docker-compose.cluster-amd.yml"; then
    pass "AMD + cluster → includes cluster-amd.yml"
else
    fail "AMD + cluster → missing cluster-amd.yml"
fi
if echo "$output" | grep -q -- "-f docker-compose.cluster.yml"; then
    fail "AMD + cluster → should NOT include cluster.yml (non-amd)"
else
    pass "AMD + cluster → excludes generic cluster.yml"
fi

# CPU + cluster enabled
output=$(CLUSTER_ENABLED=true resolve --gpu-backend cpu)
if echo "$output" | grep -q "docker-compose.cluster.yml"; then
    pass "CPU + cluster → includes cluster.yml (generic)"
else
    fail "CPU + cluster → missing cluster.yml"
fi

# Cluster disabled (default) → no cluster overlay
output=$(resolve --gpu-backend nvidia)
if echo "$output" | grep -q "cluster"; then
    fail "Cluster disabled → should NOT include any cluster overlay"
else
    pass "Cluster disabled → no cluster overlay"
fi

# Explicit CLUSTER_ENABLED=false → no cluster overlay
output=$(CLUSTER_ENABLED=false resolve --gpu-backend nvidia)
if echo "$output" | grep -q "cluster"; then
    fail "CLUSTER_ENABLED=false → should NOT include any cluster overlay"
else
    pass "CLUSTER_ENABLED=false → no cluster overlay"
fi

echo ""

# ── Compose resolver: cluster + multi-GPU coexistence ───────────

echo "── Compose resolver: cluster + multi-GPU coexistence ──"

# AMD multi-GPU + cluster should include BOTH overlays in correct order
output=$(CLUSTER_ENABLED=true resolve --gpu-backend amd --gpu-count 2)
if echo "$output" | grep -q "docker-compose.multigpu-amd.yml"; then
    pass "AMD multi-GPU + cluster → includes multigpu overlay"
else
    fail "AMD multi-GPU + cluster → missing multigpu overlay"
fi
if echo "$output" | grep -q "docker-compose.cluster-amd.yml"; then
    pass "AMD multi-GPU + cluster → includes cluster overlay"
else
    fail "AMD multi-GPU + cluster → missing cluster overlay"
fi

# Verify ordering: multigpu before cluster (cluster overrides multigpu's image)
multigpu_pos=$(echo "$output" | tr ' ' '\n' | grep -n "multigpu-amd" | head -1 | cut -d: -f1)
cluster_pos=$(echo "$output" | tr ' ' '\n' | grep -n "cluster-amd" | head -1 | cut -d: -f1)
if [[ -n "$multigpu_pos" && -n "$cluster_pos" && "$multigpu_pos" -lt "$cluster_pos" ]]; then
    pass "AMD: multigpu overlay appears before cluster overlay"
else
    fail "AMD: multigpu overlay should appear before cluster overlay"
fi

# NVIDIA multi-GPU + cluster
output=$(CLUSTER_ENABLED=true resolve --gpu-backend nvidia --gpu-count 2)
multigpu_pos=$(echo "$output" | tr ' ' '\n' | grep -n "multigpu-nvidia" | head -1 | cut -d: -f1)
cluster_pos=$(echo "$output" | tr ' ' '\n' | grep -n "cluster.yml" | head -1 | cut -d: -f1)
if [[ -n "$multigpu_pos" && -n "$cluster_pos" && "$multigpu_pos" -lt "$cluster_pos" ]]; then
    pass "NVIDIA: multigpu overlay appears before cluster overlay"
else
    fail "NVIDIA: multigpu overlay should appear before cluster overlay"
fi

echo ""

# ── Dockerfile structural validation ───────────────────────────

echo "── Dockerfile structural validation ──"

for df in "$ROOT_DIR"/images/llama-rpc/Dockerfile.*; do
    name=$(basename "$df")

    # Has FROM
    if grep -q '^FROM' "$df"; then
        pass "$name has FROM instruction"
    else
        fail "$name missing FROM instruction"
    fi

    # Multi-stage build (has 'AS build')
    if grep -q 'AS build' "$df"; then
        pass "$name uses multi-stage build"
    else
        fail "$name missing multi-stage build"
    fi

    # Has LLAMA_CPP_TAG build arg
    if grep -q 'LLAMA_CPP_TAG' "$df"; then
        pass "$name accepts LLAMA_CPP_TAG arg"
    else
        fail "$name missing LLAMA_CPP_TAG arg"
    fi

    # Builds with GGML_RPC=ON
    if grep -q 'GGML_RPC=ON' "$df"; then
        pass "$name enables GGML_RPC"
    else
        fail "$name missing GGML_RPC=ON"
    fi
done

# ROCm Dockerfiles should have AMDGPU_TARGETS as build arg
for df in "$ROOT_DIR"/images/llama-rpc/Dockerfile.*rocm; do
    name=$(basename "$df")
    if grep -q 'ARG AMDGPU_TARGETS' "$df"; then
        pass "$name has AMDGPU_TARGETS as build arg"
    else
        fail "$name missing AMDGPU_TARGETS build arg"
    fi
done

# Controller Dockerfiles should include supervisor script
for df in "$ROOT_DIR"/images/llama-rpc/Dockerfile.cuda "$ROOT_DIR"/images/llama-rpc/Dockerfile.rocm; do
    name=$(basename "$df")
    if grep -q 'dream-cluster-supervisor' "$df"; then
        pass "$name includes supervisor script"
    else
        fail "$name missing supervisor script COPY"
    fi
done

# Worker Dockerfiles should NOT include supervisor
for df in "$ROOT_DIR"/images/llama-rpc/Dockerfile.rpc-cuda "$ROOT_DIR"/images/llama-rpc/Dockerfile.rpc-rocm; do
    name=$(basename "$df")
    if grep -q 'dream-cluster-supervisor' "$df"; then
        fail "$name should NOT include supervisor (worker-only image)"
    else
        pass "$name correctly excludes supervisor"
    fi
done

echo ""

# ── Compose YAML validation ────────────────────────────────────

echo "── Compose YAML content validation ──"

# Check cluster overlays reference the supervisor entrypoint
for f in docker-compose.cluster.yml docker-compose.cluster-amd.yml; do
    if grep -q "dream-cluster-supervisor" "$ROOT_DIR/$f"; then
        pass "$f references supervisor entrypoint"
    else
        fail "$f missing supervisor entrypoint"
    fi
done

# Check cluster overlays have --rpc and --tensor-split
for f in docker-compose.cluster.yml docker-compose.cluster-amd.yml; do
    if grep -qF -- '--rpc' "$ROOT_DIR/$f" && grep -qF -- '--tensor-split' "$ROOT_DIR/$f"; then
        pass "$f has --rpc and --tensor-split flags"
    else
        fail "$f missing --rpc or --tensor-split"
    fi
done

# Check cluster overlays have --no-warmup (avoids b8233+ crash)
for f in docker-compose.cluster.yml docker-compose.cluster-amd.yml; do
    if grep -qF -- '--no-warmup' "$ROOT_DIR/$f"; then
        pass "$f has --no-warmup flag"
    else
        fail "$f missing --no-warmup (required to avoid b8233+ RPC warmup crash)"
    fi
done

# Check cluster overlays have restart: "no"
for f in docker-compose.cluster.yml docker-compose.cluster-amd.yml; do
    if grep -q 'restart:.*"no"' "$ROOT_DIR/$f"; then
        pass "$f has restart: no (supervisor owns restart)"
    else
        fail "$f missing restart: no"
    fi
done

# Check AMD overlay has ROCm device passthrough
if grep -q '/dev/dri' "$ROOT_DIR/docker-compose.cluster-amd.yml" \
    && grep -q '/dev/kfd' "$ROOT_DIR/docker-compose.cluster-amd.yml"; then
    pass "cluster-amd.yml has ROCm device passthrough"
else
    fail "cluster-amd.yml missing ROCm device passthrough"
fi

# Check model default matches base compose
base_default=$(grep -oP 'GGUF_FILE:-\K[^}]+' "$ROOT_DIR/docker-compose.base.yml" | head -1)
for f in docker-compose.cluster.yml docker-compose.cluster-amd.yml; do
    cluster_default=$(grep -oP 'GGUF_FILE:-\K[^}]+' "$ROOT_DIR/$f" | head -1)
    if [[ "$cluster_default" == "$base_default" ]]; then
        pass "$f model default matches base compose ($base_default)"
    else
        fail "$f model default ($cluster_default) differs from base ($base_default)"
    fi
done

echo ""

# ── Env config validation ──────────────────────────────────────

echo "── Env config validation ──"

# Schema should have cluster vars
for var in CLUSTER_ENABLED CLUSTER_WORKERS CLUSTER_TENSOR_SPLIT CLUSTER_RESTART_POLICY LLAMA_CPP_TAG; do
    if grep -q "\"$var\"" "$ROOT_DIR/.env.schema.json"; then
        pass ".env.schema.json has $var"
    else
        fail ".env.schema.json missing $var"
    fi
done

# Schema must declare CLUSTER_TOKEN — the shared secret used by the
# TCP handshake listener to gate worker joins (Phase 2 landed).
if grep -q "CLUSTER_TOKEN" "$ROOT_DIR/.env.schema.json"; then
    pass ".env.schema.json has CLUSTER_TOKEN"
else
    fail ".env.schema.json missing CLUSTER_TOKEN"
fi

# .env.example should have cluster section
if grep -q "CLUSTER_ENABLED" "$ROOT_DIR/.env.example"; then
    pass ".env.example documents CLUSTER_ENABLED"
else
    fail ".env.example missing CLUSTER_ENABLED"
fi

# .env.example should have security warning
if grep -q "no encryption" "$ROOT_DIR/.env.example" || grep -q "plaintext" "$ROOT_DIR/.env.example"; then
    pass ".env.example has RPC security warning"
else
    fail ".env.example missing RPC security warning"
fi

# .gitignore should have cluster.json
if grep -q "cluster.json" "$ROOT_DIR/.gitignore"; then
    pass ".gitignore excludes config/cluster.json"
else
    fail ".gitignore missing config/cluster.json"
fi

echo ""

# ── Supervisor script structural checks ────────────────────────

echo "── Supervisor script structural checks ──"

SUP="$ROOT_DIR/scripts/dream-cluster-supervisor.py"

# Supervisor canonical location is scripts/. The image build context
# (images/llama-rpc/) should NOT have a committed copy — it's staged at
# install time by installers/phases/08-images.sh and is gitignored.
if [[ -f "$ROOT_DIR/images/llama-rpc/dream-cluster-supervisor.py" ]]; then
    fail "Stale supervisor copy in images/llama-rpc/ (should be staged at build, not committed)"
else
    pass "No committed supervisor copy in image build context"
fi

# SIGTERM handler should reference _child_proc (not just sys.exit)
if grep -q '_child_proc' "$SUP" && grep -q '_handle_term' "$SUP"; then
    pass "Supervisor has proper SIGTERM forwarding handler"
else
    fail "Supervisor missing SIGTERM child forwarding"
fi

# log_event should use os.replace for atomic writes
if grep -q 'os.replace' "$SUP"; then
    pass "Supervisor uses atomic os.replace() for event writes"
else
    fail "Supervisor missing atomic os.replace() in log_event"
fi

# Recovery loop must re-partition workers rather than reuse the initial
# snapshot — otherwise a worker that came back online would stay marked
# dead. Structural check: partition_workers(all_workers) should appear
# at least twice (top-of-loop + inside the wait-for-recovery deadline).
partition_calls=$(grep -c 'partition_workers(all_workers)' "$SUP")
if [[ "$partition_calls" -ge 2 ]]; then
    pass "Supervisor re-partitions workers inside recovery loop (no stale snapshot)"
else
    fail "Supervisor may reuse stale worker partition in recovery (found $partition_calls partition_workers calls, need >=2)"
fi

echo ""

# ── Summary ────────────────────────────────────────────────────

echo "═══════════════════════════════════════════════"
echo "Result: $PASSED passed, $FAILED failed"
echo "═══════════════════════════════════════════════"
[[ $FAILED -eq 0 ]]
