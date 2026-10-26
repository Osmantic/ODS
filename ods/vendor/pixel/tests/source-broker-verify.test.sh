#!/usr/bin/env bash
# ./pixel verify's Source Broker checks, run on their own against fake units and files.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT

pixel_die() { printf 'DIE: %s\n' "$*"; exit 1; }
pixel_warn() { printf 'WARN: %s\n' "$*"; }
# The fake broker's units: the timer is active, the refresh unit reports FAKE_ACTIVE_STATE.
pixel_courier_systemctl_read() {
  case "$*" in
    "is-active --quiet "*) return 0 ;;
    *" -p ActiveState --value") printf '%s\n' "${FAKE_ACTIVE_STATE:-inactive}" ;;
    *" -p Result --value") printf 'success\n' ;;
    *) printf 'unexpected systemctl call: %s\n' "$*" >&2; return 2 ;;
  esac
}
# shellcheck source=scripts/lib/source-broker-verify.sh
source "$ROOT/scripts/lib/source-broker-verify.sh"

export PIXEL_SOURCE_PROJECTION_DIR="$tmp/projection" PIXEL_SOURCE_TOKEN_PATH="$tmp/private/google-token.json"
export XDG_CONFIG_HOME="$tmp/config" PIXEL_LIMB_EMAIL_ENABLED=1 PIXEL_LIMB_CALENDAR_ENABLED=1 PIXEL_LIMB_SOCIAL_ENABLED=0
mkdir -p "$PIXEL_SOURCE_PROJECTION_DIR" "$XDG_CONFIG_HOME/pixel-agent" "$tmp/private"
printf "PIXEL_SOURCE_PROJECTION_DIR='%s'\n" "$PIXEL_SOURCE_PROJECTION_DIR" > "$XDG_CONFIG_HOME/pixel-agent/gateway.env"

projection() {
  printf '{"schemaVersion":1,"enabled":%s,"records":[],"boundary":{"projectionOnly":true,"rawContentStored":false}}\n' "$2" \
    > "$PIXEL_SOURCE_PROJECTION_DIR/$1.json"
}
status() {
  local email=$1 calendar=$2
  printf '{"schemaVersion":1,"kind":"pixel-source-broker-status","boundary":{"contentFree":true,"credentialsIncluded":false},"sources":{"email":%s,"calendar":%s,"social":{"enabled":false,"state":"disabled","errorClass":null}}}\n' \
    "$email" "$calendar" > "$PIXEL_SOURCE_PROJECTION_DIR/status.json"
}
expect_pass() {
  local name=$1 output
  output=$( (pixel_verify_source_broker) 2>&1 ) || { printf 'FAIL %s: verify refused\n%s\n' "$name" "$output" >&2; exit 1; }
  printf '%s\n' "$output"
}
expect_refusal() {
  local name=$1 message=$2 output
  if output=$( (pixel_verify_source_broker) 2>&1 ); then
    printf 'FAIL %s: verify accepted\n%s\n' "$name" "$output" >&2; exit 1
  fi
  grep -F "$message" <<<"$output" >/dev/null || { printf 'FAIL %s: wrong refusal\n%s\n' "$name" "$output" >&2; exit 1; }
}

# A connection made after install: the first refresh is still running and nothing is written yet.
FAKE_ACTIVE_STATE=activating expect_pass first-sync | grep -F 'WARN: Source Broker is finishing its first refresh' >/dev/null

# The same missing status with no refresh running is a broken broker.
FAKE_ACTIVE_STATE=inactive expect_refusal stopped-without-status 'Invalid Source Broker projection: email'
projection email true; projection calendar true; projection social false
FAKE_ACTIVE_STATE=inactive expect_refusal status-missing 'Source Broker status is missing or invalid'

# A healthy broker passes silently; a source the provider refuses is a warning naming its cause.
ok='{"enabled":true,"state":"ok","errorClass":null}'
status "$ok" "$ok"
[[ -z "$(expect_pass healthy)" ]] || { echo "FAIL healthy: unexpected output" >&2; exit 1; }
status '{"enabled":true,"state":"error","errorClass":"rate-limited"}' "$ok"
expect_pass source-error | grep -F 'WARN: Source Broker cannot refresh email right now (rate-limited)' >/dev/null

# A status that disagrees with the limbs, or a token the gateway owner can read, fails.
status '{"enabled":false,"state":"disabled","errorClass":null}' "$ok"
expect_refusal limb-mismatch 'Source Broker status does not match the email limb state'
status "$ok" "$ok"
: > "$PIXEL_SOURCE_TOKEN_PATH"
expect_refusal readable-token 'Gateway owner can still read the isolated Google credential'
echo "Source Broker verify checks passed"
