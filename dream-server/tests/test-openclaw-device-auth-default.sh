#!/bin/bash
# ============================================================================
# Test: OpenClaw device auth is ENABLED by default  (#1270)
# ============================================================================
# SECURITY REGRESSION TEST (swarm task #10, owns #1270 verification).
#
# config/openclaw/openclaw.json shipped with
#   "gateway.controlUi.dangerouslyDisableDeviceAuth": true
# AND config/openclaw/inject-token.js UNCONDITIONALLY re-forces that flag at
# every container start (lines ~74 and ~254). So a fix that edits only the
# static JSON is COSMETIC — the runtime-effective config is whatever
# inject-token.js writes. The compose entrypoint also ran
# `gateway --allow-unconfigured --bind lan`, so the only thing protecting an
# unauthenticated agent gateway was the default BIND_ADDRESS (#1270).
#
# Post-fix contract verified here:
#   A. STATIC source of truth (config/openclaw/openclaw.json): device auth
#      NOT disabled by default (key absent or false).
#   B. RUNTIME-EFFECTIVE path (inject-token.js output): with NO opt-in env,
#      the patched config must NOT disable device auth. With the explicit
#      opt-in env set, disabling is allowed (gated, deliberate).
#   C. compose entrypoint no longer combines --allow-unconfigured + --bind lan.
#   D. Localhost path unaffected: BIND_ADDRESS default 127.0.0.1, port map
#      unchanged.
#
# Run: bash tests/test-openclaw-device-auth-default.sh
# ============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0
FAIL=0
SKIP=0

pass() { echo "  PASS: $1"; ((PASS++)); }
fail() { echo "  FAIL: $1"; ((FAIL++)); }
skip() { echo "  SKIP: $1"; ((SKIP++)); }

CONF="$SCRIPT_DIR/config/openclaw/openclaw.json"
INJECT="$SCRIPT_DIR/config/openclaw/inject-token.js"
COMPOSE="$SCRIPT_DIR/extensions/services/openclaw/compose.yaml"
OPTIN_ENV="OPENCLAW_DANGEROUSLY_DISABLE_DEVICE_AUTH"

have_node=0; command -v node >/dev/null 2>&1 && have_node=1
have_jq=0;   command -v jq   >/dev/null 2>&1 && have_jq=1
have_py=0;   command -v python3 >/dev/null 2>&1 && have_py=1

json_get() { # file pyexpr
    python3 - "$1" "$2" <<'PY' 2>/dev/null
import json,sys
d=json.load(open(sys.argv[1]))
try: print(eval(sys.argv[2]))
except Exception: print("__MISSING__")
PY
}

echo "== #1270: OpenClaw device auth enabled by default =="

# ── A. Static source of truth ──────────────────────────────────────────────
if [[ ! -f "$CONF" ]]; then
    fail "config/openclaw/openclaw.json missing"
elif (( have_py )); then
    v="$(json_get "$CONF" "d.get('gateway',{}).get('controlUi',{}).get('dangerouslyDisableDeviceAuth','__MISSING__')")"
    case "$v" in
        True)       fail "static config still disables device auth (dangerouslyDisableDeviceAuth:true)";;
        False)      pass "static config: dangerouslyDisableDeviceAuth explicitly false";;
        __MISSING__) pass "static config: dangerouslyDisableDeviceAuth absent (auth on by default)";;
        *)          fail "static config: unexpected value '$v'";;
    esac
else
    if grep -Eq '"dangerouslyDisableDeviceAuth"[[:space:]]*:[[:space:]]*true' "$CONF"; then
        fail "static config still disables device auth"
    else
        pass "static config does not hardcode device-auth disable"
    fi
fi

# ── B. Runtime-effective path: inject-token.js output ──────────────────────
# This is the security-critical assertion: inject-token.js is what the
# container actually runs, and it currently re-forces the insecure default.
if [[ ! -f "$INJECT" ]]; then
    fail "config/openclaw/inject-token.js missing"
