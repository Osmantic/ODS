#!/usr/bin/env bash
# Regression contract for the AMD GTT initramfs rebuild path.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT_DIR/installers/phases/10-amd-tuning.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

PASS=0
FAIL=0

pass() {
    echo "[PASS] $1"
    PASS=$((PASS + 1))
}

fail() {
    echo "[FAIL] $1"
    FAIL=$((FAIL + 1))
}

extract_rebuild_helper() {
    awk '
        /^    _phase10_rebuild_initramfs\(\) \{/ { capture=1 }
        capture {
            line=$0
            sub(/^    /, "", line)
            print line
            if ($0 == "    }") exit
        }
    ' "$PHASE"
}

HELPER_TEXT="$(extract_rebuild_helper)"
if [[ -n "$HELPER_TEXT" ]]; then
    pass "phase exposes the initramfs rebuild helper"
else
    fail "phase must expose _phase10_rebuild_initramfs"
fi

run_success_case() {
    local tool="$1" expected="$2"
    local case_dir="$TMP_DIR/$tool" call_log="$TMP_DIR/$tool.calls"
    mkdir -p "$case_dir"
    : > "$case_dir/$tool"
    chmod +x "$case_dir/$tool"

    if (
        PATH="$case_dir"
        _phase10_privileged() { printf '%s\n' "$*" >> "$call_log"; }
        eval "$HELPER_TEXT"
        _phase10_rebuild_initramfs
    ); then
        if [[ "$(< "$call_log")" == "$expected" ]]; then
            pass "$tool is invoked with the distribution-specific arguments"
        else
            fail "$tool invocation mismatch: expected '$expected', got '$(< "$call_log")'"
        fi
    else
        fail "$tool should be accepted as an initramfs generator"
    fi
}

run_success_case update-initramfs "update-initramfs -u"
run_success_case dracut "dracut --force"
run_success_case mkinitcpio "mkinitcpio -P"

fallback_dir="$TMP_DIR/fallback"
fallback_log="$TMP_DIR/fallback.calls"
mkdir -p "$fallback_dir"
: > "$fallback_dir/update-initramfs"
: > "$fallback_dir/dracut"
chmod +x "$fallback_dir/update-initramfs" "$fallback_dir/dracut"
if (
    PATH="$fallback_dir"
    _phase10_privileged() {
        printf '%s\n' "$*" >> "$fallback_log"
        [[ "$1" != "update-initramfs" ]]
    }
    eval "$HELPER_TEXT"
    _phase10_rebuild_initramfs
); then
    if [[ "$(< "$fallback_log")" == $'update-initramfs -u\ndracut --force' ]]; then
        pass "a failed generator falls through to the next available tool"
    else
        fail "generator fallback order was not preserved"
    fi
else
    fail "dracut should recover a failed update-initramfs attempt"
fi

empty_path="$TMP_DIR/no-generator"
mkdir -p "$empty_path"
set +e
(
    PATH="$empty_path"
    _phase10_privileged() { printf '%s\n' "$*" >> "$TMP_DIR/unexpected.calls"; }
    eval "$HELPER_TEXT"
    _phase10_rebuild_initramfs
)
no_generator_status=$?
set -e
if [[ "$no_generator_status" -ne 0 && ! -e "$TMP_DIR/unexpected.calls" ]]; then
    pass "missing initramfs tooling fails without attempting a privileged command"
else
    fail "missing initramfs tooling must fail explicitly"
fi

failed_tool_dir="$TMP_DIR/failed-tool"
mkdir -p "$failed_tool_dir"
: > "$failed_tool_dir/mkinitcpio"
chmod +x "$failed_tool_dir/mkinitcpio"
set +e
(
    PATH="$failed_tool_dir"
    _phase10_privileged() { return 23; }
    eval "$HELPER_TEXT"
    _phase10_rebuild_initramfs
)
failed_tool_status=$?
set -e
if [[ "$failed_tool_status" -ne 0 ]]; then
    pass "initramfs generator failures propagate to the phase caller"
else
    fail "initramfs generator failure was hidden"
fi

gtt_block="$(awk '
    /# Install GTT memory optimization/ { capture=1 }
    capture { print }
    /# Configure kernel boot parameters/ { exit }
' "$PHASE")"
if [[ "$gtt_block" == *'if _phase10_rebuild_initramfs'* \
    && "$gtt_block" == *'GTT memory config was installed, but initramfs could not be rebuilt.'* ]]; then
    pass "GTT install success is gated on a successful initramfs rebuild"
else
    fail "GTT block must warn instead of reporting success when initramfs rebuild fails"
fi

echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
