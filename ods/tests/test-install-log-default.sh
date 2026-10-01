#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_tmp="$(mktemp -d)"
trap 'rm -f "$test_tmp"/*; rmdir "$test_tmp"' EXIT

# A pre-existing legacy log must not stop another account's default install.
printf 'legacy log\n' > "$test_tmp/ods-install.log"
printf 'legacy capabilities\n' > "$test_tmp/ods-capabilities.json"
printf 'legacy preflight\n' > "$test_tmp/ods-preflight-report.json"
chmod 600 "$test_tmp/ods-install.log"
export TMPDIR="$test_tmp"
unset LOG_FILE CAPABILITY_PROFILE_FILE PREFLIGHT_REPORT_FILE
source "$script_dir/installers/lib/constants.sh"
source "$script_dir/installers/lib/secure-log.sh"
[[ "$LOG_FILE" == "$test_tmp/ods-install-$(id -u).log" ]]
[[ "$CAPABILITY_PROFILE_FILE" == "$test_tmp/ods-capabilities-$(id -u).json" ]]
[[ "$PREFLIGHT_REPORT_FILE" == "$test_tmp/ods-preflight-report-$(id -u).json" ]]
ods_prepare_install_log "$LOG_FILE"
if [[ "$(uname -s)" == Darwin ]]; then
    [[ "$(stat -f '%Lp' "$LOG_FILE")" == 600 ]]
else
    [[ "$(stat -c '%a' "$LOG_FILE")" == 600 ]]
fi
[[ "$(cat "$test_tmp/ods-install.log")" == 'legacy log' ]]
[[ "$(cat "$test_tmp/ods-capabilities.json")" == 'legacy capabilities' ]]
[[ "$(cat "$test_tmp/ods-preflight-report.json")" == 'legacy preflight' ]]

# Explicit artifact paths remain owner-controlled.
LOG_FILE="$test_tmp/explicit.log"
CAPABILITY_PROFILE_FILE="$test_tmp/explicit-capabilities.json"
PREFLIGHT_REPORT_FILE="$test_tmp/explicit-preflight.json"
source "$script_dir/installers/lib/constants.sh"
[[ "$LOG_FILE" == "$test_tmp/explicit.log" ]]
[[ "$CAPABILITY_PROFILE_FILE" == "$test_tmp/explicit-capabilities.json" ]]
[[ "$PREFLIGHT_REPORT_FILE" == "$test_tmp/explicit-preflight.json" ]]

printf 'PASS: per-user installer artifact defaults and explicit overrides\n'
