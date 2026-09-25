#!/usr/bin/env bash
# Transactional replacement of executable bytes for already-installed, enabled brokers.
set -euo pipefail

pixel_broker_enabled() {
  case "$1" in
    source) [[ ${PIXEL_SOURCE_BROKER_ENABLED:-1} == 1 ]] ;;
    ops) [[ ${PIXEL_OPS_BROKER_ENABLED:-0} == 1 ]] ;;
    frontier) [[ ${PIXEL_FRONTIER_BROKER_ENABLED:-0} == 1 ]] ;;
    *) return 1 ;;
  esac
}

pixel_broker_install_dir() {
  local value="" test_root resolved_root resolved_value
  case "$1" in
    source) value=${PIXEL_SOURCE_BROKER_INSTALL_DIR:-} ;;
    ops) value=${PIXEL_OPS_BROKER_INSTALL_DIR:-} ;;
    frontier) value=${PIXEL_FRONTIER_BROKER_INSTALL_DIR:-} ;;
    *) return 1 ;;
  esac
  [[ -n "$value" ]] || return 1
  pixel_safe_absolute_dir "$value" "PIXEL_${1^^}_BROKER_INSTALL_DIR"
  if pixel_broker_bytes_test_mode; then
    test_root=${PIXEL_BROKER_BYTES_TEST_ROOT:-}
    pixel_safe_absolute_dir "$test_root" PIXEL_BROKER_BYTES_TEST_ROOT
    [[ -d "$test_root" && ! -L "$test_root" ]] || pixel_die "Broker-byte test root is missing or linked"
    resolved_root=$(realpath -e -- "$test_root") || pixel_die "Broker-byte test root cannot be resolved"
    resolved_value=$(realpath -m -- "$value") || pixel_die "Broker-byte test path cannot be resolved"
    [[ "$resolved_value" == "$resolved_root/"* ]] || pixel_die "Broker-byte test path escapes the explicit test root: $value"
  fi
  printf '%s' "$value"
}

pixel_broker_unit() {
  case "$1" in
    source) printf '%s' "${PIXEL_SOURCE_BROKER_UNIT:-pixel-source-broker.service}" ;;
    ops) printf '%s' "${PIXEL_OPS_BROKER_UNIT:-pixel-ops-broker.service}" ;;
    frontier) printf '%s' "${PIXEL_FRONTIER_BROKER_UNIT:-pixel-frontier-broker.service}" ;;
    *) return 1 ;;
  esac
}

pixel_broker_byte_files() {
  case "$1" in
    source) printf '%s\n%s\n' 'broker.py|0755|source-broker/broker.py' 'action_journal/__init__.py|0644|source-broker/action_journal/__init__.py' ;;
    ops) printf '%s\n' 'broker.py|0755|ops-broker/broker.py' ;;
    frontier) printf '%s\n%s\n' 'broker.py|0755|frontier-broker/broker.py' 'verify-codex.py|0755|frontier-broker/verify-codex.py' ;;
    *) return 1 ;;
  esac
}

pixel_broker_limbs() {
  local limb install_dir
  for limb in source ops frontier; do
    pixel_broker_enabled "$limb" || continue
    install_dir=$(pixel_broker_install_dir "$limb") || continue
    # Any object at the primary executable path means the limb is installed enough
    # to require validation. A broken symlink or non-regular substitution must not
    # make an enabled broker disappear from the transaction.
    [[ -e "$install_dir/broker.py" || -L "$install_dir/broker.py" ]] || continue
    printf '%s\n' "$limb"
  done
}

pixel_broker_validate_configuration() {
  local limb install_dir
  for limb in source ops frontier; do
    pixel_broker_enabled "$limb" || continue
    case "$limb" in
      source) install_dir=${PIXEL_SOURCE_BROKER_INSTALL_DIR:-} ;;
      ops) install_dir=${PIXEL_OPS_BROKER_INSTALL_DIR:-} ;;
      frontier) install_dir=${PIXEL_FRONTIER_BROKER_INSTALL_DIR:-} ;;
    esac
    [[ -n "$install_dir" ]] || continue
    # Run validation in the current shell. A command substitution inside
    # pixel_broker_limbs would otherwise turn pixel_die into a status that the
    # optional-limb discovery loop could silently treat as "not installed".
    pixel_broker_install_dir "$limb" >/dev/null
  done
}

