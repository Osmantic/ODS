#!/bin/bash
# ============================================================================
# mlx-server.sh — Experimental MLX engine manager (Apple Silicon)
# ============================================================================
# Runs the MLX OpenAI-compatible inference server (mlx_lm.server) natively on
# Apple Silicon, beside the default native llama-server engine. MLX models
# (mlx-community/* on Hugging Face) use Apple's unified-memory ML framework
# and can outperform GGUF on M-series hardware for some workloads.
#
# EXPERIMENTAL — and deliberately additive:
#   * Nothing in the default install path invokes this script.
#   * Mutating verbs (install/start/restart) require
#     DREAM_ENABLE_EXPERIMENTAL_MLX=1 (same opt-in pattern as Jetson).
#   * stop/status/health are read-only-safe and work without the gate, so an
#     operator can always inspect or bring down a server they started.
#   * The default engine contract (config/backends/apple.json llm_engine)
#     is untouched; MLX listens on its own port (default 8081).
#
# Usage:
#   DREAM_ENABLE_EXPERIMENTAL_MLX=1 scripts/mlx-server.sh install
#   DREAM_ENABLE_EXPERIMENTAL_MLX=1 scripts/mlx-server.sh start [--model <hf-id>]
#   scripts/mlx-server.sh status
#   scripts/mlx-server.sh health
#   scripts/mlx-server.sh stop
#
# Configuration (`.env` keys or environment, all optional):
#   MLX_PORT                       API port            (default: runtime.mlx.api_port, 8081)
#   MLX_MODEL                      Hugging Face repo   (default: runtime.mlx.default_model)
#   MLX_START_TIMEOUT              Health wait seconds (default: 600 — first start
#                                  downloads the model from Hugging Face)
#   BIND_ADDRESS                   127.0.0.1 (default) or 0.0.0.0 — same knob
#                                  the native llama-server honours
#
# State lives entirely under <install>/data/mlx/ (venv + Hugging Face cache)
# plus the .mlx-server.pid / mlx-server.log siblings of the llama-server
# equivalents, so `rm -rf data/mlx data/.mlx-server.pid data/mlx-server.log`
# removes every trace.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Resolve the install dir the same way the rest of the stack does: shared
# path-utils when available (installers/lib/ in both the source tree and the
# installed layout), DREAM_HOME fallback otherwise.
if [[ -f "$ROOT_DIR/installers/lib/path-utils.sh" ]]; then
    . "$ROOT_DIR/installers/lib/path-utils.sh"
    INSTALL_DIR="$(resolve_install_dir)"
else
    INSTALL_DIR="${DREAM_HOME:-$HOME/dream-server}"
fi

ENV_FILE="$INSTALL_DIR/.env"
MLX_STATE_DIR="$INSTALL_DIR/data/mlx"
MLX_VENV_DIR="$MLX_STATE_DIR/venv"
MLX_HF_CACHE_DIR="$MLX_STATE_DIR/hf-cache"
MLX_PID_FILE="$INSTALL_DIR/data/.mlx-server.pid"
MLX_LOG_FILE="$INSTALL_DIR/data/mlx-server.log"
BACKEND_CONTRACT="$ROOT_DIR/config/backends/apple.json"

