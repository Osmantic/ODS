#!/bin/bash
# Secure pre-update snapshot and restore contract for ods-update.sh.
#
# This file is sourced after ods-update.sh has defined INSTALL_DIR,
# ROLLBACK_DIR, VERSION_FILE, logging helpers, get_current_version(), and
# _prune_rollback_snapshots().  It intentionally does not snapshot
# data/assistant-first/secrets: update and rollback preserve that host-owned
# store in place while desired state contains references only.

ODS_UPDATE_SNAPSHOT_SCHEMA="ods.pre-update-snapshot.v2"

# Bash 3.2 compatible parallel arrays.  Keep these paths static: restore uses
# the same allowlist before every bounded removal.
ODS_UPDATE_SNAPSHOT_PATHS=(
    ".env"
    ".compose-flags"
    ".version"
    "config"
    "data/assistant-first/desired-state"
    "data/assistant-first/transaction-store"
    "data/user-extensions"
)
ODS_UPDATE_SNAPSHOT_KINDS=(
    "file"
    "file"
    "file"
    "directory"
    "directory"
    "directory"
    "directory"
)
ODS_UPDATE_SNAPSHOT_TRANSIENT_EXCLUSIONS=(
    "data/user-extensions/.tmp"
)
_snapshot_sha256() {
    local path="$1"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$path" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$path" | awk '{print $1}'
    else
        log_error "sha256sum or shasum is required for rollback snapshots."
        return 1
    fi
}

_snapshot_mode() {
    local path="$1"
    if stat -c '%a' "$path" >/dev/null 2>&1; then
        stat -c '%a' "$path"
    else
        stat -f '%Lp' "$path"
    fi
}

_snapshot_relative_path_valid() {
    local path="$1"
    [[ -n "$path" && "$path" != /* && "$path" != "." ]] || return 1
    case "/$path/" in
        */../*|*/./*) return 1 ;;
    esac
    [[ "$path" != *$'\n'* && "$path" != *$'\r'* && "$path" != *$'\t'* ]] || return 1
    return 0
}

_snapshot_payload_path_allowed() {
    local path="$1"
    _snapshot_relative_path_valid "$path" || return 1
    case "$path" in
        data/user-extensions/.tmp|data/user-extensions/.tmp/*) return 1 ;;
        .env|.compose-flags|.version) return 0 ;;
        config/*) return 0 ;;
        data/assistant-first/desired-state/*) return 0 ;;
        data/assistant-first/transaction-store/*) return 0 ;;
        data/user-extensions/*) return 0 ;;
    esac
    if [[ "$path" != */* ]]; then
        case "$path" in
            .env.*|docker-compose*.yml|docker-compose*.yaml) return 0 ;;
        esac
    fi
    return 1
}