pixel_broker_bytes_test_mode() {
  [[ ${PIXEL_BROKER_BYTES_TESTING:-0} == 1 ]]
}

pixel_broker_bytes_require_trust() {
  local limbs limb install_dir
  pixel_broker_validate_configuration
  limbs=$(pixel_broker_limbs)
  [[ -n "$limbs" ]] || return 0
  for command in install sha256sum stat; do pixel_require_command "$command"; done
  for limb in $limbs; do
    install_dir=$(pixel_broker_install_dir "$limb")
    [[ -d "$install_dir" && ! -L "$install_dir" ]] || pixel_die "Broker install directory is missing or linked: $install_dir"
    pixel_broker_require_installed_file "$install_dir/broker.py" 0755
  done
  if pixel_release_operator_enabled && [[ ${PIXEL_BROKER_BYTES_DIRECT_SUDO:-0} != 1 ]]; then
    pixel_die "Privileged broker-byte transaction is outside the release operator; rerun the reviewed command with PIXEL_BROKER_BYTES_DIRECT_SUDO=1 to use the fixed direct sudo surface"
  fi
  if ! pixel_broker_bytes_test_mode; then
    pixel_require_command sudo
    sudo -v
  fi
}

pixel_broker_install_byte() {
  local source=$1 target=$2 mode=$3
  if pixel_broker_bytes_test_mode; then
    install -m "$mode" "$source" "$target"
  else
    sudo install -o root -g root -m "$mode" "$source" "$target"
  fi
}

pixel_broker_remove_byte() {
  local target=$1
  if pixel_broker_bytes_test_mode; then rm -f -- "$target"; else sudo rm -f -- "$target"; fi
}

pixel_broker_systemctl() {
  if pixel_broker_bytes_test_mode; then
    "${PIXEL_BROKER_BYTES_SYSTEMCTL_BIN:?PIXEL_BROKER_BYTES_SYSTEMCTL_BIN is required in test mode}" "$@"
  else
    sudo systemctl "$@"
  fi
}

pixel_broker_systemctl_read() {
  if pixel_broker_bytes_test_mode; then
    "${PIXEL_BROKER_BYTES_SYSTEMCTL_BIN:?PIXEL_BROKER_BYTES_SYSTEMCTL_BIN is required in test mode}" "$@"
  else
    systemctl "$@"
  fi
}

pixel_broker_require_installed_file() {
  local path=$1 mode=$2 info
  [[ -f "$path" && ! -L "$path" ]] || pixel_die "Installed broker byte is not a regular file: $path"
  info=$(stat -c '%h:%a:%U:%G' -- "$path")
  if pixel_broker_bytes_test_mode; then
    [[ "$info" == "1:${mode#0}:"* ]] || pixel_die "Installed broker byte has unsafe metadata: $path"
  else
    [[ "$info" == "1:${mode#0}:root:root" ]] || pixel_die "Installed broker byte must be root:root:${mode#0} with one link: $path"
  fi
}

pixel_broker_require_release_file() {
  local path=$1
  [[ -f "$path" && ! -L "$path" && $(stat -c %h -- "$path") == 1 ]] || pixel_die "Active release broker byte is missing, linked, or multiply linked: $path"
}