GRN='\033[0;32m'; RED='\033[0;31m'; AMB='\033[0;33m'; NC='\033[0m'
ok()   { printf "${GRN}✓${NC} %s\n" "$*"; }
warn() { printf "${AMB}!${NC} %s\n" "$*"; }
err()  { printf "${RED}✗${NC} %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

usage() {
    sed -n '/^# Usage:/,/^# Configuration/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' | sed '$d'
}

require_experimental_gate() {
    if [[ "${DREAM_ENABLE_EXPERIMENTAL_MLX:-0}" != "1" ]]; then
        err "The MLX engine is experimental and disabled by default."
        err "Re-run with: DREAM_ENABLE_EXPERIMENTAL_MLX=1 $0 $*"
        exit 1
    fi
}

require_apple_silicon() {
    [[ "$(uname -s)" == "Darwin" ]] || die "MLX requires macOS (detected $(uname -s))."
    [[ "$(uname -m)" == "arm64" ]] || die "MLX requires Apple Silicon (detected $(uname -m))."
}

require_python3() {
    command -v python3 >/dev/null 2>&1 \
        || die "python3 is required. Install the Xcode CLT (xcode-select --install) or Homebrew Python."
}

# Read KEY=value from .env, stripping surrounding quotes. Empty when unset.
read_env_var() {
    local key="$1"
    [[ -f "$ENV_FILE" ]] || { echo ""; return 0; }
    grep "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' || true
}

# Read a runtime.mlx field from the apple backend contract. Empty on absence
# so callers can fall back to hardcoded defaults.
contract_mlx_field() {
    local field="$1"
    [[ -f "$BACKEND_CONTRACT" ]] || { echo ""; return 0; }
    python3 - "$BACKEND_CONTRACT" "$field" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        contract = json.load(f)
    value = contract.get("runtime", {}).get("mlx", {}).get(sys.argv[2], "")
except (OSError, json.JSONDecodeError):
    value = ""
print(value)
PY
}

resolve_settings() {
    MLX_PORT="${MLX_PORT:-$(read_env_var MLX_PORT)}"
    [[ -z "$MLX_PORT" ]] && MLX_PORT="$(contract_mlx_field api_port)"
    [[ -z "$MLX_PORT" ]] && MLX_PORT=8081

    MLX_MODEL="${MLX_MODEL:-$(read_env_var MLX_MODEL)}"
    [[ -z "$MLX_MODEL" ]] && MLX_MODEL="$(contract_mlx_field default_model)"
    [[ -z "$MLX_MODEL" ]] && die "No MLX model configured. Set MLX_MODEL (e.g. mlx-community/Qwen3-4B-4bit)."

    BIND_ADDRESS="${BIND_ADDRESS:-$(read_env_var BIND_ADDRESS)}"
    [[ -z "$BIND_ADDRESS" ]] && BIND_ADDRESS="127.0.0.1"

    MLX_HEALTH_PATH="$(contract_mlx_field health_path)"
    [[ -z "$MLX_HEALTH_PATH" ]] && MLX_HEALTH_PATH="/health"

    MLX_START_TIMEOUT="${MLX_START_TIMEOUT:-600}"
}

get_mlx_pid() {
    MLX_PID=""
    if [[ -f "$MLX_PID_FILE" ]]; then
        MLX_PID="$(tr -dc '0-9' < "$MLX_PID_FILE" 2>/dev/null || true)"
        if [[ -z "$MLX_PID" ]] || ! kill -0 "$MLX_PID" 2>/dev/null; then
            MLX_PID=""
        fi
    fi
}

# Health probe. mlx_lm.server exposes /health; older releases only have the
# OpenAI surface, so fall back to /v1/models. Always probe 127.0.0.1: probing
# a 0.0.0.0 bind via loopback is valid, and IPv6 ::1 resolution of
# "localhost" can hang on macOS.
probe_health() {
    curl -sf --max-time 5 "http://127.0.0.1:${MLX_PORT}${MLX_HEALTH_PATH}" >/dev/null 2>&1 \
        || curl -sf --max-time 5 "http://127.0.0.1:${MLX_PORT}/v1/models" >/dev/null 2>&1
}

cmd_install() {
    require_apple_silicon
    require_python3

    mkdir -p "$MLX_STATE_DIR"

    # PEP 668: Homebrew/system Python rejects bare `pip install` and
    # `pip install --user`. A dedicated venv under data/mlx/ is the only
    # install mode this script supports — it also makes uninstall a rm -rf.
    if [[ ! -x "$MLX_VENV_DIR/bin/python" ]]; then
        echo "Creating MLX virtualenv: $MLX_VENV_DIR"
        python3 -m venv "$MLX_VENV_DIR"
    fi

    local spec="mlx-lm${MLX_LM_VERSION:+==$MLX_LM_VERSION}"
    echo "Installing $spec into the MLX virtualenv (this can take a minute)..."
    "$MLX_VENV_DIR/bin/python" -m pip install --quiet --upgrade "$spec"

    local installed
    installed="$("$MLX_VENV_DIR/bin/python" -m pip show mlx-lm 2>/dev/null | grep '^Version:' | awk '{print $2}')"
    ok "mlx-lm ${installed:-?} installed in $MLX_VENV_DIR"
}

cmd_start() {
    require_apple_silicon
    resolve_settings

    [[ -x "$MLX_VENV_DIR/bin/python" ]] \
        || die "MLX virtualenv missing. Run: DREAM_ENABLE_EXPERIMENTAL_MLX=1 $0 install"

    get_mlx_pid
    if [[ -n "$MLX_PID" ]]; then
        ok "mlx-server already running (PID $MLX_PID, port $MLX_PORT)"
        return 0
    fi

    mkdir -p "$MLX_STATE_DIR" "$(dirname "$MLX_PID_FILE")"

    # Prefer the console entry point; fall back to module invocation for
    # mlx-lm releases that don't ship it.
    local -a launch_cmd
    if [[ -x "$MLX_VENV_DIR/bin/mlx_lm.server" ]]; then
        launch_cmd=("$MLX_VENV_DIR/bin/mlx_lm.server")
    else
        launch_cmd=("$MLX_VENV_DIR/bin/python" -m mlx_lm.server)
    fi

    echo "Starting mlx-server: $MLX_MODEL on ${BIND_ADDRESS}:${MLX_PORT}"
    echo "  (first start downloads the model from Hugging Face into data/mlx/hf-cache)"
    # HF_HOME keeps model weights inside the install dir, mirroring how GGUF
    # files live in data/models/.
    HF_HOME="$MLX_HF_CACHE_DIR" \
        "${launch_cmd[@]}" \
        --model "$MLX_MODEL" \
        --host "$BIND_ADDRESS" \
        --port "$MLX_PORT" \
        > "$MLX_LOG_FILE" 2>&1 &
    local pid=$!
    echo "$pid" > "$MLX_PID_FILE"

    local waited=0
    while [[ "$waited" -lt "$MLX_START_TIMEOUT" ]]; do
        sleep 2
        waited=$((waited + 2))
        if probe_health; then
            ok "mlx-server healthy (PID $pid, http://127.0.0.1:${MLX_PORT}/v1)"
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            rm -f "$MLX_PID_FILE"
            err "mlx-server process died during startup. Last log lines:"
            tail -20 "$MLX_LOG_FILE" >&2 || true
            exit 1
        fi
        if (( waited % 30 == 0 )); then
            echo "  Still starting (model download/load)... ${waited}s — tail -f $MLX_LOG_FILE"
        fi
    done

    err "mlx-server did not become healthy within ${MLX_START_TIMEOUT}s; leaving it running."
    err "Inspect: tail -f $MLX_LOG_FILE — then '$0 status' or '$0 stop'."
    exit 1
}

cmd_stop() {
    get_mlx_pid
    if [[ -z "$MLX_PID" ]]; then
        echo "mlx-server not running"
        rm -f "$MLX_PID_FILE"
        return 0
    fi

    # SIGTERM, bounded wait, then SIGKILL — same shutdown contract as the
    # native llama-server path.
    kill "$MLX_PID" 2>/dev/null || true
    local i=0
    while [[ "$i" -lt 20 ]] && kill -0 "$MLX_PID" 2>/dev/null; do
        sleep 0.5
        i=$((i + 1))
    done
    if kill -0 "$MLX_PID" 2>/dev/null; then
        kill -9 "$MLX_PID" 2>/dev/null || true
    fi
    rm -f "$MLX_PID_FILE"
    ok "mlx-server stopped (PID $MLX_PID)"
}

cmd_status() {
    resolve_settings 2>/dev/null || true
    get_mlx_pid
    if [[ -n "$MLX_PID" ]]; then
        if probe_health; then
            ok "mlx-server running and healthy (PID $MLX_PID, port ${MLX_PORT:-?})"
        else
            warn "mlx-server running (PID $MLX_PID) but not answering on port ${MLX_PORT:-?} yet"
        fi
    else
        echo "mlx-server not running"
    fi
    if [[ -x "$MLX_VENV_DIR/bin/python" ]]; then
        echo "  venv: $MLX_VENV_DIR"
    else
        echo "  venv: not installed (run: DREAM_ENABLE_EXPERIMENTAL_MLX=1 $0 install)"
    fi
}

cmd_health() {
    resolve_settings
    if probe_health; then
        ok "healthy: http://127.0.0.1:${MLX_PORT}${MLX_HEALTH_PATH}"
    else
        err "unhealthy: no response on port ${MLX_PORT}"
        exit 1
    fi
}

VERB="${1:-}"
shift || true

# Optional --model override for start
while [[ $# -gt 0 ]]; do
    case "$1" in
        --model)
            MLX_MODEL="${2:-}"
            shift 2
            ;;
        *)
            die "Unknown argument: $1"
            ;;
    esac
done

case "$VERB" in
    install)
        require_experimental_gate install
        cmd_install
        ;;
    start)
        require_experimental_gate start
        cmd_start
        ;;
    restart)
        require_experimental_gate restart
        cmd_stop
        cmd_start
        ;;
    stop)
        cmd_stop
        ;;
    status)
        cmd_status
        ;;
    health)
        cmd_health
        ;;
    *)
        usage
        exit 1
        ;;
esac
