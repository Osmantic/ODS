#!/usr/bin/env bash
# The Source Broker part of ./pixel verify, kept apart so it can be tested on its own.
#
# Usage:
#   source "$ROOT/scripts/lib/source-broker-verify.sh"
#   pixel_verify_source_broker
#
# Expects pixel_die, pixel_warn and pixel_courier_systemctl_read from common.sh, and the
# configured PIXEL_SOURCE_* and PIXEL_LIMB_* settings from pixel_load_env.
set -euo pipefail

pixel_verify_source_broker() {
  local broker_unit=${PIXEL_SOURCE_BROKER_UNIT:-pixel-source-broker.service}
  local broker_timer=${PIXEL_SOURCE_BROKER_TIMER:-pixel-source-broker.timer}
  local source_status="$PIXEL_SOURCE_PROJECTION_DIR/status.json"
  local direct_path source limb_enabled expected source_error
  pixel_courier_systemctl_read is-active --quiet "$broker_timer" || pixel_die "Source Broker timer is not active"
  if [[ ${PIXEL_CALENDAR_DIRECT_ENABLED:-0} == 1 ]]; then
    direct_path=${PIXEL_SOURCE_DIRECT_PATH_UNIT:-pixel-source-direct.path}
    pixel_courier_systemctl_read is-active --quiet "$direct_path" || pixel_die "Bounded direct Calendar watcher is not active"
  fi
  [[ ! -r "$PIXEL_SOURCE_TOKEN_PATH" ]] || pixel_die "Gateway owner can still read the isolated Google credential"
  ! grep -q '^PIXEL_GOOGLE_TOKEN_PATH=' "${XDG_CONFIG_HOME:-$HOME/.config}/pixel-agent/gateway.env" || pixel_die "Gateway environment still references a Google credential"
  # A source connected after install refreshes for the first time in the background, and
  # a large mailbox takes minutes. Until that refresh writes status.json there is nothing
  # to check yet; a status missing for any other reason still fails below.
  if [[ ! -e "$source_status" && "$(pixel_courier_systemctl_read show "$broker_unit" -p ActiveState --value)" == activating ]]; then
    pixel_warn "Source Broker is finishing its first refresh; Portal answers from it once that completes"
    return 0
  fi
  for source in email calendar social; do
    jq -e '.schemaVersion == 1 and .boundary.projectionOnly == true and .boundary.rawContentStored == false and (.records | type == "array")' "$PIXEL_SOURCE_PROJECTION_DIR/$source.json" >/dev/null || pixel_die "Invalid Source Broker projection: $source"
  done
  jq -e ".enabled == $([[ ${PIXEL_LIMB_EMAIL_ENABLED:-1} == 1 ]] && echo true || echo false)" "$PIXEL_SOURCE_PROJECTION_DIR/email.json" >/dev/null || pixel_die "Email projection does not match limb state"
  jq -e ".enabled == $([[ ${PIXEL_LIMB_CALENDAR_ENABLED:-1} == 1 ]] && echo true || echo false)" "$PIXEL_SOURCE_PROJECTION_DIR/calendar.json" >/dev/null || pixel_die "Calendar projection does not match limb state"
  jq -e ".enabled == $([[ ${PIXEL_LIMB_SOCIAL_ENABLED:-0} == 1 ]] && echo true || echo false)" "$PIXEL_SOURCE_PROJECTION_DIR/social.json" >/dev/null || pixel_die "Social projection does not match limb state"
  pixel_courier_systemctl_read show "$broker_unit" -p Result --value | grep -Fx success >/dev/null || pixel_die "Source Broker last refresh failed"
  # Each source reports on its own: a source the provider refuses is a warning with its
  # recorded cause, while a missing, malformed or limb-mismatched status file fails.
  jq -e '.schemaVersion == 1 and .kind == "pixel-source-broker-status" and .boundary.contentFree == true and .boundary.credentialsIncluded == false and (.sources | type == "object")' "$source_status" >/dev/null ||
    pixel_die "Source Broker status is missing or invalid; run sudo systemctl start ${broker_unit} and verify again"
  for source in email calendar social; do
    case "$source" in
      email) limb_enabled=${PIXEL_LIMB_EMAIL_ENABLED:-1} ;;
      calendar) limb_enabled=${PIXEL_LIMB_CALENDAR_ENABLED:-1} ;;
      social) limb_enabled=${PIXEL_LIMB_SOCIAL_ENABLED:-0} ;;
    esac
    expected=false; [[ $limb_enabled == 1 ]] && expected=true
    jq -e --arg source "$source" --argjson enabled "$expected" '.sources[$source] as $s | ($s | type == "object") and $s.enabled == $enabled and (if $enabled then ($s.state == "ok" or $s.state == "error") else $s.state == "disabled" end)' "$source_status" >/dev/null ||
      pixel_die "Source Broker status does not match the $source limb state"
    source_error=$(jq -r --arg source "$source" '.sources[$source] | select(.state == "error") | .errorClass // "unknown"' "$source_status")
    if [[ -n "$source_error" ]]; then
      pixel_warn "Source Broker cannot refresh $source right now ($source_error); Portal answers from its last snapshot until it recovers"
    fi
  done
}
