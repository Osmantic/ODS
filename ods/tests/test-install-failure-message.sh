#!/usr/bin/env bash
# A failure in a phase that only checks the host and chooses a route (01, 02,
# 02b) leads with "Nothing was changed" and says an existing install keeps
# working. A fresh install (no .env when the run started) that stops in phase
# 03 or 04 leads with "Nothing was set up yet" and says to run the same command
# again. A failure after ODS files change, or in phase 03/04 of a rerun over an
# installation, keeps the partial-state guidance. All name the log (with its
# Windows path under WSL) and end with the Discord help line.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail() { echo "FAIL: $*" >&2; exit 1; }

body="$(awk '/^cleanup_on_error\(\) \{/ { emit = 1 }
    emit { print }
    emit && /^}$/ { exit }' "$ROOT/install-core.sh")"
[[ -n "$body" ]] || fail "cleanup_on_error was not found in install-core.sh"

# failure_output PHASE [WSL_DISTRO_NAME] [ODS_EXISTING_INSTALL]
# Without the third argument ODS_EXISTING_INSTALL stays unset.
failure_output() {
    ( set +e; unset ODS_EXISTING_INSTALL; eval "$body"; INSTALL_PHASE="$1"; INSTALL_DIR=/opt/ods
      LOG_FILE=/tmp/ods.log; WSL_DISTRO_NAME="${2:-}"; [[ -z "${3:-}" ]] || ODS_EXISTING_INSTALL="$3"
      false; cleanup_on_error ) 2>&1 || true
}

for phase in 01-preflight 02-detection 02b-external-services; do
    output="$(failure_output "$phase")"
    head -n 3 <<<"$output" | grep -qF "Nothing was changed: the install stopped during its checks ($phase)." \
        || fail "$phase did not lead with nothing was changed: $output"
    grep -qF "An existing ODS installation keeps working as it was." <<<"$output" \
        || fail "$phase did not say the existing install keeps working: $output"
    grep -qF "Installation failed" <<<"$output" && fail "$phase still said the installation failed"
    grep -qF "Partial state may exist" <<<"$output" && fail "$phase still warned about partial state"
    grep -qF "Log file: /tmp/ods.log" <<<"$output" || fail "$phase lost the log file: $output"
    grep -qF "From Windows" <<<"$output" && fail "$phase named a Windows path outside WSL"
    grep -qF "https://discord.gg/4ntNp9MAwC" <<<"$output" || fail "$phase lost the help line"
done

# Fleet 2026-10-18: a port in use stopped a fresh install in phase 04, and the
# partial-state advice ("do not delete the directory") contradicted rerunning.
for phase in 03-features 04-requirements; do
    output="$(failure_output "$phase" "" false)"
    head -n 3 <<<"$output" | grep -qF "Nothing was set up yet: the install stopped before configuring ODS ($phase)." \
        || fail "fresh $phase did not lead with nothing was set up: $output"
    grep -qF "Fix the problem above, then run the same command again." <<<"$output" \
        || fail "fresh $phase did not say to run the same command again: $output"
    grep -qF "Installation failed" <<<"$output" && fail "fresh $phase still said the installation failed"
    grep -qF "Partial state may exist" <<<"$output" && fail "fresh $phase still warned about partial state"
    grep -qF "Do not delete" <<<"$output" && fail "fresh $phase still said not to delete the directory"
    grep -qF "existing ODS installation" <<<"$output" && fail "fresh $phase spoke of an existing installation"
    grep -qF "Log file: /tmp/ods.log" <<<"$output" || fail "fresh $phase lost the log file: $output"
    grep -qF "https://discord.gg/4ntNp9MAwC" <<<"$output" || fail "fresh $phase lost the help line"
done

# Phase 03 and 04 keep the partial-state guidance on a rerun over an
# installation and when the install state is unknown; phase 05 and later keep
# it on a fresh install too.
for state in "03-features true" "04-requirements true" "03-features unset" "04-requirements unset" \
    "05-docker false" "06-directories false" "11-services false" "06-directories true" "11-services true"; do
    read -r phase existing <<<"$state"
    [[ "$existing" != unset ]] || existing=""
    output="$(failure_output "$phase" "" "$existing")"
    grep -qF "[ERROR] Installation failed during phase: $phase" <<<"$output" || fail "$state lost its failure line: $output"
    grep -qF "Partial state may exist at:" <<<"$output" || fail "$state lost the partial-state guidance: $output"
    grep -qF "Nothing was" <<<"$output" && fail "$state claimed nothing was changed or set up"
    grep -qF "https://discord.gg/4ntNp9MAwC" <<<"$output" || fail "$state lost the help line"
done

# The trap trusts ODS_EXISTING_INSTALL, so install-core.sh records it before the
# first phase runs and nothing in the installer reassigns it.
awk 'index($0, "source \"$SCRIPT_DIR/installers/phases/") && !first { first = NR }
    /ODS_EXISTING_INSTALL=/ { seen = 1; if (first) late = 1 }
    END { exit (seen && !late) ? 0 : 1 }' "$ROOT/install-core.sh" \
    || fail "install-core.sh must set ODS_EXISTING_INSTALL before the first phase, and only there"
if grep -rn 'ODS_EXISTING_INSTALL=' "$ROOT/installers"; then
    fail "an installer phase or library reassigns ODS_EXISTING_INSTALL"
fi

# Windows setup runs the installer in WSL: the log also gets its Windows path.
output="$(failure_output 02b-external-services Ubuntu-24.04)"
grep -qF 'From Windows: \\wsl$\Ubuntu-24.04\tmp\ods.log' <<<"$output" \
    || fail "WSL did not give the log's Windows path: $output"

echo "PASS: install failures say whether anything changed, where the log is, and point at help"
