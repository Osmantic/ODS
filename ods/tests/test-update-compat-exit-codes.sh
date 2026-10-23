#!/usr/bin/env bash
# `ods update` must exit non-zero when the version compatibility check refuses
# the update (installed version below min_compatible_ods_version). Declining
# an interactive prompt stays exit 0; a prompt nobody can answer (EOF on
# stdin, e.g. cron or `< /dev/null`) is a refusal and exits non-zero. Exercises the real cmd_update and
# _check_version_compat; nothing after the compatibility check may run.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${ODS_CLI_UNDER_TEST:-$ROOT_DIR/ods-cli}"
FIXTURE="$(mktemp -d)"
trap 'rm -rf "$FIXTURE"' EXIT

extract() {
    awk -v name="$1" '$0 ~ "^" name "\\(\\) \\{" {copy=1} copy{print} copy && /^}$/{exit}' "$TARGET"
}

# scenario: installed version, target version, minimum compatible, prompt reply
run_update() {
    local installed="$1" target="$2" minimum="$3" reply="$4"
    local dir="$FIXTURE/$installed-$target-$minimum-$reply"
    mkdir -p "$dir"
    printf 'ODS_VERSION=%s\n' "$installed" > "$dir/.env"
    printf '{"ods_version":"%s","min_compatible_ods_version":"%s"}\n' "$target" "$minimum" > "$dir/manifest.json"
    printf '#!/bin/sh\nexit 0\n' > "$dir/ods-update.sh"
    chmod +x "$dir/ods-update.sh"
    (
        set -euo pipefail
        INSTALL_DIR="$dir"
        VERSION="$installed"
        RED="" GREEN="" YELLOW="" BLUE="" CYAN="" NC=""
        check_install() { :; }
        load_env() { :; }
        log() { printf '%s\n' "$*"; }
        log_error() { printf 'ERROR: %s\n' "$*"; }
        warn() { printf 'WARN: %s\n' "$*"; }
        error() { log_error "$*"; exit 1; }
        success() { printf 'OK: %s\n' "$*"; }
        # Anything past the compatibility check is out of scope here.
        bash() { echo "UPDATE-PROCEEDED"; exit 99; }
        eval "$(extract _semver_major)"
        eval "$(extract _semver_lt)"
        eval "$(extract _manifest_field)"
        eval "$(extract _check_version_compat)"
        eval "$(extract cmd_update)"
        printf '%s' "$reply" | cmd_update
    )
}

expect_rc() {
    local want="$1" label="$2"; shift 2
    local rc=0 output
    output=$(run_update "$@" 2>&1) || rc=$?
    if [[ "$rc" != "$want" ]]; then
        echo "[FAIL] $label: expected exit $want, got $rc" >&2
        printf '%s\n' "$output" >&2
        exit 1
    fi
    if [[ "$output" == *UPDATE-PROCEEDED* ]]; then
        echo "[FAIL] $label: update continued past the compatibility check" >&2
        exit 1
    fi
    echo "[PASS] $label (exit $rc)"
}

expect_rc 1 "hard block below min_compatible_ods_version fails" 1.2.0 1.6.0 1.5.0 n
expect_rc 0 "declined major-version jump stays a cancellation" 1.0.0 3.0.0 0.0.0 n
expect_rc 0 "declined downgrade stays a cancellation" 3.0.0 2.0.0 0.0.0 n
expect_rc 1 "unanswerable major-version prompt fails" 1.0.0 3.0.0 0.0.0 ""
expect_rc 1 "unanswerable downgrade prompt fails" 3.0.0 2.0.0 0.0.0 ""