_snapshot_payload_directory_allowed() {
    local path="$1"
    case "$path" in
        data/user-extensions/.tmp|data/user-extensions/.tmp/*) return 1 ;;
        config|config/*) return 0 ;;
        data|data/assistant-first) return 0 ;;
        data/assistant-first/desired-state|data/assistant-first/desired-state/*) return 0 ;;
        data/assistant-first/transaction-store|data/assistant-first/transaction-store/*) return 0 ;;
        data/user-extensions|data/user-extensions/*) return 0 ;;
    esac
    return 1
}

_snapshot_tree_safe() {
    local root="$1" label="$2" entry relative
    if [[ -L "$root" ]]; then
        log_error "Snapshot source is a symlink: ${label}"
        return 1
    fi
    if [[ -f "$root" ]]; then
        return 0
    fi
    if [[ ! -d "$root" ]]; then
        log_error "Snapshot source is not a regular file or directory: ${label}"
        return 1
    fi
    while IFS= read -r -d '' entry; do
        relative="${entry#"$root"/}"
        [[ "$entry" == "$root" ]] && relative="."
        if [[ -L "$entry" ]]; then
            log_error "Snapshot source contains a symlink: ${label}/${relative}"
            return 1
        fi
        if [[ ! -f "$entry" && ! -d "$entry" ]]; then
            log_error "Snapshot source contains a special file: ${label}/${relative}"
            return 1
        fi
        [[ "$relative" == "." ]] || _snapshot_relative_path_valid "$relative" || {
            log_error "Snapshot source contains an unsafe path: ${label}/${relative}"
            return 1
        }
    done < <(find "$root" -print0)
    return 0
}

_snapshot_copy_user_extensions() (
    local source="$1" destination="$2" relative="$3"
    local entry base mode

    if [[ -L "$source" || ! -d "$source" ]]; then
        log_error "Snapshot path has the wrong type; expected directory: ${relative}"
        return 1
    fi
    mkdir -p "$destination"
    mode="$(_snapshot_mode "$source")" || return 1
    chmod "$mode" "$destination"

    shopt -s dotglob nullglob
    for entry in "$source"/*; do
        base="$(basename "$entry")"
        # .tmp contains only in-flight staging/swap material.  It is neither a
        # committed extension definition nor a rollback point and must never
        # become authoritative after an update rollback.  .backups is durable
        # definition rollback state and is copied like every other entry.
        [[ "$base" == ".tmp" ]] && continue
        _snapshot_tree_safe "$entry" "${relative}/${base}" || return 1
        cp -pR "$entry" "$destination/"
    done
)

_snapshot_copy_tracked_path() {
    local snap_dir="$1" relative="$2" kind="$3" records="$4"
    local source="${INSTALL_DIR}/${relative}" destination="${snap_dir}/payload/${relative}"
    local present=false

    if [[ -e "$source" || -L "$source" ]]; then
        if [[ "$kind" == "file" && ! -f "$source" ]]; then
            log_error "Snapshot path has the wrong type; expected file: ${relative}"
            return 1
        fi
        if [[ "$kind" == "directory" && ! -d "$source" ]]; then
            log_error "Snapshot path has the wrong type; expected directory: ${relative}"
            return 1
        fi
        mkdir -p "$(dirname "$destination")"
        if [[ "$relative" == "data/user-extensions" ]]; then
            _snapshot_copy_user_extensions "$source" "$destination" "$relative" || return 1
        elif [[ "$kind" == "directory" ]]; then
            _snapshot_tree_safe "$source" "$relative" || return 1
            cp -pR "$source" "$destination"
        else
            _snapshot_tree_safe "$source" "$relative" || return 1
            cp -p "$source" "$destination"
        fi
        present=true
    fi

    jq -cn \
        --arg path "$relative" \
        --arg kind "$kind" \
        --argjson present "$present" \
        '{path:$path, kind:$kind, present:$present}' >> "$records"
}

_snapshot_copy_family_files() {
    local snap_dir="$1" source relative destination
    shopt -s nullglob
    for source in \
        "${INSTALL_DIR}"/.env.* \
        "${INSTALL_DIR}"/docker-compose*.yml \
        "${INSTALL_DIR}"/docker-compose*.yaml; do
        relative="${source#"${INSTALL_DIR}/"}"
        _snapshot_relative_path_valid "$relative" || {
            log_error "Refusing unsafe rollback snapshot path: ${relative}"
            shopt -u nullglob
            return 1
        }
        if [[ -L "$source" || ! -f "$source" ]]; then
            log_error "Rollback snapshot family member is not a regular file: ${relative}"
            shopt -u nullglob
            return 1
        fi
        destination="${snap_dir}/payload/${relative}"
        cp -p "$source" "$destination"
    done
    shopt -u nullglob
}

_snapshot_write_file_records() {
    local snap_dir="$1" records="$2" file relative digest
    while IFS= read -r -d '' file; do
        relative="${file#"${snap_dir}/payload/"}"
        _snapshot_payload_path_allowed "$relative" || {
            log_error "Snapshot payload contains an untracked file: ${relative}"
            return 1
        }
        digest="$(_snapshot_sha256 "$file")" || return 1
        [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || {
            log_error "Could not hash rollback snapshot file: ${relative}"
            return 1
        }
        jq -cn --arg path "$relative" --arg sha256 "$digest" \
            '{path:$path, sha256:$sha256}' >> "$records"
    done < <(find "${snap_dir}/payload" -type f -print0)
}

_snapshot_validate_payload_copy() {
    local payload="$1" metadata="$2" entry relative file expected actual count
    _snapshot_tree_safe "$payload" "payload" || return 1
    while IFS= read -r -d '' entry; do
        [[ "$entry" == "$payload" ]] && continue
        if [[ -d "$entry" ]]; then
            relative="${entry#"${payload}/"}"
            _snapshot_payload_directory_allowed "$relative" || {
                log_error "Rollback snapshot payload contains an untracked directory: ${relative}"
                return 1
            }
        fi
    done < <(find "$payload" -type d -print0)

    while IFS=$'\t' read -r relative expected; do
        expected="${expected%$'\r'}"
        _snapshot_payload_path_allowed "$relative" || {
            log_error "Rollback snapshot metadata names an unsafe file: ${relative}"
            return 1
        }
        file="${payload}/${relative}"
        [[ -f "$file" && ! -L "$file" ]] || {
            log_error "Rollback snapshot payload file is missing: ${relative}"
            return 1
        }
        actual="$(_snapshot_sha256 "$file")" || return 1
        [[ "$actual" == "$expected" ]] || {
            log_error "Rollback snapshot checksum mismatch: ${relative}"
            return 1
        }
    done < <(jq -r '.files[] | [.path, .sha256] | @tsv' "$metadata")

    count="$(find "$payload" -type f | wc -l | tr -d ' ')"
    [[ "$count" -eq "$(jq '.files_count' "$metadata")" ]] || {
        log_error "Rollback snapshot contains unlisted or missing payload files."
        return 1
    }
    while IFS= read -r -d '' file; do
        relative="${file#"${payload}/"}"
        jq -e --arg path "$relative" '.files | any(.path == $path)' "$metadata" >/dev/null || {
            log_error "Rollback snapshot contains an unlisted payload file: ${relative}"
            return 1
        }
    done < <(find "$payload" -type f -print0)
    return 0
}

_snapshot_validate_v2() {
    local snap_dir="$1"
    local metadata="${snap_dir}/snapshot.json" payload="${snap_dir}/payload"
    local index relative kind present count entry mode base

    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*) ;;
        *)
            mode="$(_snapshot_mode "$snap_dir")" || return 1
            (( (8#$mode & 077) == 0 )) || {
                log_error "Rollback snapshot directory is not owner-private."
                return 1
            }
            mode="$(_snapshot_mode "$payload")" || return 1
            (( (8#$mode & 077) == 0 )) || {
                log_error "Rollback snapshot payload is not owner-private."
                return 1
            }
            mode="$(_snapshot_mode "$metadata")" || return 1
            (( (8#$mode & 077) == 0 )) || {
                log_error "Rollback snapshot metadata is not owner-private."
                return 1
            }
            ;;
    esac

    jq -e \
        --arg schema "$ODS_UPDATE_SNAPSHOT_SCHEMA" \
        --arg install_dir "$INSTALL_DIR" \
        --arg transient_exclusion "${ODS_UPDATE_SNAPSHOT_TRANSIENT_EXCLUSIONS[0]}" \
        '.schema == $schema and .type == "pre-update" and
         (.timestamp | type == "string") and (.version | type == "string") and
         .install_dir == $install_dir and
         (.paths | type == "array") and (.families | type == "array") and
         .transient_exclusions == [$transient_exclusion] and
         (.files | type == "array") and
         ((.files_count | type) == "number") and .files_count >= 0 and
         ((.files_count | floor) == .files_count) and
         .files_count == (.files | length) and
         ([.files[].path] | length) == ([.files[].path] | unique | length) and
         (all(.files[];
              ((.path | type) == "string") and
              ((.sha256 | type) == "string") and
              (try (.sha256 | test("^[0-9a-f]{64}$")) catch false))) and
         .secret_custody == {
             path: "data/assistant-first/secrets",
             disposition: "preserved-in-place"
         }' \
        "$metadata" >/dev/null || {
        log_error "Rollback snapshot metadata violates the v2 contract."
        return 1
    }

    [[ "$(jq '.paths | length' "$metadata")" -eq "${#ODS_UPDATE_SNAPSHOT_PATHS[@]}" ]] || {
        log_error "Rollback snapshot tracked-path set is incomplete."
        return 1
    }
    for ((index = 0; index < ${#ODS_UPDATE_SNAPSHOT_PATHS[@]}; index++)); do
        relative="${ODS_UPDATE_SNAPSHOT_PATHS[$index]}"
        kind="${ODS_UPDATE_SNAPSHOT_KINDS[$index]}"
        count="$(jq --arg path "$relative" --arg kind "$kind" \
            '[.paths[] | select(.path == $path and .kind == $kind and (.present | type) == "boolean")] | length' \
            "$metadata")"
        [[ "$count" -eq 1 ]] || {
            log_error "Rollback snapshot path declaration is invalid: ${relative}"
            return 1
        }
        present="$(jq -r --arg path "$relative" '.paths[] | select(.path == $path) | .present' "$metadata" | tr -d '\r')"
        if [[ "$present" == "true" ]]; then
            if [[ "$kind" == "file" ]]; then
                [[ -f "${payload}/${relative}" && ! -L "${payload}/${relative}" ]] || {
                    log_error "Rollback snapshot is missing declared file: ${relative}"
                    return 1
                }
            else
                [[ -d "${payload}/${relative}" && ! -L "${payload}/${relative}" ]] || {
                    log_error "Rollback snapshot is missing declared directory: ${relative}"
                    return 1
                }
            fi
        elif [[ -e "${payload}/${relative}" || -L "${payload}/${relative}" ]]; then
            log_error "Rollback snapshot contains a path declared absent: ${relative}"
            return 1
        fi
    done

    jq -e '
        (.families | sort) == ([".env.*", "docker-compose*.yaml", "docker-compose*.yml"] | sort)
    ' "$metadata" >/dev/null || {
        log_error "Rollback snapshot file-family contract is invalid."
        return 1
    }

    shopt -s dotglob nullglob
    for entry in "$snap_dir"/*; do
        base="$(basename "$entry")"
        case "$base" in
            payload|snapshot.json) ;;
            *)
                shopt -u dotglob nullglob
                log_error "Rollback snapshot contains an untracked top-level entry: ${base}"
                return 1
                ;;
        esac
    done
    shopt -u dotglob nullglob

    _snapshot_validate_payload_copy "$payload" "$metadata"
}

_snapshot_validate_legacy() {
    local snap_dir="$1" entry
    jq -e '.type == "pre-update"' "${snap_dir}/snapshot.json" >/dev/null || {
        log_error "Legacy rollback snapshot metadata is invalid."
        return 1
    }
    while IFS= read -r -d '' entry; do
        if [[ -L "$entry" || ( ! -f "$entry" && ! -d "$entry" ) ]]; then
            log_error "Legacy rollback snapshot contains an unsafe entry."
            return 1
        fi
    done < <(find "$snap_dir" -print0)
    log_warn "Using a legacy rollback snapshot without payload checksums."
    return 0
}

_validate_snapshot() {
    local snap_dir="$1" schema
    if [[ ! -d "$snap_dir" || -L "$snap_dir" ]]; then
        log_error "Rollback snapshot is missing or unsafe: ${snap_dir}"
        return 1
    fi
    if [[ ! -f "${snap_dir}/snapshot.json" || -L "${snap_dir}/snapshot.json" ]]; then
        log_error "Snapshot is missing a regular snapshot.json: ${snap_dir}"
        return 1
    fi
    jq empty "${snap_dir}/snapshot.json" >/dev/null 2>&1 || {
        log_error "snapshot.json is not valid JSON: ${snap_dir}"
        return 1
    }
    schema="$(jq -r '.schema // "legacy"' "${snap_dir}/snapshot.json" | tr -d '\r')"
    case "$schema" in
        "$ODS_UPDATE_SNAPSHOT_SCHEMA") _snapshot_validate_v2 "$snap_dir" ;;
        legacy) _snapshot_validate_legacy "$snap_dir" ;;
        *)
            log_error "Unsupported rollback snapshot schema: ${schema}"
            return 1
            ;;
    esac
}

snapshot_pre_update() (
    local timestamp="${1:-$(date +%Y%m%d-%H%M%S)}"
    local created_snapshot roots_file files_file index file_count complete=0

    if [[ ! "$timestamp" =~ ^[0-9]{8}-[0-9]{6}$ ]]; then
        log_error "Invalid timestamp format '${timestamp}'; expected YYYYMMDD-HHMMSS." >&2
        return 1
    fi
    _snapshot_sha256 "$VERSION_FILE" >/dev/null 2>&1 || {
        # VERSION_FILE may be absent, so test the command availability directly.
        command -v sha256sum >/dev/null 2>&1 || command -v shasum >/dev/null 2>&1 || {
            log_error "sha256sum or shasum is required for rollback snapshots." >&2
            return 1
        }
    }
    if [[ -L "$ROLLBACK_DIR" || ( -e "$ROLLBACK_DIR" && ! -d "$ROLLBACK_DIR" ) ]]; then
        log_error "Rollback directory is not a safe directory: ${ROLLBACK_DIR}" >&2
        return 1
    fi

    umask 077
    mkdir -p "$ROLLBACK_DIR"
    chmod 700 "$ROLLBACK_DIR"
    created_snapshot="${ROLLBACK_DIR}/pre-update-${timestamp}"
    if [[ -e "$created_snapshot" || -L "$created_snapshot" ]]; then
        log_error "Rollback snapshot already exists: ${created_snapshot}" >&2
        return 1
    fi
    mkdir "$created_snapshot"
    mkdir "${created_snapshot}/payload"
    chmod 700 "$created_snapshot" "${created_snapshot}/payload"
    roots_file="${created_snapshot}/.paths.jsonl"
    files_file="${created_snapshot}/.files.jsonl"
    : > "$roots_file"
    : > "$files_file"
    trap 'if [[ "$complete" -ne 1 ]]; then rm -rf -- "$created_snapshot"; fi' EXIT INT TERM

    log_info "Creating rollback snapshot: pre-update-${timestamp}" >&2
    for ((index = 0; index < ${#ODS_UPDATE_SNAPSHOT_PATHS[@]}; index++)); do
        _snapshot_copy_tracked_path \
            "$created_snapshot" \
            "${ODS_UPDATE_SNAPSHOT_PATHS[$index]}" \
            "${ODS_UPDATE_SNAPSHOT_KINDS[$index]}" \
            "$roots_file" || return 1
    done
    _snapshot_copy_family_files "$created_snapshot" || return 1
    _snapshot_write_file_records "$created_snapshot" "$files_file" || return 1
    file_count="$(wc -l < "$files_file" | tr -d ' ')"

    jq -n \
        --arg schema "$ODS_UPDATE_SNAPSHOT_SCHEMA" \
        --arg timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg version "$(get_current_version)" \
        --arg install_dir "$INSTALL_DIR" \
        --arg transient_exclusion "${ODS_UPDATE_SNAPSHOT_TRANSIENT_EXCLUSIONS[0]}" \
        --argjson files_count "$file_count" \
        --slurpfile paths "$roots_file" \
        --slurpfile files "$files_file" \
        '{
          schema: $schema,
          type: "pre-update",
          timestamp: $timestamp,
          version: $version,
          install_dir: $install_dir,
          paths: ($paths | sort_by(.path)),
          families: [".env.*", "docker-compose*.yml", "docker-compose*.yaml"],
          transient_exclusions: [$transient_exclusion],
          files: ($files | sort_by(.path)),
          files_count: $files_count,
          secret_custody: {path: "data/assistant-first/secrets", disposition: "preserved-in-place"}
        }' > "${created_snapshot}/snapshot.json"
    chmod 600 "${created_snapshot}/snapshot.json"
    rm -f "$roots_file" "$files_file"

    _validate_snapshot "$created_snapshot" || return 1
    complete=1
    trap - EXIT INT TERM
    log_ok "Rollback snapshot ready (${file_count} files): ${created_snapshot}" >&2
    _prune_rollback_snapshots
    printf '%s\n' "$created_snapshot"
)

_snapshot_validate_live_parent() {
    local relative="$1" parent current component remaining
    _snapshot_relative_path_valid "$relative" || return 1
    [[ -d "$INSTALL_DIR" && ! -L "$INSTALL_DIR" ]] || {
        log_error "Install directory is not a safe directory."
        return 1
    }
    [[ "$relative" == */* ]] || return 0
    parent="${relative%/*}"
    current="$INSTALL_DIR"
    remaining="$parent"
    while [[ -n "$remaining" ]]; do
        component="${remaining%%/*}"
        if [[ "$remaining" == */* ]]; then
            remaining="${remaining#*/}"
        else
            remaining=""
        fi
        current="${current}/${component}"
        if [[ -L "$current" ]]; then
            log_error "Restore target has a symlinked parent: ${relative}"
            return 1
        fi
        if [[ -e "$current" && ! -d "$current" ]]; then
            log_error "Restore target parent is not a directory: ${relative}"
            return 1
        fi
    done
    return 0
}