elif (( ! have_node )); then
    skip "node unavailable — cannot exercise inject-token.js runtime path"
else
    run_inject() { # $1 = optin value ("" = unset)  -> echoes patched flag
        local optin="$1"
        local td; td="$(mktemp -d)"
        mkdir -p "$td/.openclaw"
        # Seed with a SECURE source config (post-fix static default).
        cat > "$td/.openclaw/openclaw.json" <<'JSON'
{ "gateway": { "mode": "local", "controlUi": {} } }
JSON
        (
            cd "$td" || exit 3
            export HOME="$td"
            export OPENCLAW_GATEWAY_TOKEN="tkn"
            export OPENCLAW_CONTROL_UI_HTML="$td/index.html"
            export OPENCLAW_AUTO_TOKEN_JS="$td/auto-token.js"
            : > "$td/index.html"
            if [[ -n "$optin" ]]; then export "$OPTIN_ENV=$optin"; else unset "$OPTIN_ENV"; fi
            node "$INJECT" >/dev/null 2>&1 || true
        )
        if (( have_jq )); then
            jq -r '.gateway.controlUi.dangerouslyDisableDeviceAuth // "ABSENT"' \
                "$td/.openclaw/openclaw.json" 2>/dev/null
        else
            json_get "$td/.openclaw/openclaw.json" \
              "d.get('gateway',{}).get('controlUi',{}).get('dangerouslyDisableDeviceAuth','ABSENT')"
        fi
        rm -rf "$td"
    }

    default_flag="$(run_inject "")"
    case "$default_flag" in
        true|True)
            fail "RUNTIME: inject-token.js still forces dangerouslyDisableDeviceAuth=true with NO opt-in (gateway unauthenticated by default)";;
        false|False|ABSENT|null|"")
            pass "RUNTIME: inject-token.js leaves device auth ENABLED when opt-in unset (flag=$default_flag)";;
        *)
            fail "RUNTIME: unexpected inject-token.js flag value '$default_flag'";;
    esac

    optin_flag="$(run_inject "true")"
    case "$optin_flag" in
        true|True)
            pass "RUNTIME: explicit opt-in ($OPTIN_ENV=true) deliberately disables device auth";;
        *)
            # Not fatal to the security goal, but the opt-in escape hatch
            # should still work; flag as a soft failure.
            fail "RUNTIME: opt-in $OPTIN_ENV=true did not disable device auth (flag=$optin_flag) — escape hatch broken";;
    esac
fi

# ── C. compose entrypoint ──────────────────────────────────────────────────
if [[ ! -f "$COMPOSE" ]]; then
    fail "extensions/services/openclaw/compose.yaml missing"
else
    if grep -Eq -- '--allow-unconfigured' "$COMPOSE" \
       && grep -Eq -- '--bind[[:space:]]+lan' "$COMPOSE"; then
        fail "entrypoint still runs --allow-unconfigured --bind lan (unauth LAN gateway)"
    else
        pass "entrypoint no longer combines --allow-unconfigured with --bind lan"
    fi
fi

# ── D. Localhost path unaffected ───────────────────────────────────────────
if [[ -f "$COMPOSE" ]]; then
    if grep -Eq 'BIND_ADDRESS:-127\.0\.0\.1' "$COMPOSE"; then
        pass "BIND_ADDRESS still defaults to 127.0.0.1 (localhost unaffected)"
    else
        fail "BIND_ADDRESS default changed away from 127.0.0.1"
    fi
    if grep -Eq '\$\{BIND_ADDRESS:-127\.0\.0\.1\}:\$\{OPENCLAW_PORT:-7860\}:18789' "$COMPOSE"; then
        pass "port mapping 127.0.0.1:7860->18789 unchanged"
    else
        fail "openclaw port mapping changed"
    fi
fi

echo
echo "== #1270 results: $PASS passed, $FAIL failed, $SKIP skipped =="
[[ $FAIL -eq 0 ]] || exit 1
