#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/scripts/ap-mode.sh"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_eq() {
  local actual="$1"
  local expected="$2"
  local label="$3"
  [[ "$actual" == "$expected" ]] || fail "${label}: expected ${expected}, got ${actual}"
}

assert_fails() {
  local label="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    fail "${label}: expected command to fail"
  fi
}

assert_eq "$(_netmask_to_prefix 255.255.255.0)" "24" "netmask /24"
assert_eq "$(_netmask_to_prefix 255.255.254.0)" "23" "netmask /23"
assert_eq "$(_netmask_to_prefix 255.255.255.128)" "25" "netmask /25"
assert_fails "non-contiguous netmask" _netmask_to_prefix 255.0.255.0
assert_fails "too few netmask octets" _netmask_to_prefix 255.255.0

ODS_AP_PASSWORD="changeme-set-per-device"
assert_fails "placeholder AP password" require_password

ODS_AP_PASSWORD="1234567"
assert_fails "short AP password" require_password

ODS_AP_PASSWORD="unique-device-pass"
require_password >/dev/null

printf 'AP mode helper checks passed\n'
