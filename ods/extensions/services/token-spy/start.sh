#!/bin/bash
# Token Spy — API Monitor — launcher
# Starts proxy instances sharing a single database.
# Authenticated proxy — strips Token Spy auth before forwarding upstream.
#
# Dual upstream routing:
#   Anthropic Messages API (/v1/messages) → ANTHROPIC_UPSTREAM
#   OpenAI Chat Completions (/v1/chat/completions) → OPENAI_UPSTREAM
#
# Database backend:
#   DB_BACKEND=sqlite (default) — uses SQLite in data/usage.db
#   DB_BACKEND=postgres — uses PostgreSQL/TimescaleDB on DB_HOST:DB_PORT
# ─────────────────────────────────────────────────────────────────────────────

set -e
cd "$(dirname "$0")"
mkdir -p data

# Safe .env loading (no eval; use ODS lib/safe-env.sh)
ODS_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
[[ -f "$ODS_ROOT/lib/safe-env.sh" ]] && . "$ODS_ROOT/lib/safe-env.sh"
load_env_file "$(pwd)/.env"

# Database backend (sqlite or postgres)
export DB_BACKEND="${DB_BACKEND:-sqlite}"

# Upstream API config
# Strix Halo: llama-server on port 11434 (container port 8080 mapped to host 11434)
export ANTHROPIC_UPSTREAM="${ANTHROPIC_UPSTREAM:-https://api.anthropic.com}"
export OPENAI_UPSTREAM="${OPENAI_UPSTREAM:-http://localhost:11434}"
export API_PROVIDER="${API_PROVIDER:-local}"

# ── Agent Configuration ──────────────────────────────────────────────────────
# Define your agents below. Each agent gets its own proxy port.
# Format: AGENT_NAME=<name> python3 -m uvicorn main:app --host 0.0.0.0 --port <port>
#
# Single agent (simplest setup — Strix Halo default):
#   AGENT_NAME=openclaw python3 -m uvicorn main:app --host 0.0.0.0 --port 9110
#
# Multiple agents (one process per agent):
#   AGENT_NAME=agent-1 python3 -m uvicorn main:app --host 0.0.0.0 --port 9110 &
#   AGENT_NAME=agent-2 python3 -m uvicorn main:app --host 0.0.0.0 --port 9111 &
#
# Local model agent (routes to llama-server):
#   AGENT_NAME=openclaw OPENAI_UPSTREAM=http://localhost:11434 API_PROVIDER=local \
#     python3 -m uvicorn main:app --host 0.0.0.0 --port 9110 &
# ─────────────────────────────────────────────────────────────────────────────

AGENT_NAME="${AGENT_NAME:-openclaw}"
PORT="${PORT:-9110}"

# Session management for OpenClaw (local inference, $0 cost)
export AGENT_SESSION_DIRS="${AGENT_SESSION_DIRS:-'{\"openclaw\":\"~/ods/data/openclaw/home/agents/main/sessions\"}'}"
export LOCAL_MODEL_AGENTS="${LOCAL_MODEL_AGENTS:-openclaw}"

echo "Starting Token Spy — API Monitor..."
echo "  Agent     → ${AGENT_NAME}"
echo "  Port      → :${PORT}"
echo "  Provider  → ${API_PROVIDER}"
echo "  DB Backend→ ${DB_BACKEND}"
echo "  Anthropic → ${ANTHROPIC_UPSTREAM}"
echo "  OpenAI    → ${OPENAI_UPSTREAM:-<not set>}"
echo "  Local     → ${LOCAL_MODEL_AGENTS:-<none>}"

PYTHON_CMD="python3"
if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(0)' >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1 && python -c 'import sys; sys.exit(0)' >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

AGENT_NAME="${AGENT_NAME}" "$PYTHON_CMD" -m uvicorn main:app --host 0.0.0.0 --port "${PORT}" --log-level warning

# ── Multi-Agent Example ──────────────────────────────────────────────────────
# Uncomment and customize for multiple agents:
#
# AGENT_NAME=agent-1 python3 -m uvicorn main:app --host 0.0.0.0 --port 9110 --log-level warning &
# PID1=$!
#
# AGENT_NAME=agent-2 python3 -m uvicorn main:app --host 0.0.0.0 --port 9111 --log-level warning &
# PID2=$!
#
# trap "echo 'Stopping...'; kill $PID1 $PID2 2>/dev/null; wait" EXIT INT TERM
# wait
