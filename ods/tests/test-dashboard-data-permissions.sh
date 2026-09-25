#!/usr/bin/env bash
# Exercise phase 06's public permission boundary, including denied repairs.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
awk '/_phase06_step "prepare-(dashboard|service)-permissions"/{copy=1} /_phase06_step "generate-env"/{copy=0} copy' \
    "$ROOT_DIR/installers/phases/06-directories.sh" > "$work/phase.sh"

run_case() (
    local scenario="$1"
    export SCRIPT_DIR="$ROOT_DIR" INSTALL_DIR="$work/$scenario"
    mkdir -p "$INSTALL_DIR/data/token-spy" "$INSTALL_DIR/data/private"
    chmod 750 "$INSTALL_DIR/data"
    chmod 700 "$INSTALL_DIR/data/private"
    export QA_GROUP=2001 QA_SCENARIO="$scenario" QA_CALLS="$INSTALL_DIR/calls"
    : > "$QA_CALLS"
    _phase06_rootless=false
    [[ "$scenario" != rootless* ]] || _phase06_rootless=true
    if [[ "$scenario" == current ]]; then
        QA_GROUP=1000
        chmod 770 "$INSTALL_DIR/data"
    fi
    _phase06_step() { :; }
    warn() { echo "$*" >&2; }
    error() { echo "$*" >&2; return 1; }
    chown() { :; } # Existing unrelated Token Spy adjustment.
    ods_sudo() {
        printf '%s\n' "$*" >> "$QA_CALLS"
        case "$1" in
            chgrp)
                [[ "$QA_SCENARIO" != denied_group ]] || return 1
                [[ "$2" == 1000 && "$3" == "$INSTALL_DIR/data" && "$#" == 3 ]]
                [[ "$QA_SCENARIO" == bad_readback ]] || QA_GROUP=1000 ;;
            chmod)
                [[ "$QA_SCENARIO" != denied_mode ]] || return 1
                command chmod "$2" "$3" ;;
            chown) return 1 ;;
            *) return 91 ;;
        esac
    }
    stat() {
        if [[ "$1" == -c && "$2" == '%g:%a' ]]; then
            printf '%s:%s\n' "$QA_GROUP" "$(command stat -c '%a' "$3")"
        elif [[ "$1" == -c && "$2" == '%u:%g' && "$QA_SCENARIO" == chat_denied ]]; then
            printf '1001:2001\n'
        elif [[ "$1" == -c && "$2" == %g ]]; then
            printf '%s\n' "$QA_GROUP"
        else
            command stat "$@"
        fi
    }
    _ods_rootless_ensure_helper_image() { [[ "$QA_SCENARIO" != rootless_image_denied ]]; }
    ODS_ROOTLESS_HELPER_IMAGE=busybox:1.36.1
    docker_run() {
        printf '%s\n' "$*" >> "$QA_CALLS"
        [[ "$QA_SCENARIO" != rootless_denied ]] || return 1
        [[ "$*" == *'--network none'* && "$*" == *'--user 0:0'* ]] || return 1
        [[ "$*" == *"src=$INSTALL_DIR/data,dst=/data"* ]] || return 1
        [[ "$*" != *'chown -R '* ]] || return 1
        # Run the exact namespace command against this fixture's data root.
        local command_text="${!#}"
        command_text="${command_text//\/data/\"$INSTALL_DIR\/data\"}"
        chgrp() { [[ "$1" == 1000 ]]; QA_GROUP=1000; }
        eval "$command_text"
    }
    if [[ "$scenario" == symlink ]]; then
        mv "$INSTALL_DIR/data" "$INSTALL_DIR/elsewhere"
        ln -s elsewhere "$INSTALL_DIR/data"
    fi
    [[ "$scenario" != chat_symlink ]] || ln -s private "$INSTALL_DIR/data/pixel-chat-results"
    [[ "$scenario" != chat_denied ]] || mkdir "$INSTALL_DIR/data/pixel-chat-results"
    local result=0
    source "$work/phase.sh" || result=$?
    case "$scenario" in
        denied_group|denied_mode|symlink|chat_symlink|chat_denied|bad_readback|rootless_denied|rootless_image_denied)
            [[ "$result" != 0 ]] || { echo "FAIL: $scenario did not stop phase 06"; exit 1; }
            [[ "$scenario" != symlink || ! -s "$QA_CALLS" ]] ;;
        *)
            [[ "$result" == 0 && "$QA_GROUP" == 1000 ]] \
                || { echo "FAIL: $scenario leaves Dashboard data unwritable"; exit 1; }
            [[ $(command stat -c %a "$INSTALL_DIR/data") == 770 ]]
            [[ $(command stat -c %a "$INSTALL_DIR/data/private") == 700 ]]
            [[ $(command stat -c %u "$INSTALL_DIR/data") == "$(id -u)" ]] ;;
    esac
    [[ "$scenario" != current || ! -s "$QA_CALLS" ]]
    echo "PASS: $scenario"
)
for scenario in owner current rootless denied_group denied_mode bad_readback rootless_denied rootless_image_denied symlink chat_symlink chat_denied; do
    run_case "$scenario"
done
