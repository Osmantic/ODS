#!/usr/bin/env bash

# Materialize the narrow broker-reader ACLs from trusted target configuration.
# These ACLs are deployment policy, not private backup content, so restores must
# rebuild them after broker startup and before verification. Never use recursive
# ACL changes here: authority, credential, policy, plan, and approval paths stay
# outside this explicit projection allowlist.

pixel_broker_acl_directory() {
  local path=$1 label=$2
  pixel_safe_absolute_dir "$path" "$label"
  if ! sudo test -d "$path" || sudo test -L "$path"; then
    pixel_die "$label is missing, linked, or not a directory"
  fi
}

pixel_broker_acl_file() {
  local path=$1 label=$2
  [[ "$path" == /* && "$path" != / && "$path" != *$'\n'* && "$path" != *$'\r'* ]] ||
    pixel_die "$label must be an absolute, non-root path"
  if ! sudo test -f "$path" || sudo test -L "$path"; then
    pixel_die "$label is missing, linked, or not a regular file"
  fi
}

pixel_apply_ops_reader_acls() {
  [[ ${PIXEL_OPS_BROKER_ENABLED:-0} == 1 ]] || return 0
  pixel_require_command setfacl
  local reader=${PIXEL_OPS_READER_USER:-}
  local state=${PIXEL_OPS_BROKER_STATE_DIR:-/var/lib/pixel-ops-broker}
  local results=${PIXEL_OPS_RESULT_DIR:-$state/results}
  local events=${PIXEL_OPS_EVENT_DIR:-$state/events}
  local inventory=${PIXEL_OPS_INVENTORY_PATH:-$state/inventory.json}
  [[ "$reader" =~ ^[A-Za-z_][A-Za-z0-9_.-]{0,63}$ ]] || pixel_die "Operations reader user is unsafe"
  pixel_broker_acl_directory "$state" "Operations state root"
  pixel_broker_acl_directory "$results" "Operations result projection"
  pixel_broker_acl_directory "$events" "Operations event projection"
  pixel_broker_acl_file "$inventory" "Operations inventory projection"
  sudo setfacl -m "u:$reader:--x,d:u:$reader:r--" "$state"
  sudo setfacl -m "u:$reader:r-x,d:u:$reader:r--" "$results"
  sudo setfacl -m "u:$reader:r-x,d:u:$reader:r--" "$events"
  sudo setfacl -m "u:$reader:r--" "$inventory"
  pixel_user_can_access "$reader" r "$inventory" || pixel_die "Gateway owner cannot read the Operations inventory projection"
}

pixel_apply_frontier_reader_acls() {
  [[ ${PIXEL_FRONTIER_BROKER_ENABLED:-0} == 1 ]] || return 0
  pixel_require_command setfacl
  local reader=${PIXEL_FRONTIER_READER_USER:-}
  local state=${PIXEL_FRONTIER_BROKER_STATE_DIR:-/var/lib/pixel-frontier-broker}
  local results=${PIXEL_FRONTIER_RESULT_DIR:-$state/results}
  local events=${PIXEL_FRONTIER_EVENT_DIR:-$state/events}
  local metrics=$state/metrics
  local qualifications=$metrics/qualifications
  local usage=$metrics/usage.json
  [[ "$reader" =~ ^[A-Za-z_][A-Za-z0-9_.-]{0,63}$ ]] || pixel_die "Frontier reader user is unsafe"
  pixel_broker_acl_directory "$state" "Frontier state root"
  pixel_broker_acl_directory "$results" "Frontier result projection"
  pixel_broker_acl_directory "$events" "Frontier event projection"
  pixel_broker_acl_directory "$metrics" "Frontier metrics projection"
  pixel_broker_acl_directory "$qualifications" "Frontier qualification projection"
  pixel_broker_acl_file "$usage" "Frontier usage projection"
  sudo setfacl -m "u:$reader:--x,d:u:$reader:r--" "$state"
  sudo setfacl -m "u:$reader:r-x,d:u:$reader:r--" "$results"
  sudo setfacl -m "u:$reader:r-x,d:u:$reader:r--" "$events"
  sudo setfacl -m "u:$reader:r-x,d:u:$reader:r--" "$metrics"
  sudo setfacl -m "u:$reader:r-x,d:u:$reader:r--" "$qualifications"
  sudo setfacl -m "u:$reader:r--" "$usage"
  pixel_user_can_access "$reader" r "$results" || pixel_die "Gateway owner cannot read Frontier result projections"
  pixel_user_can_access "$reader" r "$usage" || pixel_die "Gateway owner cannot read the Frontier usage projection"
}

pixel_apply_broker_reader_acls() {
  pixel_apply_ops_reader_acls
  pixel_apply_frontier_reader_acls
}
