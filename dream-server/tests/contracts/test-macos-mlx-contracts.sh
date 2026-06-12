#!/usr/bin/env bash
# Contract tests for the experimental MLX engine manager (scripts/mlx-server.sh).
# All assertions are static — no Apple Silicon hardware, network, or Python
# packages required — so they run on every platform in `make test`.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

MLX_SCRIPT="scripts/mlx-server.sh"
APPLE_CONTRACT="config/backends/apple.json"

PASS=0
FAIL=0

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

json_get() {
    python3 - "$1" "$2" <<'PY'
import json
import sys

path, key_path = sys.argv[1], sys.argv[2]
with open(path, "r", encoding="utf-8") as f:
    value = json.load(f)
for key in key_path.split("."):
    value = value[key]
print(value)
PY
}

# ---------------------------------------------------------------------------
# 1. Script exists, is executable, and parses under Bash 3.2 constructs
# ---------------------------------------------------------------------------
echo "[contract] MLX manager script shape"
if [[ -x "$MLX_SCRIPT" ]]; then
    pass "exists and executable: $MLX_SCRIPT"
else
    fail "missing or not executable: $MLX_SCRIPT"
fi

if bash -n "$MLX_SCRIPT"; then
    pass "bash -n parses"
else
    fail "bash -n failed"
fi

# macOS ships Bash 3.2 and this script MUST run there (it manages a native
# macOS process). Reject Bash-4+-only constructs.
if grep -qE 'declare -A|\$\{[A-Za-z_]+(\^\^|,,)\}|mapfile|readarray' "$MLX_SCRIPT"; then
    fail "uses Bash 4+ constructs (declare -A / case conversion / mapfile)"
else
    pass "no Bash 4+ constructs (runs on stock macOS Bash 3.2)"
fi

# ---------------------------------------------------------------------------
# 2. Experimental gate: every mutating verb requires the opt-in, and the
#    gate variable matches the DREAM_ENABLE_EXPERIMENTAL_* convention
# ---------------------------------------------------------------------------
echo "[contract] experimental opt-in gate"
if grep -q 'DREAM_ENABLE_EXPERIMENTAL_MLX' "$MLX_SCRIPT"; then
    pass "gate variable follows DREAM_ENABLE_EXPERIMENTAL_* convention"
else
    fail "gate variable missing or misnamed"
fi

for verb in install start restart; do
    if awk -v verb="$verb" '
        $0 ~ "^[[:space:]]*" verb "\\)" { in_verb=1 }
        in_verb && /require_experimental_gate/ { found=1 }
        in_verb && /;;/ { exit }
        END { exit(found ? 0 : 1) }
    ' "$MLX_SCRIPT"; then
        pass "verb '$verb' requires the experimental gate"
    else
        fail "verb '$verb' does not require the experimental gate"
    fi
done

for verb in stop status health; do
    if awk -v verb="$verb" '
        $0 ~ "^[[:space:]]*" verb "\\)" { in_verb=1 }
        in_verb && /require_experimental_gate/ { found=1 }
        in_verb && /;;/ { exit }
        END { exit(found ? 0 : 1) }
    ' "$MLX_SCRIPT"; then
        fail "read-only/safe verb '$verb' must not be gated (operators must always be able to inspect/stop)"
    else
        pass "verb '$verb' works without the gate"
    fi
done

# ---------------------------------------------------------------------------
# 3. PEP 668 discipline: dedicated venv, never pip --user / bare pip
# ---------------------------------------------------------------------------
echo "[contract] PEP 668 install discipline"
if grep -q 'python3 -m venv' "$MLX_SCRIPT"; then
    pass "installs into a dedicated venv"
else
    fail "must install mlx-lm into a dedicated venv"
fi

if grep -v '^[[:space:]]*#' "$MLX_SCRIPT" | grep -q 'pip install --user'; then
    fail "must not use pip --user (Homebrew Python rejects it under PEP 668)"
else
    pass "no pip --user"
fi

# ---------------------------------------------------------------------------
# 4. State containment: every artifact lives under the install dir
# ---------------------------------------------------------------------------
echo "[contract] state containment"
for var in 'MLX_STATE_DIR="$INSTALL_DIR/data/mlx"' \
           'MLX_PID_FILE="$INSTALL_DIR/data/.mlx-server.pid"' \
           'MLX_LOG_FILE="$INSTALL_DIR/data/mlx-server.log"'; do
    if grep -qF "$var" "$MLX_SCRIPT"; then
        pass "declares $var"
    else
        fail "missing or relocated: $var"
    fi
done

