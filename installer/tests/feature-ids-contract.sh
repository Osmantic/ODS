#!/usr/bin/env bash
# Contract: every feature id the GUI feature picker can send to the Tauri
# start_install command must be accepted by the backend allowlist.
#
# Regression coverage for the Private Search option: it had no install.sh
# flag to map to (SearXNG is auto-derived from recommended/agent features in
# installers/phases/03-features.sh) and was absent from ALLOWED_FEATURES in
# commands.rs, so selecting it -- or clicking Select All -- made
# start_install fail with "Unsupported feature: search".
#
# The always-on "chat" entry is exempt: it is locked (not toggleable) and its
# backend handling is covered by a separate change.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FEATURES_TSX="$ROOT_DIR/src/pages/Features.tsx"
COMMANDS_RS="$ROOT_DIR/src-tauri/src/commands.rs"

fail() { echo "[FAIL] $*" >&2; exit 1; }

[[ -f "$FEATURES_TSX" ]] || fail "missing $FEATURES_TSX"
[[ -f "$COMMANDS_RS" ]] || fail "missing $COMMANDS_RS"

# Ids declared in the FEATURES option array.
mapfile -t ui_ids < <(grep -oE '^\s*id: "[a-z_]+"' "$FEATURES_TSX" | grep -oE '"[a-z_]+"' | tr -d '"')
[[ ${#ui_ids[@]} -gt 0 ]] || fail "no feature ids found in Features.tsx"

# Ids accepted by the backend: const ALLOWED_FEATURES: &[&str] = &[...]
mapfile -t allowed < <(
    grep -A2 'ALLOWED_FEATURES' "$COMMANDS_RS" \
        | grep -oE '"[a-z_]+"' | tr -d '"'
)
[[ ${#allowed[@]} -gt 0 ]] || fail "no ALLOWED_FEATURES entries found in commands.rs"

failures=0
for id in "${ui_ids[@]}"; do
    [[ "$id" == "chat" ]] && continue  # always-on core payload; see PR removing it
    ok=0
    for a in "${allowed[@]}"; do
        [[ "$id" == "$a" ]] && { ok=1; break; }
    done
    if [[ $ok -eq 0 ]]; then
        echo "[FAIL] Features.tsx id '$id' is rejected by start_install (not in ALLOWED_FEATURES)" >&2
        failures=1
    fi
done

[[ $failures -eq 0 ]] || exit 1
echo "[PASS] every selectable GUI feature id is accepted by start_install"
