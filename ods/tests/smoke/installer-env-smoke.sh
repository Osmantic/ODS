#!/bin/bash
# ============================================================================
# Installer Environment Smoke Test
# ============================================================================
# Exercises real .env generation, schema validation, dependency validation,
# and compose syntax checking — without Docker containers or GPU hardware.
#
# This catches: schema drift, duplicate .env keys, undefined functions,
# dependency validator failures, and compose syntax errors.
#
# Usage: ./tests/smoke/installer-env-smoke.sh
# ============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0
TMPDIR_SMOKE=""

pass() { echo -e "  ${GREEN}✓${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗${NC} $1"; FAILED=$((FAILED + 1)); }

cleanup() {
    [[ -n "$TMPDIR_SMOKE" && -d "$TMPDIR_SMOKE" ]] && rm -rf "$TMPDIR_SMOKE"
}
trap cleanup EXIT

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   Installer Environment Smoke Test            ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# ── Test 1: All lib files parse without syntax errors ──
echo "── Lib & phase syntax ──"
ALL_OK=true
for f in installers/lib/*.sh installers/phases/*.sh lib/*.sh; do
    [[ -f "$f" ]] || continue
    if ! bash -n "$f" 2>/dev/null; then
        fail "Syntax error: $f"
        ALL_OK=false
    fi
done
$ALL_OK && pass "All lib/phase scripts parse cleanly"

# ── Test 2: Generate a real .env in a temp directory ──
echo ""
echo "── .env generation ──"
TMPDIR_SMOKE="$(mktemp -d)"
INSTALL_DIR="$TMPDIR_SMOKE/ods"
mkdir -p "$INSTALL_DIR"/{config,data,models}
mkdir -p "$INSTALL_DIR"/config/{n8n,litellm,openclaw,searxng}

# Copy source inputs so phase 06 can find compose files and schemas. Exclude
# local frontend build artifacts; copying node_modules across WSL/NTFS can turn
# this smoke test into a multi-minute filesystem benchmark.
tar -C "$ROOT_DIR" \
    --exclude='./.env' \
    --exclude='./extensions/services/dashboard/node_modules' \
    --exclude='./extensions/services/dashboard/dist' \
    -cf - . | tar -C "$INSTALL_DIR" -xf -

# Set up minimal environment that phase 06 expects
export INSTALL_DIR
export SCRIPT_DIR="$ROOT_DIR"
export LOG_FILE="/dev/null"
export DRY_RUN=false
export INTERACTIVE=false
export ODS_MODE="local"
export GPU_BACKEND="cpu"
export TIER="T1"
export LLM_MODEL="qwen3-1.7b"
export GGUF_FILE="Qwen3-1.7B-Q4_K_M.gguf"
export MAX_CONTEXT=4096
export ODS_VERSION="2.1.0"
export ENABLE_VOICE=true
export ENABLE_WORKFLOWS=true
export ENABLE_RAG=true
export ENABLE_HERMES=true
export ENABLE_OPENCLAW=true

# Source required libraries (same order as install-core.sh)
source installers/lib/constants.sh
source installers/lib/logging.sh
source installers/lib/ui.sh
source installers/lib/detection.sh
source installers/lib/tier-map.sh
source installers/lib/compose-select.sh
source installers/lib/packaging.sh
source installers/lib/progress.sh

# Stub functions that need a terminal or aren't relevant
ods_progress() { :; }
ai() { :; }
ai_ok() { :; }
ai_warn() { :; }
ai_bad() { :; }
chapter() { :; }
signal() { :; }
show_phase() { :; }
spin_task() { :; }

# Run .env generation from phase 06
# We source just the env-generation portion
# Phase 06 generates .env, searxng config, etc.
ENV_GENERATED=false
if bash -c "
    export INSTALL_DIR='$INSTALL_DIR'
    # The tracked source inputs were copied above. Treat that copy as an
    # in-place install so this env-generation smoke does not recopy the whole
    # developer checkout when rsync is unavailable.
    export SCRIPT_DIR='$INSTALL_DIR'
    export LOG_FILE='/dev/null'
    export DRY_RUN=false
    export INTERACTIVE=false
    export ODS_MODE=local
    export GPU_BACKEND=cpu
    export TIER=T1
    export LLM_MODEL=qwen3-1.7b
    export GGUF_FILE=Qwen3-1.7B-Q4_K_M.gguf
    export MAX_CONTEXT=4096
    export ODS_VERSION=2.1.0
    export ENABLE_VOICE=true
    export ENABLE_WORKFLOWS=true
    export ENABLE_RAG=true
    export ENABLE_HERMES=true
export ENABLE_OPENCLAW=true
    export EMBEDDING_MODEL=BAAI/bge-m3
    export RAG_EMBEDDING_MODEL=
    export RAG_OPENAI_API_BASE_URL=https://embeddings.example.test/v1
    export RAG_OPENAI_API_KEY=external-test-key
    export EMBEDDINGS_MEMORY_LIMIT=6GB

    # Source libs
    source installers/lib/constants.sh
    source installers/lib/logging.sh
    source installers/lib/ui.sh
    source installers/lib/detection.sh
    source installers/lib/progress.sh

    # Stub UI functions
    ods_progress() { :; }
    ai() { :; }
    ai_ok() { :; }
    ai_warn() { :; }
    ai_bad() { :; }
    chapter() { :; }
    signal() { :; }
    show_phase() { :; }
    sudo() { return 0; }

    docker() {
        if [[ \"\$1\" == \"info\" && \"\${2:-}\" == \"--format\" ]]; then
            echo 4
            return 0
        fi
        command docker \"\$@\"
    }

    # Run phase 06 (generates .env, configs)
    source installers/phases/06-directories.sh

    # A second installer run must retain the values written by the first run,
    # even when the invoking process now carries different defaults.
    export EMBEDDING_MODEL=BAAI/should-not-replace-existing
    export RAG_EMBEDDING_MODEL=should-not-replace-existing
    export RAG_OPENAI_API_BASE_URL=https://replacement.example.test/v1
    export RAG_OPENAI_API_KEY=replacement-secret
    export EMBEDDINGS_MEMORY_LIMIT=8GB
    source installers/phases/06-directories.sh
" 2>/dev/null; then
    ENV_GENERATED=true
    pass ".env generation completed"
else
    fail ".env generation failed (exit $?)"
fi

# ── Test 3: Validate generated .env against schema ──
echo ""
echo "── .env schema validation ──"
if [[ "$ENV_GENERATED" == true && -f "$INSTALL_DIR/.env" ]]; then
    if bash scripts/validate-env.sh "$INSTALL_DIR/.env" "$ROOT_DIR/.env.schema.json" 2>/dev/null; then
        pass ".env validates against schema"
    else
        fail ".env schema validation failed"
        # Show what went wrong
        bash scripts/validate-env.sh "$INSTALL_DIR/.env" "$ROOT_DIR/.env.schema.json" 2>&1 || true
    fi
else
    fail ".env file was not generated"
fi

if [[ "$ENV_GENERATED" == true && -f "$INSTALL_DIR/.env" ]]; then
    if grep -q '^EMBEDDING_MODEL=BAAI/bge-m3$' "$INSTALL_DIR/.env" \
        && grep -q '^RAG_EMBEDDING_MODEL=$' "$INSTALL_DIR/.env" \
        && grep -q '^RAG_OPENAI_API_BASE_URL=https://embeddings.example.test/v1$' "$INSTALL_DIR/.env" \
        && grep -q '^RAG_OPENAI_API_KEY=external-test-key$' "$INSTALL_DIR/.env" \
        && grep -q '^EMBEDDINGS_MEMORY_LIMIT=6GB$' "$INSTALL_DIR/.env"; then
        pass "Embedding/RAG values survive a Linux installer rerun"
    else
        fail "Embedding/RAG values were not preserved across a Linux installer rerun"
    fi

    if grep -q '^LLAMA_CPU_LIMIT=4.0$' "$INSTALL_DIR/.env"; then
        pass "LLAMA_CPU_LIMIT auto-caps to Docker CPU count"
    else
        fail "LLAMA_CPU_LIMIT was not auto-capped as expected"
    fi

    if grep -q '^LLAMA_CPU_RESERVATION=1.0$' "$INSTALL_DIR/.env"; then
        pass "LLAMA_CPU_RESERVATION stays within the capped limit"
    else
        fail "LLAMA_CPU_RESERVATION was not written as expected"
    fi

    if grep -q '^TTS_CPU_LIMIT=4.0$' "$INSTALL_DIR/.env"; then
        pass "TTS_CPU_LIMIT auto-caps to Docker CPU count"
    else
        fail "TTS_CPU_LIMIT was not auto-capped as expected"
    fi

    if grep -q '^WHISPER_CPU_LIMIT=4.0$' "$INSTALL_DIR/.env" \
        && grep -q '^HERMES_CPU_LIMIT=4.0$' "$INSTALL_DIR/.env" \
        && grep -q '^COMFYUI_CPU_LIMIT=4.0$' "$INSTALL_DIR/.env"; then
        pass "Bundled service CPU limits stay at or below Docker CPU count"
    else
        fail "Bundled service CPU limits were not written as expected"
    fi
fi

# Check for duplicate keys
if [[ "$ENV_GENERATED" == true && -f "$INSTALL_DIR/.env" ]]; then
    DUPES=$(grep -v '^#' "$INSTALL_DIR/.env" | grep -v '^$' | cut -d= -f1 | sort | uniq -d)
    if [[ -z "$DUPES" ]]; then
        pass "No duplicate keys in .env"
    else
        fail "Duplicate keys in .env: $DUPES"
    fi
fi

# ── Test 4: Validate service dependency graph ──
echo ""
echo "── Dependency validation ──"
if [[ -f lib/service-registry.sh && -f lib/validate-dependencies.sh ]]; then
    DEP_RESULT=$(bash -c "
        export INSTALL_DIR='$INSTALL_DIR'
        export SCRIPT_DIR='$ROOT_DIR'

        # Minimal stubs
        log() { :; }
        warn() { :; }

        source lib/service-registry.sh
        sr_load
        source lib/validate-dependencies.sh
        validate_service_dependencies 2>&1
        echo \"EXIT:\$?\"
    " 2>&1) || true

    if echo "$DEP_RESULT" | grep -q "EXIT:0"; then
        pass "All service dependencies satisfied"
    else
        fail "Service dependency validation failed"
        echo "$DEP_RESULT" | grep -v "^EXIT:" | head -10
    fi
else
    fail "service-registry.sh or validate-dependencies.sh not found"
fi

# ── Test 5: Compose syntax validation ──
echo ""
echo "── Compose syntax ──"
if command -v docker &>/dev/null && docker compose version &>/dev/null 2>&1; then
    # Generate a minimal .env for compose parsing
    COMPOSE_ENV="$TMPDIR_SMOKE/compose-test.env"
    if [[ "$ENV_GENERATED" == true && -f "$INSTALL_DIR/.env" ]]; then
        cp "$INSTALL_DIR/.env" "$COMPOSE_ENV"
    else
        # Generate minimal env for compose parsing
        cat > "$COMPOSE_ENV" << 'MINENV'
LLM_MODEL=test
GGUF_FILE=test.gguf
MAX_CONTEXT=4096
GPU_BACKEND=cpu
WEBUI_PORT=3000
WEBUI_SECRET=test
OLLAMA_PORT=11434
WHISPER_PORT=9000
TTS_PORT=8880
N8N_PORT=5678
QDRANT_PORT=6333
QDRANT_GRPC_PORT=6334
QDRANT_API_KEY=test
LITELLM_PORT=4000
LITELLM_KEY=test
OPENCLAW_PORT=7860
OPENCLAW_TOKEN=test
SEARXNG_PORT=8888
DASHBOARD_API_KEY=test
LIVEKIT_API_KEY=test
LIVEKIT_API_SECRET=test
OPENCODE_SERVER_PASSWORD=test
OPENCODE_PORT=3003
N8N_USER=admin
N8N_PASS=test
N8N_HOST=localhost
N8N_WEBHOOK_URL=http://localhost:5678
WEBUI_AUTH=true
ENABLE_WEB_SEARCH=true
WEB_SEARCH_ENGINE=searxng
WHISPER_MODEL=base
TTS_VOICE=en_US-lessac-medium
TIMEZONE=UTC
ODS_MODE=local
LLM_API_URL=http://llama-server:8080/v1
CTX_SIZE=4096
LANGFUSE_PORT=3006
LANGFUSE_ENABLED=false
LANGFUSE_NEXTAUTH_SECRET=test
LANGFUSE_SALT=test
LANGFUSE_ENCRYPTION_KEY=test
LANGFUSE_DB_PASSWORD=test
LANGFUSE_CLICKHOUSE_PASSWORD=test
LANGFUSE_REDIS_PASSWORD=test
LANGFUSE_MINIO_ACCESS_KEY=test
LANGFUSE_MINIO_SECRET_KEY=test
LANGFUSE_PROJECT_PUBLIC_KEY=test
LANGFUSE_PROJECT_SECRET_KEY=test
LANGFUSE_INIT_PROJECT_ID=test
LANGFUSE_INIT_USER_EMAIL=test@test.com
LANGFUSE_INIT_USER_PASSWORD=test
MINENV
    fi

    if docker compose --env-file "$COMPOSE_ENV" \
        -f docker-compose.base.yml \
        -f docker-compose.nvidia.yml \
        config --quiet 2>/dev/null; then
        pass "Base + NVIDIA compose validates"
    else
        fail "Compose validation failed (base + nvidia)"
    fi
else
    echo "  (skipped — docker compose not available)"
fi

# ── Test 6: All function calls resolve ──
echo ""
echo "── Function resolution ──"
# Check that ai_err is not called anywhere (it doesn't exist)
if grep -rn 'ai_err ' installers/phases/*.sh installers/lib/*.sh 2>/dev/null; then
    fail "Found calls to undefined function 'ai_err'"
else
    pass "No calls to undefined 'ai_err'"
fi

# Check all ai_* calls match defined functions
DEFINED_AI_FUNCS=$(grep -ohP '^(ai|ai_ok|ai_warn|ai_bad|signal|chapter)\(' installers/lib/ui.sh 2>/dev/null | sed 's/($//' | sort -u)
CALLED_AI_FUNCS=$(grep -ohP '\b(ai_\w+)\b' installers/phases/*.sh 2>/dev/null | sort -u)
UNDEFINED=""
for func in $CALLED_AI_FUNCS; do
    # Skip ai() itself and common patterns
    [[ "$func" == "ai" ]] && continue
    if ! echo "$DEFINED_AI_FUNCS" | grep -q "^${func}$"; then
        # Check if it's defined anywhere else
        if ! grep -rq "^${func}()\|^function ${func}" installers/lib/*.sh lib/*.sh 2>/dev/null; then
            UNDEFINED="$UNDEFINED $func"
        fi
    fi
done
if [[ -z "$UNDEFINED" ]]; then
    pass "All ai_* function calls resolve to definitions"
else
    fail "Undefined functions called:$UNDEFINED"
fi

# ── Summary ──
echo ""
echo "════════════════════════════════════════════════"
TOTAL=$((PASSED + FAILED))
echo -e "  Results: ${GREEN}$PASSED passed${NC}, ${RED}$FAILED failed${NC} / $TOTAL total"
echo "════════════════════════════════════════════════"
echo ""

[[ $FAILED -eq 0 ]] && exit 0 || exit 1
