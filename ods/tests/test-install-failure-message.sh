#!/usr/bin/env bash
# A failure in a phase that only checks the host and chooses a route (01, 02,
# 02b) leads with "Nothing was changed" and says an existing install keeps
# working; a failure after ODS files change keeps the partial-state guidance.
# Both name the log (with its Windows path under WSL) and end with the
# Discord help line.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail() { echo "FAIL: $*" >&2; exit 1; }

body="$(awk '/^cleanup_on_error\(\) \{/ { emit = 1 }
    emit { print }
    emit && /^}$/ { exit }' "$ROOT/install-core.sh")"
[[ -n "$body" ]] || fail "cleanup_on_error was not found in install-core.sh"

failure_output() {
    ( set +e; eval "$body"; INSTALL_PHASE="$1"; INSTALL_DIR=/opt/ods; LOG_FILE=/tmp/ods.log
      WSL_DISTRO_NAME="${2:-}"; false; cleanup_on_error ) 2>&1 || true
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

for phase in 03-features 06-directories 11-services; do
    output="$(failure_output "$phase")"
    grep -qF "[ERROR] Installation failed during phase: $phase" <<<"$output" || fail "$phase lost its failure line: $output"
    grep -qF "Partial state may exist at:" <<<"$output" || fail "$phase lost the partial-state guidance: $output"
    grep -qF "Nothing was changed" <<<"$output" && fail "$phase claimed nothing was changed"
    grep -qF "https://discord.gg/4ntNp9MAwC" <<<"$output" || fail "$phase lost the help line"
done

# Windows setup runs the installer in WSL: the log also gets its Windows path.
output="$(failure_output 02b-external-services Ubuntu-24.04)"
grep -qF 'From Windows: \\wsl$\Ubuntu-24.04\tmp\ods.log' <<<"$output" \
    || fail "WSL did not give the log's Windows path: $output"

echo "PASS: install failures say whether anything changed, where the log is, and point at help"