_snapshot_remove_live_path() {
    local relative="$1" allowed=false index
    for ((index = 0; index < ${#ODS_UPDATE_SNAPSHOT_PATHS[@]}; index++)); do
        [[ "$relative" == "${ODS_UPDATE_SNAPSHOT_PATHS[$index]}" ]] && allowed=true
    done
    if [[ "$relative" != */* ]]; then
        case "$relative" in
            .env.*|docker-compose*.yml|docker-compose*.yaml) allowed=true ;;
        esac
    fi
    [[ "$allowed" == "true" ]] || {
        log_error "Refusing to remove an untracked restore path: ${relative}"
        return 1
    }
    _snapshot_validate_live_parent "$relative" || return 1
    rm -rf -- "${INSTALL_DIR:?}/${relative}"
}

_restore_snapshot_v2() {
    local snap_dir="$1"
    local metadata="${snap_dir}/snapshot.json" payload="${snap_dir}/payload"
    local stage="${INSTALL_DIR}/.ods-snapshot-restore.$$" index relative present source

    _snapshot_validate_v2 "$snap_dir" || return 1
    for ((index = 0; index < ${#ODS_UPDATE_SNAPSHOT_PATHS[@]}; index++)); do
        _snapshot_validate_live_parent "${ODS_UPDATE_SNAPSHOT_PATHS[$index]}" || return 1
    done
    shopt -s nullglob
    for source in \
        "${INSTALL_DIR}"/.env.* \
        "${INSTALL_DIR}"/docker-compose*.yml \
        "${INSTALL_DIR}"/docker-compose*.yaml; do
        relative="${source#"${INSTALL_DIR}/"}"
        _snapshot_validate_live_parent "$relative" || {
            shopt -u nullglob
            return 1
        }
    done
    shopt -u nullglob

    if [[ -e "$stage" || -L "$stage" ]]; then
        log_error "Restore staging path already exists."
        return 1
    fi
    umask 077
    mkdir "$stage"
    if ! cp -pR "${payload}/." "$stage/"; then
        rm -rf -- "$stage"
        log_error "Could not stage rollback snapshot before restore."
        return 1
    fi
    if ! _snapshot_validate_payload_copy "$stage" "$metadata"; then
        rm -rf -- "$stage"
        log_error "Staged rollback payload failed integrity verification."
        return 1
    fi

    # Remove the exact families first so files introduced by the failed update
    # do not survive rollback.
    shopt -s nullglob
    for source in \
        "${INSTALL_DIR}"/.env.* \
        "${INSTALL_DIR}"/docker-compose*.yml \
        "${INSTALL_DIR}"/docker-compose*.yaml; do
        relative="${source#"${INSTALL_DIR}/"}"
        _snapshot_remove_live_path "$relative" || {
            shopt -u nullglob
            rm -rf -- "$stage"
            return 1
        }
    done
    shopt -u nullglob

    for ((index = 0; index < ${#ODS_UPDATE_SNAPSHOT_PATHS[@]}; index++)); do
        relative="${ODS_UPDATE_SNAPSHOT_PATHS[$index]}"
        present="$(jq -r --arg path "$relative" '.paths[] | select(.path == $path) | .present' "$metadata" | tr -d '\r')"
        _snapshot_remove_live_path "$relative" || {
            rm -rf -- "$stage"
            return 1
        }
        if [[ "$present" == "true" ]]; then
            mkdir -p "$(dirname "${INSTALL_DIR}/${relative}")"
            if ! mv "${stage}/${relative}" "${INSTALL_DIR}/${relative}"; then
                rm -rf -- "$stage"
                log_error "Could not restore rollback snapshot path: ${relative}"
                return 1
            fi
            log_info "  Restored: ${relative}"
        else
            log_info "  Restored absence: ${relative}"
        fi
    done

    shopt -s nullglob
    for source in "$stage"/.env.* "$stage"/docker-compose*.yml "$stage"/docker-compose*.yaml; do
        relative="${source#"${stage}/"}"
        if ! mv "$source" "${INSTALL_DIR}/${relative}"; then
            shopt -u nullglob
            rm -rf -- "$stage"
            log_error "Could not restore rollback snapshot file: ${relative}"
            return 1
        fi
        log_info "  Restored: ${relative}"
    done
    shopt -u nullglob
    rm -rf -- "$stage"
    log_ok "Snapshot restored."
}

_restore_snapshot_legacy() {
    local snap_dir="$1" file base ext_dir source
    _snapshot_validate_legacy "$snap_dir" || return 1
    log_info "Restoring legacy rollback snapshot: $(basename "$snap_dir")"
    shopt -s dotglob
    for file in "$snap_dir"/*; do
        base="$(basename "$file")"
        [[ -f "$file" && "$base" != "snapshot.json" && "$base" != "metadata.json" ]] || continue
        cp -p "$file" "${INSTALL_DIR}/"
        log_info "  Restored: ${base}"
    done
    shopt -u dotglob
    for ext_dir in litellm n8n openclaw searxng; do
        source="${snap_dir}/config-${ext_dir}"
        if [[ -d "$source" ]]; then
            rm -rf -- "${INSTALL_DIR}/config/${ext_dir}"
            mkdir -p "${INSTALL_DIR}/config"
            cp -pR "$source" "${INSTALL_DIR}/config/${ext_dir}"
            log_info "  Restored: config/${ext_dir}/"
        fi
    done
    log_ok "Legacy snapshot restored."
}

_restore_snapshot() {
    local snap_dir="$1" schema
    _validate_snapshot "$snap_dir" || return 1
    schema="$(jq -r '.schema // "legacy"' "${snap_dir}/snapshot.json" | tr -d '\r')"
    if [[ "$schema" == "$ODS_UPDATE_SNAPSHOT_SCHEMA" ]]; then
        _restore_snapshot_v2 "$snap_dir"
    else
        _restore_snapshot_legacy "$snap_dir"
    fi
}
