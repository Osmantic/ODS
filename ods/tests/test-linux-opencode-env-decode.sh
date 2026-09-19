#!/usr/bin/env bash
# test-linux-opencode-env-decode.sh
#
# Phase 07 reads ODS_MODE / LITELLM_* back out of the .env written by
# phase 06 to choose the OpenCode provider. Reading them with raw
# grep|cut keeps dotenv quoting and CRLF endings, so a valid
# ODS_MODE="lemonade" fails the `== "lemonade"` check and OpenCode is
# pointed at the wrong backend. The phase must decode values with the
# same grammar as lib/safe-env.sh (the pattern phase 06 already uses).
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PHASE="$ROOT/installers/phases/07-devtools.sh"

pass=0
fail=0
ok()  { echo "PASS: $1"; pass=$((pass + 1)); }
bad() { echo "FAIL: $1"; fail=$((fail + 1)); }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

export HOME="$TMP/home"
mkdir -p "$HOME/.opencode/bin" "$HOME/bin"
touch "$HOME/.bashrc"

# Hermetic tool stubs. opencode is "installed" so the phase writes its
# config; node/npm/claude/codex resolve so the phase skips network work.
# shellcheck disable=SC2016 # the stubs are literal script bodies, not expansions
printf '#!/bin/sh\n[ "$1" = "-p" ] && echo 22\nexit 0\n' > "$HOME/bin/node"
printf '#!/bin/sh\nexit 0\n' > "$HOME/bin/npm"
printf '#!/bin/sh\nexit 0\n' > "$HOME/bin/claude"
printf '#!/bin/sh\nexit 0\n' > "$HOME/bin/codex"
printf '#!/bin/sh\nexit 0\n' > "$HOME/.opencode/bin/opencode"
chmod +x "$HOME/bin/"* "$HOME/.opencode/bin/opencode"
export PATH="$HOME/bin:$PATH"

export SCRIPT_DIR="$ROOT"
export INSTALL_DIR="$TMP/install"
mkdir -p "$INSTALL_DIR"
export DRY_RUN=false ENABLE_OPENCODE=true
export LLM_MODEL=test-model MAX_CONTEXT=8192
export LOG_FILE="$TMP/install.log" PKG_MANAGER=apt
export OLLAMA_PORT='' LITELLM_PORT='' LITELLM_KEY='' ODS_MODEL_SWITCHBOARD=''

# Helpers install-core.sh normally provides.
ods_progress() { :; }
ai()          { :; }
ai_ok()       { :; }
ai_warn()     { :; }
ai_err()      { printf '%s\n' "$*" >&2; }
log()         { :; }
ods_sudo_available() { return 1; }

CFG="$HOME/.config/opencode/opencode.json"

run_phase() {
    unset ODS_MODE
    rm -f "$CFG"
    . "$PHASE"
}

# Quoted + CRLF .env (a standard dotenv formatting) must still select
# the LiteLLM branch and pass the key through literally.
printf 'OLLAMA_PORT=8080\r\nODS_MODE="lemonade"\r\nLITELLM_PORT="4000"\r\nLITELLM_KEY="test-key-123"\r\nODS_MODEL_SWITCHBOARD=disabled\r\n' > "$INSTALL_DIR/.env"
run_phase
if [[ -f "$CFG" ]]; then
    if grep -q '"baseURL": "http://127.0.0.1:4000/v1"' "$CFG"; then
        ok "quoted ODS_MODE selects LiteLLM provider"
    else
        bad "quoted ODS_MODE did not select LiteLLM provider"
    fi
    if grep -q '"apiKey": "test-key-123"' "$CFG"; then
        ok "quoted LITELLM_KEY decoded literally"
    else
        bad "LITELLM_KEY was not decoded"
    fi
else
    bad "opencode.json was not written (quoted .env)"
fi

# Plain unquoted values keep working.
printf 'OLLAMA_PORT=8080\nODS_MODE=lemonade\nLITELLM_PORT=4000\nLITELLM_KEY=plain-key\nODS_MODEL_SWITCHBOARD=disabled\n' > "$INSTALL_DIR/.env"
run_phase
if [[ -f "$CFG" ]]; then
    if grep -q '"baseURL": "http://127.0.0.1:4000/v1"' "$CFG"; then
        ok "unquoted ODS_MODE still selects LiteLLM provider"
    else
        bad "unquoted ODS_MODE regressed"
    fi
else
    bad "opencode.json was not written (unquoted .env)"
fi

echo
echo "Result: $pass passed, $fail failed"
[[ $fail -eq 0 ]]