pixel_broker_require_safe_target() {
  local target=$1 parent info
  parent=$(dirname "$target")
  [[ -d "$parent" && ! -L "$parent" ]] || pixel_die "Broker-byte destination parent is missing or linked: $parent"
  info=$(stat -c '%a:%U:%G' -- "$parent")
  if ! pixel_broker_bytes_test_mode; then
    [[ "$info" == *':root:root' && $((8#${info%%:*} & 8#022)) == 0 ]] || pixel_die "Broker-byte destination parent must be root-owned and not group/world writable: $parent"
  fi
  if [[ -e "$target" || -L "$target" ]]; then
    [[ -f "$target" && ! -L "$target" && $(stat -c %h -- "$target") == 1 ]] || pixel_die "Broker-byte destination is linked or not a single regular file: $target"
  fi
}

pixel_broker_bytes_backup_all() {
  local backup_dir=$1
  local root="$backup_dir/brokers" limb install_dir limb_root relative mode release_relative hash state
  pixel_broker_bytes_require_trust
  [[ ! -e "$root" && ! -L "$root" ]] || pixel_die "Broker-byte backup already exists: $root"
  install -d -m 700 "$root"
  for limb in $(pixel_broker_limbs); do
    install_dir=$(pixel_broker_install_dir "$limb")
    [[ -d "$install_dir" && ! -L "$install_dir" ]] || pixel_die "Broker install directory is missing or linked: $install_dir"
    limb_root="$root/$limb"
    install -d -m 700 "$limb_root"
    : > "$limb_root/manifest.tsv"
    chmod 600 "$limb_root/manifest.tsv"
    while IFS='|' read -r relative mode release_relative; do
      if [[ -e "$install_dir/$relative" || -L "$install_dir/$relative" ]]; then
        pixel_broker_require_installed_file "$install_dir/$relative" "$mode"
        install -d -m 700 "$(dirname "$limb_root/$relative")"
        install -m 600 "$install_dir/$relative" "$limb_root/$relative"
        hash=$(sha256sum "$limb_root/$relative" | awk '{print $1}')
        state=present
      else
        hash=-
        state=absent
      fi
      printf '%s|%s|%s|%s\n' "$relative" "$state" "$hash" "$mode" >> "$limb_root/manifest.tsv"
    done < <(pixel_broker_byte_files "$limb")
  done
}

pixel_broker_bytes_quiesce() {
  local limb=$1 unit
  unit=$(pixel_broker_unit "$limb")
  if [[ "$limb" == source ]]; then
    pixel_broker_systemctl stop "${PIXEL_SOURCE_BROKER_TIMER:-pixel-source-broker.timer}"
    if [[ ${PIXEL_CALENDAR_DIRECT_ENABLED:-0} == 1 ]]; then
      pixel_broker_systemctl stop "${PIXEL_SOURCE_DIRECT_PATH_UNIT:-pixel-source-direct.path}"
    fi
    pixel_broker_systemctl stop "$unit"
  else
    pixel_broker_systemctl stop "$unit"
  fi
}

pixel_broker_restart() {
  local limb=$1 unit result
  unit=$(pixel_broker_unit "$limb")
  if [[ "$limb" == source ]]; then
    pixel_broker_systemctl start "$unit"
    result=$(pixel_broker_systemctl_read show "$unit" -p Result --value)
    [[ "$result" == success ]] || pixel_die "Source Broker refresh failed after the byte update"
    pixel_broker_systemctl start "${PIXEL_SOURCE_BROKER_TIMER:-pixel-source-broker.timer}"
    pixel_broker_systemctl_read is-active --quiet "${PIXEL_SOURCE_BROKER_TIMER:-pixel-source-broker.timer}" || pixel_die "Source Broker timer is not active after the byte update"
    if [[ ${PIXEL_CALENDAR_DIRECT_ENABLED:-0} == 1 ]]; then
      pixel_broker_systemctl start "${PIXEL_SOURCE_DIRECT_PATH_UNIT:-pixel-source-direct.path}"
      pixel_broker_systemctl_read is-active --quiet "${PIXEL_SOURCE_DIRECT_PATH_UNIT:-pixel-source-direct.path}" || pixel_die "Source Broker direct watcher is not active after the byte update"
    fi
  else
    pixel_broker_systemctl restart "$unit"
    pixel_broker_systemctl_read is-active --quiet "$unit" || pixel_die "Broker service $unit did not restart after the byte update"
  fi
}

pixel_broker_bytes_install_all() {
  local active_release=$1 backup_dir=$2 limb install_dir relative mode release_relative source
  pixel_broker_bytes_require_trust
  [[ -d "$backup_dir/brokers" && ! -L "$backup_dir/brokers" ]] || pixel_die "Broker-byte backup is missing"
  install -m 600 /dev/null "$backup_dir/brokers/.mutation-started"
  for limb in $(pixel_broker_limbs); do pixel_broker_bytes_quiesce "$limb"; done
  for limb in $(pixel_broker_limbs); do
    install_dir=$(pixel_broker_install_dir "$limb")
    while IFS='|' read -r relative mode release_relative; do
      source="$active_release/$release_relative"
      pixel_broker_require_release_file "$source"
      pixel_broker_require_safe_target "$install_dir/$relative"
      pixel_broker_install_byte "$source" "$install_dir/$relative" "$mode"
    done < <(pixel_broker_byte_files "$limb")
  done
  for limb in $(pixel_broker_limbs); do pixel_broker_restart "$limb"; done
}

pixel_broker_bytes_restore_all() {
  local backup_dir=$1
  local root="$backup_dir/brokers" limb install_dir limb_root relative mode release_relative record_relative state hash record_mode observed
  [[ -f "$root/.mutation-started" && ! -L "$root/.mutation-started" ]] || return 0
  pixel_broker_bytes_require_trust
  for limb in source ops frontier; do
    limb_root="$root/$limb"
    [[ -f "$limb_root/manifest.tsv" && ! -L "$limb_root/manifest.tsv" ]] || continue
    install_dir=$(pixel_broker_install_dir "$limb") || pixel_die "Broker install directory is unavailable during restore: $limb"
    pixel_broker_bytes_quiesce "$limb"
    exec 3< "$limb_root/manifest.tsv"
    while IFS='|' read -r relative mode release_relative; do
      IFS='|' read -r record_relative state hash record_mode <&3 || pixel_die "Broker-byte backup manifest is truncated: $limb"
      [[ "$record_relative" == "$relative" && "$record_mode" == "$mode" ]] || pixel_die "Broker-byte backup manifest does not match the fixed file contract: $limb"
      case "$state" in
        present)
          pixel_broker_require_release_file "$limb_root/$relative"
          observed=$(sha256sum "$limb_root/$relative" | awk '{print $1}')
          [[ "$observed" == "$hash" ]] || pixel_die "Broker-byte backup hash mismatch: $limb/$relative"
          pixel_broker_require_safe_target "$install_dir/$relative"
          pixel_broker_install_byte "$limb_root/$relative" "$install_dir/$relative" "$mode"
          ;;
        absent)
          [[ "$hash" == - && ! -e "$limb_root/$relative" && ! -L "$limb_root/$relative" ]] || pixel_die "Invalid absent broker-byte backup record: $limb/$relative"
          pixel_broker_remove_byte "$install_dir/$relative"
          ;;
        *) pixel_die "Invalid broker-byte backup state: $limb/$relative" ;;
      esac
    done < <(pixel_broker_byte_files "$limb")
    if IFS= read -r observed <&3; then pixel_die "Broker-byte backup manifest has unexpected records: $limb"; fi
    exec 3<&-
    pixel_broker_restart "$limb"
  done
}

pixel_broker_bytes_verify() {
  local active_release=$1 limb install_dir relative mode release_relative installed release
  pixel_broker_validate_configuration
  for limb in $(pixel_broker_limbs); do
    install_dir=$(pixel_broker_install_dir "$limb")
    while IFS='|' read -r relative mode release_relative; do
      pixel_broker_require_installed_file "$install_dir/$relative" "$mode"
      pixel_broker_require_release_file "$active_release/$release_relative"
      installed=$(sha256sum "$install_dir/$relative" | awk '{print $1}')
      release=$(sha256sum "$active_release/$release_relative" | awk '{print $1}')
      [[ "$installed" == "$release" ]] || pixel_die "Installed $limb broker bytes do not match the active release: $relative"
    done < <(pixel_broker_byte_files "$limb")
  done
}
