#!/bin/bash
# ============================================================================
# ODS Installer — llmfit Model Advisor
# ============================================================================
# Part of: scripts/
# Purpose: Query llmfit binary (if available) for hardware-aware model
#          recommendation. Falls back silently to existing tier map if
#          llmfit is absent, times out, or returns unmapped output.
#
# Expects: HARDWARE_CLASS (set by classify-hardware.sh)
#          ODS_HOME (optional, for vendored binary lookup)
# Provides: LLMFIT_ODS_GGUF, LLMFIT_ODS_TIER, LLMFIT_TOKENSEC (exported)
#           Returns 0 on successful recommendation, 1 on any failure
#
# Modder notes:
#   This is a pure advisor — it never writes to .env or disk.
#   All side effects belong in the calling phase script.
#   LLMFIT_VERSION is pinned — update intentionally, not automatically.
#   Model map is in config/llmfit-model-map.json — update when ODS
#   tier map changes.
# ============================================================================

set -euo pipefail

LLMFIT_VERSION="0.4.2"
LLMFIT_TIMEOUT=10

if [ -n "${SCRIPT_DIR:-}" ]; then
    if [ -f "$SCRIPT_DIR/config/llmfit-model-map.json" ]; then
        LLMFIT_MODEL_MAP="$SCRIPT_DIR/config/llmfit-model-map.json"
    elif [ -f "$SCRIPT_DIR/../config/llmfit-model-map.json" ]; then
        LLMFIT_MODEL_MAP="$SCRIPT_DIR/../config/llmfit-model-map.json"
    else
        LLMFIT_MODEL_MAP="$SCRIPT_DIR/llmfit-model-map.json"
    fi
else
    LLMFIT_MODEL_MAP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../config/llmfit-model-map.json"
fi

_log() {
    if command -v log >/dev/null 2>&1; then
        log "$@"
    else
        echo "[INFO] $@"
    fi
}

_warn() {
    if command -v warn >/dev/null 2>&1; then
        warn "$@"
    else
        echo "[WARN] $@" >&2
    fi
}

# Locate llmfit binary — vendored first, then PATH
_llmfit_binary() {
    local vendored="${ODS_HOME:-$HOME/ods}/bin/llmfit-${LLMFIT_VERSION}"
    if [ -x "$vendored" ]; then
        echo "$vendored"
        return 0
    fi
    if command -v llmfit > /dev/null 2>&1; then
        echo "llmfit"
        return 0
    fi
    return 1
}

# Parse llmfit JSON output and map to ODS GGUF catalog
# Returns 0 and exports LLMFIT_ODS_GGUF/TIER/TOKENSEC on success
# Returns 1 on parse failure or unmapped model
_parse_llmfit_output() {
    local json="$1"
    local result
    result=$(python3 - "$json" "$LLMFIT_MODEL_MAP" << 'EOF'
import sys, json

llmfit_json, map_path = sys.argv[1], sys.argv[2]

try:
    recommendations = json.loads(llmfit_json)['recommendations']
    with open(map_path) as f:
        model_map = json.load(f)['mappings']
except (KeyError, json.JSONDecodeError, FileNotFoundError) as e:
    sys.exit(1)

# Find first recommendation that maps to a known ODS GGUF
for rec in recommendations:
    model_id = rec.get('model_id', '')
    quant = rec.get('quantization', '')
    tokensec = rec.get('estimated_tokens_per_sec', 'unknown')

    for entry in model_map:
        if (entry['llmfit_model_id'] == model_id and
                entry['llmfit_quant'] == quant):
            print(f"{entry['ods_gguf_file']}|{entry['ods_tier']}|{tokensec}")
            sys.exit(0)

# No mapping found
sys.exit(1)
EOF
    ) || return 1

    LLMFIT_ODS_GGUF=$(echo "$result" | cut -d'|' -f1)
    LLMFIT_ODS_TIER=$(echo "$result" | cut -d'|' -f2)
    LLMFIT_TOKENSEC=$(echo "$result" | cut -d'|' -f3)
    export LLMFIT_ODS_GGUF LLMFIT_ODS_TIER LLMFIT_TOKENSEC
    return 0
}

# Main entry point — call this from installer phases
# Returns 0 with LLMFIT_ODS_GGUF/TIER/TOKENSEC set, or 1 to use tier map
query_llmfit_recommendation() {
    local category="${1:-general}"
    local binary

    binary=$(_llmfit_binary) || {
        # Silent — llmfit not installed is expected, not an error
        return 1
    }

    local output
    output=$(timeout "$LLMFIT_TIMEOUT" \
        "$binary" --json --category "$category" 2>/dev/null) || {
        _warn "llmfit timed out or failed — using tier map fallback"
        return 1
    }

    _parse_llmfit_output "$output" || {
        _warn "llmfit recommendation not in ODS model catalog — using tier map fallback"
        return 1
    }

    return 0
}
