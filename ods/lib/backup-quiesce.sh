#!/usr/bin/env bash
# Purpose: Quiesce and recover the running ODS Compose project around backups.
# Expects: ODS_DIR, docker, jq.
# Provides: ods_backup_quiesce_begin, ods_backup_quiesce_resume,
#           ods_backup_quiesce_recover_dead.

ODS_BACKUP_QUIESCE_STATE="${ODS_DIR}/data/backup-quiesce.json"
ODS_BACKUP_QUIESCE_LOCK="${ODS_DIR}/data/.backup-quiesce.lock"
ODS_BACKUP_QUIESCE_ACTIVE="false"
ODS_BACKUP_QUIESCE_COUNT=0

_ods_backup_quiesce_log() {
    printf '[backup] %s\n' "$*" >&2
}

_ods_backup_quiesce_owner() {
    [[ -f "$ODS_BACKUP_QUIESCE_LOCK/owner" ]] || return 1
    local owner
    owner=$(<"$ODS_BACKUP_QUIESCE_LOCK/owner")
    [[ "$owner" =~ ^[1-9][0-9]*$ ]] || return 1
    printf '%s\n' "$owner"
}

_ods_backup_quiesce_release_lock() {
    rm -f "$ODS_BACKUP_QUIESCE_LOCK/owner"
    rmdir "$ODS_BACKUP_QUIESCE_LOCK"
}

_ods_backup_quiesce_acquire() {
    mkdir -p "${ODS_DIR}/data"

    if mkdir "$ODS_BACKUP_QUIESCE_LOCK" 2>/dev/null; then
        chmod 700 "$ODS_BACKUP_QUIESCE_LOCK"
        printf '%s\n' "$$" > "$ODS_BACKUP_QUIESCE_LOCK/owner"
        return 0
    fi

    local owner
    if ! owner=$(_ods_backup_quiesce_owner); then
        _ods_backup_quiesce_log "Invalid backup lock; refusing automatic recovery: $ODS_BACKUP_QUIESCE_LOCK"
        return 1
    fi
    if kill -0 "$owner" 2>/dev/null; then
        _ods_backup_quiesce_log "Backup transaction is already owned by live process $owner"
        return 1
    fi

    local stale="${ODS_BACKUP_QUIESCE_LOCK}.stale.$$"
    if ! mv "$ODS_BACKUP_QUIESCE_LOCK" "$stale" 2>/dev/null; then
        _ods_backup_quiesce_log "Another process is recovering the interrupted backup"
        return 1
    fi
    rm -f "$stale/owner"
    rmdir "$stale"

    mkdir "$ODS_BACKUP_QUIESCE_LOCK"
    chmod 700 "$ODS_BACKUP_QUIESCE_LOCK"
    printf '%s\n' "$$" > "$ODS_BACKUP_QUIESCE_LOCK/owner"
}

_ods_backup_quiesce_write_state() {
    local state="$1"
    shift
    local tmp="${ODS_BACKUP_QUIESCE_STATE}.tmp.$$"
    printf '%s\n' "$@" | jq -Rsc \
        --arg state "$state" \
        --argjson owner_pid "$$" \
        '{version: 1, state: $state, owner_pid: $owner_pid,
          container_ids: (split("\n") | map(select(length > 0)))}' > "$tmp"
    chmod 600 "$tmp"
    mv "$tmp" "$ODS_BACKUP_QUIESCE_STATE"
}

_ods_backup_quiesce_read_ids() {
    jq -er '.version == 1 and (.owner_pid | type == "number") and
        (.container_ids | type == "array") and
        all(.container_ids[]; type == "string" and test("^[a-fA-F0-9]{12,64}$"))' \
        "$ODS_BACKUP_QUIESCE_STATE" >/dev/null
    jq -r '.container_ids[]' "$ODS_BACKUP_QUIESCE_STATE"
}

_ods_backup_quiesce_start_recorded() {
    local -a ids=()
    local output id running
    output=$(_ods_backup_quiesce_read_ids) || return 1
    if [[ -n "$output" ]]; then
        while IFS= read -r id; do
            ids+=("$id")
        done <<< "$output"
    fi
    if [[ ${#ids[@]} -gt 0 ]]; then
        _ods_backup_quiesce_log "Restarting ${#ids[@]} container(s) captured by the backup transaction"
        docker start "${ids[@]}" >/dev/null
        for id in "${ids[@]}"; do
            running=$(docker inspect --format '{{.State.Running}}' "$id")
            if [[ "$running" != "true" ]]; then
                _ods_backup_quiesce_log "Container did not remain running after restart: $id"
                return 1
            fi
        done
    fi
}

ods_backup_quiesce_recover_dead() {
    [[ -f "$ODS_BACKUP_QUIESCE_STATE" ]] || return 0

    local owner
    if ! owner=$(jq -er '.owner_pid | select(type == "number")' "$ODS_BACKUP_QUIESCE_STATE"); then
        _ods_backup_quiesce_log "Invalid recovery receipt; refusing to guess which containers to start"
        return 1
    fi
    if [[ "$owner" != "$$" ]] && kill -0 "$owner" 2>/dev/null; then
        _ods_backup_quiesce_log "Backup transaction is still active in process $owner"
        return 1
    fi

    _ods_backup_quiesce_acquire
    if ! _ods_backup_quiesce_start_recorded; then
        _ods_backup_quiesce_log "Container recovery failed; receipt retained for the next attempt"
        _ods_backup_quiesce_release_lock
        return 1
    fi
    rm -f "$ODS_BACKUP_QUIESCE_STATE"
    _ods_backup_quiesce_release_lock
    _ods_backup_quiesce_log "Recovered containers from the interrupted backup"
}

ods_backup_quiesce_begin() {
    ods_backup_quiesce_recover_dead
    _ods_backup_quiesce_acquire

    local -a ids=()
    local output id
    if ! output=$(docker ps \
        --filter 'label=com.docker.compose.project=ods' \
        --format '{{.ID}}'); then
        _ods_backup_quiesce_log "Could not enumerate the running ODS containers"
        _ods_backup_quiesce_release_lock
        return 1
    fi
    if [[ -n "$output" ]]; then
        while IFS= read -r id; do
            ids+=("$id")
        done <<< "$output"
    fi
    for id in "${ids[@]}"; do
        if [[ ! "$id" =~ ^[a-fA-F0-9]{12,64}$ ]]; then
            _ods_backup_quiesce_log "Docker returned an invalid container ID: $id"
            _ods_backup_quiesce_release_lock
            return 1
        fi
    done
    ODS_BACKUP_QUIESCE_COUNT=${#ids[@]}
    _ods_backup_quiesce_write_state "stopping" "${ids[@]}"
    ODS_BACKUP_QUIESCE_ACTIVE="true"

    if [[ ${#ids[@]} -gt 0 ]]; then
        _ods_backup_quiesce_log "Stopping ${#ids[@]} running ODS container(s) for a consistent snapshot"
        docker stop "${ids[@]}" >/dev/null
    fi
    _ods_backup_quiesce_write_state "stopped" "${ids[@]}"
}

ods_backup_quiesce_resume() {
    [[ "$ODS_BACKUP_QUIESCE_ACTIVE" == "true" ]] || return 0
    if ! _ods_backup_quiesce_start_recorded; then
        _ods_backup_quiesce_log "Container restart failed; receipt retained at $ODS_BACKUP_QUIESCE_STATE"
        return 1
    fi
    rm -f "$ODS_BACKUP_QUIESCE_STATE"
    _ods_backup_quiesce_release_lock
    ODS_BACKUP_QUIESCE_ACTIVE="false"
}
