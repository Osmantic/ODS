#!/usr/bin/env bash
# Regression: platform CLIs must return failure for rejected or failed actions.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root_dir="$(cd "$script_dir/.." && pwd)"
macos_cli="$root_dir/installers/macos/ods-macos.sh"
windows_cli="$root_dir/installers/windows/ods.ps1"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

assert_failed_with() {
    local label="$1"
    local pattern="$2"
    shift 2

    local output_file="$tmp_dir/${label// /-}.out"
    local rc=0
    "$@" >"$output_file" 2>&1 || rc=$?
    if (( rc == 0 )); then
        printf '[FAIL] %s returned success\n' "$label" >&2
        cat "$output_file" >&2
        exit 1
    fi
    if ! grep -Fq -- "$pattern" "$output_file"; then
        printf '[FAIL] %s did not emit its failure receipt\n' "$label" >&2
        cat "$output_file" >&2
        exit 1
    fi
}

ODS_HOME="$tmp_dir/not-installed" NO_COLOR=1 \
    assert_failed_with "macOS unknown command" "Unknown command: not-a-command" \
        bash "$macos_cli" not-a-command

ODS_HOME="$tmp_dir/not-installed" NO_COLOR=1 \
    assert_failed_with "macOS logs missing service" "logs <service>" \
        bash "$macos_cli" logs

ODS_HOME="$tmp_dir/not-installed" NO_COLOR=1 \
    assert_failed_with "macOS missing native log" "No llama-server log file found" \
        bash "$macos_cli" logs llama-server

logs_block="$(awk '
    /function Invoke-Logs/ { in_block=1 }
    in_block { print }
    in_block && /function Invoke-ConfigShow/ { exit }
' "$windows_cli")"

grep -Fq 'Invoke-ODSDockerCompose -InstallDir $InstallDir' <<<"$logs_block" || {
    printf '[FAIL] Windows logs bypasses the exit-code-safe Compose wrapper\n' >&2
    exit 1
}
grep -Fq 'if ($logExit -ne 0)' <<<"$logs_block" || {
    printf '[FAIL] Windows logs does not reject a failed Compose command\n' >&2
    exit 1
}
grep -Fq 'Write-ODSComposeDiagnostics' <<<"$logs_block" || {
    printf '[FAIL] Windows logs failure omits the diagnostic receipt\n' >&2
    exit 1
}

missing_service_block="$(sed -n '/if (-not \$Service)/,/Test-Install/p' <<<"$logs_block")"
grep -Fq 'exit 1' <<<"$missing_service_block" || {
    printf '[FAIL] Windows logs without a service still returns success\n' >&2
    exit 1
}

default_block="$(sed -n '/default   {/,/^    }/p' "$windows_cli")"
grep -Fq 'Unknown command:' <<<"$default_block" && grep -Fq 'exit 1' <<<"$default_block" || {
    printf '[FAIL] Windows unknown commands still return success\n' >&2
    exit 1
}

printf '[PASS] platform CLIs propagate invalid and failed command status\n'