if grep -q 'HF_HOME="\$MLX_HF_CACHE_DIR"' "$MLX_SCRIPT"; then
    pass "model weights pinned inside the install dir via HF_HOME"
else
    fail "HF_HOME must point inside the install dir so weights do not land in ~/.cache"
fi

# ---------------------------------------------------------------------------
# 5. Network defaults: loopback bind, no curl to "localhost" (IPv6 ::1 can
#    hang on macOS — repo-wide rule), BIND_ADDRESS knob honoured
# ---------------------------------------------------------------------------
echo "[contract] network defaults"
if grep -q 'BIND_ADDRESS="127.0.0.1"' "$MLX_SCRIPT"; then
    pass "default bind is loopback (default-secure)"
else
    fail "default bind must be 127.0.0.1"
fi

if grep -qE 'curl[^|]*http://localhost' "$MLX_SCRIPT"; then
    fail "curl must use 127.0.0.1, never localhost"
else
    pass "health probes use 127.0.0.1"
fi

if grep -q 'read_env_var BIND_ADDRESS' "$MLX_SCRIPT"; then
    pass "honours the unified BIND_ADDRESS .env knob"
else
    fail "must honour BIND_ADDRESS from .env like the native llama-server"
fi

# ---------------------------------------------------------------------------
# 6. Process lifecycle: TERM -> bounded wait -> KILL, bounded health wait
# ---------------------------------------------------------------------------
echo "[contract] process lifecycle"
if awk '
    /^cmd_stop\(\)/ { in_stop=1 }
    in_stop && /kill "\$MLX_PID"/ { term=1 }
    in_stop && term && /kill -9 "\$MLX_PID"/ { found=1 }
    in_stop && /^\}/ { exit(found ? 0 : 1) }
    END { exit(found ? 0 : 1) }
' "$MLX_SCRIPT"; then
    pass "stop escalates TERM -> KILL"
else
    fail "stop must SIGTERM first and only then SIGKILL"
fi

if grep -q 'MLX_START_TIMEOUT' "$MLX_SCRIPT"; then
    pass "health wait is bounded (MLX_START_TIMEOUT)"
else
    fail "health wait must be bounded"
fi

# ---------------------------------------------------------------------------
# 7. Backend contract: apple.json declares the mlx runtime block
# ---------------------------------------------------------------------------
echo "[contract] apple backend contract"
if [[ "$(json_get "$APPLE_CONTRACT" "runtime.mlx.experimental")" == "True" ]]; then
    pass "runtime.mlx marked experimental"
else
    fail "runtime.mlx.experimental must be true"
fi

mlx_port="$(json_get "$APPLE_CONTRACT" "runtime.mlx.api_port")"
if [[ "$mlx_port" == "8081" ]]; then
    pass "runtime.mlx.api_port is 8081"
else
    fail "runtime.mlx.api_port unexpected: $mlx_port"
fi

# MLX must not squat on a port already owned by another service.
for taken in 8080 3000 8888 3004 9000 8880 5678 6333 6334 8090 4000 7860 8085 3002 3001 8188 3005 3003 11434 7710; do
    if [[ "$mlx_port" == "$taken" ]]; then
        fail "runtime.mlx.api_port collides with known port $taken"
    fi
done
pass "runtime.mlx.api_port does not collide with known service ports"

if json_get "$APPLE_CONTRACT" "runtime.mlx.default_model" | grep -q '^mlx-community/'; then
    pass "runtime.mlx.default_model is an mlx-community repo"
else
    fail "runtime.mlx.default_model should point at an mlx-community repo"
fi

if [[ "$(json_get "$APPLE_CONTRACT" "llm_engine")" == "llama-server" ]]; then
    pass "default apple engine unchanged (llama-server)"
else
    fail "default apple llm_engine must remain llama-server while MLX is experimental"
fi

# ---------------------------------------------------------------------------
# 8. Operator surface: .env.example + .env.schema.json document the knobs
# ---------------------------------------------------------------------------
echo "[contract] env documentation"
for key in DREAM_ENABLE_EXPERIMENTAL_MLX MLX_PORT MLX_MODEL; do
    if grep -q "$key" .env.example; then
        pass ".env.example documents $key"
    else
        fail ".env.example missing $key"
    fi
    if python3 -c "
import json, sys
schema = json.load(open('.env.schema.json'))
sys.exit(0 if '$key' in schema['properties'] else 1)
"; then
        pass ".env.schema.json declares $key"
    else
        fail ".env.schema.json missing $key"
    fi
done

if [[ -f "docs/MLX.md" ]]; then
    pass "docs/MLX.md exists"
else
    fail "docs/MLX.md missing"
fi

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
