#!/usr/bin/env bash
# Tests for the Windows installer custom-port contract.
#
# docker-compose and .env.example let users relocate service ports
# (DASHBOARD_PORT, N8N_PORT, PERPLEXICA_PORT, ...). Phase 04 already resolves
# WEBUI_PORT and the LLM port through Resolve-WindowsODSPort (process env ->
# persisted .env -> default), but the remaining preflight entries and several
# runtime call sites still hardcode the defaults. On a customized install the
# preflight check probes the wrong port -- either a false conflict that aborts
# a healthy install or a missed conflict that surfaces later as a compose
# bind failure -- the n8n health gate times out on the default port, the
# Perplexica auto-config POSTs to the wrong URL, and the desktop shortcut
# lands on the default port.
#
# These are hermetic static contracts (no PowerShell, no Docker required),
# matching the convention in test-windows-cli-enable-disable.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REQUIREMENTS="$ROOT_DIR/installers/windows/phases/04-requirements.ps1"
INSTALLER="$ROOT_DIR/installers/windows/install-windows.ps1"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

pass() { echo -e "${GREEN}OK${NC} $1"; }
fail() { echo -e "${RED}FAIL${NC} $1"; exit 1; }
info() { echo -e "${BLUE}i${NC} $1"; }

# Extract the $_portsToCheck construction block (from the ordered map start to
# the first foreach that consumes it), collapsing backtick line continuations
# so each Resolve-WindowsODSPort call reads as one line.
port_block() {
    awk '/_portsToCheck = \[ordered\]/{p=1} p{print} p&&/foreach \(\$svc in \$_portsToCheck/{exit}' \
        "$REQUIREMENTS" | tr -d '\r\n`'
}

info "Static: files exist"
[[ -s "$REQUIREMENTS" ]] || fail "04-requirements.ps1 missing"
[[ -s "$INSTALLER" ]] || fail "install-windows.ps1 missing"
pass "installer files exist"

info "Static: every preflight port resolves through Resolve-WindowsODSPort"
for pair in \
    'DASHBOARD_PORT:3001' \
    'DASHBOARD_API_PORT:3002' \
    'LITELLM_PORT:4000' \
    'SEARXNG_PORT:8888' \
    'TOKEN_SPY_PORT:3005' \
    'WHISPER_PORT' \
    'TTS_PORT:8880' \
    'N8N_PORT:5678' \
    'QDRANT_PORT:6333' \
    'EMBEDDINGS_PORT:8090' \
    'HERMES_PROXY_PORT:9120' \
    'OPENCLAW_PORT:7860' \
    'APE_PORT:7890' \
    'COMFYUI_PORT:8188' \
    'PERPLEXICA_PORT:3004' \
    'SHIELD_PORT:8085'; do
    key="${pair%%:*}"
    port_block | grep -Eq "Resolve-WindowsODSPort +-Name \"$key\"" \
        || fail "preflight does not resolve $key via Resolve-WindowsODSPort"
done
pass "all preflight ports honor env overrides"

info "Static: no bare default-port literals remain in the preflight map"
for port in 3001 3002 4000 8888 3005 8880 5678 6333 8090 9120 7860 7890 8188 3004 8085; do
    if port_block | grep -Eq "= +$port\b"; then
        fail "preflight still hardcodes port $port"
    fi
done
pass "preflight map has no hardcoded ports"

info "Static: n8n health check honors N8N_PORT"
grep -n 'n8n (Workflows)' "$INSTALLER" | grep -q 'localhost:5678' \
    && fail "n8n health check hardcodes localhost:5678"
awk '/enableWorkflows.*healthChecks|n8n \(Workflows\)/{c++} c&&/N8N_PORT/{found=1} END{exit !found}' \
    "$INSTALLER" || fail "n8n health check does not resolve N8N_PORT"
pass "n8n health check resolves N8N_PORT"

info "Static: Perplexica auto-config honors PERPLEXICA_PORT"
grep -n 'Set-PerplexicaConfig' "$INSTALLER" | grep -q 'PerplexicaPort 3004' \
    && fail "Set-PerplexicaConfig call hardcodes port 3004"
grep -Eq 'Set-PerplexicaConfig -PerplexicaPort \$' "$INSTALLER" \
    || fail "Set-PerplexicaConfig does not take a resolved port variable"
grep -n 'PERPLEXICA_PORT' "$INSTALLER" | grep -q '3004' \
    || fail "PERPLEXICA_PORT is not resolved with a 3004 default"
pass "Perplexica auto-config resolves PERPLEXICA_PORT"

info "Static: desktop shortcut honors DASHBOARD_PORT"
awk '/dashboardUrl/{print NR": "$0}' "$INSTALLER" | grep -q 'localhost:3001' \
    && fail "desktop shortcut hardcodes localhost:3001"
grep -Eq 'dashboardUrl += +"http://localhost:\$' "$INSTALLER" \
    || fail "desktop shortcut does not use a resolved port variable"
pass "desktop shortcut resolves DASHBOARD_PORT"

echo ""
echo "All Windows custom-port contracts hold."
