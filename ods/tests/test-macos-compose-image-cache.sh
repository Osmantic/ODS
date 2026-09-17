#!/usr/bin/env bash
# Behavioral coverage for the macOS Compose image cache preflight.

if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
    for candidate in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$candidate" ]] && exec "$candidate" "$0" "$@"
    done
    echo "[FAIL] Bash 4+ is required" >&2
    exit 1
fi

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$ROOT_DIR/installers/macos/install-macos.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

extract_installer_function() {
    awk -v function_name="$1" '
        $0 ~ "^[[:space:]]*" function_name "\\(\\) \\{" {
            capture = 1
        }
        capture {
            print
        }
        capture && $0 ~ "^    \\}$" {
            exit
        }
    ' "$INSTALLER"
}

for function_name in \
    _macos_is_local_image \
    _macos_compose_external_images \
    _macos_normalize_image_platform \
    _macos_cached_image_platform \
    _macos_pull_image_with_retry; do
    function_body="$(extract_installer_function "$function_name")"
    [[ -n "$function_body" ]] || fail "could not extract $function_name"
    eval "$function_body"
done

export ODS_LOG_FILE="$TMP_DIR/install.log"
export ODS_DOCKER_PULL_MAX_ATTEMPTS=2
# Used by the installer function loaded with eval below.
# shellcheck disable=SC2034
COMPOSE_FLAGS=()
MOCK_DOCKER_CALLS="$TMP_DIR/docker-calls.log"
MOCK_INSPECT_EXIT=1
MOCK_INSPECT_OUTPUT=""
MOCK_PULL_EXIT=0
MOCK_COMPOSE_JSON=""

ai() { :; }
ai_ok() { :; }
ai_warn() { :; }
ai_err() { :; }
log() { :; }
sleep() { :; }

docker() {
    printf '%s\n' "$*" >> "$MOCK_DOCKER_CALLS"
    if [[ "$1" == "image" && "$2" == "inspect" ]]; then
        [[ "$MOCK_INSPECT_EXIT" -eq 0 ]] || return "$MOCK_INSPECT_EXIT"
        printf '%s\n' "$MOCK_INSPECT_OUTPUT"
        return 0
    fi
    if [[ "$1" == "pull" ]]; then
        return "$MOCK_PULL_EXIT"
    fi
    if [[ "$1" == "compose" && "$*" == *"config --format json"* ]]; then
        printf '%s\n' "$MOCK_COMPOSE_JSON"
        return 0
    fi
    fail "unexpected docker invocation: $*"
}

reset_docker_mock() {
    : > "$MOCK_DOCKER_CALLS"
    MOCK_INSPECT_EXIT=1
    MOCK_INSPECT_OUTPUT=""
    MOCK_PULL_EXIT=0
}

assert_call_count() {
    local pattern="$1" expected="$2" actual
    actual="$(grep -c -- "$pattern" "$MOCK_DOCKER_CALLS" || true)"
    [[ "$actual" == "$expected" ]] \
        || fail "expected $expected calls matching '$pattern', got $actual"
}

assert_normalized() {
    local input="$1" expected="$2" actual
    actual="$(_macos_normalize_image_platform "$input")" \
        || fail "could not normalize platform '$input'"
    [[ "$actual" == "$expected" ]] \
        || fail "platform '$input' normalized to '$actual', expected '$expected'"
}

assert_normalized "linux/amd64" "linux/amd64"
assert_normalized "AMD64" "linux/amd64"
assert_normalized "linux/x86_64" "linux/amd64"
assert_normalized "linux/arm64" "linux/arm64"
assert_normalized "aarch64" "linux/arm64"
assert_normalized '"linux/arm64/v8"' "linux/arm64"
if _macos_normalize_image_platform "linux/arm/v7" >/dev/null 2>&1; then
    fail "unsupported architecture unexpectedly normalized"
fi
pass "platform aliases and inspect output variants normalize safely"

MOCK_COMPOSE_JSON='{
  "services": {
    "tei": {"image": "ghcr.io/example/tei:latest", "platform": "linux/amd64"},
    "cache": {"image": "redis:7"},
    "local": {"image": "ods-dashboard-api:latest"},
    "built": {"image": "ignored:latest", "build": {"context": "."}}
  }
}'
compose_images="$(_macos_compose_external_images)"
expected_pinned=$'ghcr.io/example/tei:latest\tlinux/amd64'
expected_unpinned=$'redis:7\t'
grep -Fqx "$expected_pinned" <<< "$compose_images" \
    || fail "compose platform pin was not preserved"
grep -Fqx "$expected_unpinned" <<< "$compose_images" \
    || fail "unpinned compose image was not preserved"
[[ "$compose_images" != *"ods-dashboard-api"* && "$compose_images" != *"ignored:latest"* ]] \
    || fail "local or built image leaked into external pre-pull set"
pass "Compose JSON parsing preserves platform pins and filters local builds"

reset_docker_mock
_macos_pull_image_with_retry "ghcr.io/example/tei:latest" "linux/amd64" \
    || fail "absent cache did not pull the requested platform"
assert_call_count "^image inspect " 1
assert_call_count "^pull --platform linux/amd64 ghcr.io/example/tei:latest$" 1
pass "absent cache pulls the pinned platform"

reset_docker_mock
MOCK_INSPECT_EXIT=0
MOCK_INSPECT_OUTPUT="linux/amd64"
MOCK_PULL_EXIT=1
_macos_pull_image_with_retry "ghcr.io/example/tei:latest" "linux/amd64" \
    || fail "matching cached image was not accepted"
assert_call_count "^image inspect " 1
assert_call_count "^pull " 0
pass "matching cached image is reused without registry access"

reset_docker_mock
MOCK_INSPECT_EXIT=0
MOCK_INSPECT_OUTPUT="linux/arm64"
_macos_pull_image_with_retry "ghcr.io/example/tei:latest" "linux/amd64" \
    || fail "mismatched cache was not remediated"
assert_call_count "^pull --platform linux/amd64 ghcr.io/example/tei:latest$" 1
pass "mismatched cached architecture is replaced by a pinned pull"

reset_docker_mock
MOCK_INSPECT_EXIT=0
MOCK_INSPECT_OUTPUT='"linux/aarch64/v8"'
MOCK_PULL_EXIT=1
_macos_pull_image_with_retry "ghcr.io/example/arm-service:latest" "linux/arm64" \
    || fail "matching arm64 cache was not accepted while offline"
assert_call_count "^pull " 0
pass "offline rerun succeeds with a normalized matching cache"

reset_docker_mock
MOCK_INSPECT_EXIT=0
MOCK_INSPECT_OUTPUT="linux/arm64"
MOCK_PULL_EXIT=1
if _macos_pull_image_with_retry "ghcr.io/example/tei:latest" "linux/amd64"; then
    fail "mismatched cache unexpectedly passed after pull failures"
fi
assert_call_count "^pull --platform linux/amd64 ghcr.io/example/tei:latest$" 2
pass "pull failure remains fatal when the cached platform is wrong"

echo "[OK] macOS Compose image cache preflight passed"
