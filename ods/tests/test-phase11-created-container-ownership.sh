#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
eval "$(sed -n '/^_phase11_start_created_owned() {/,/^}$/p' "$ROOT/installers/phases/11-services.sh")"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
export LOG_FILE="$tmp/log"
INSTALL_DIR='/opt/owner ods'
export DOCKER_CMD=fake_docker
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
pass() { printf 'PASS: %s\n' "$*"; }
a="$(printf 'a%.0s' {1..64})"; b="$(printf 'b%.0s' {1..64})"
c="$(printf 'c%.0s' {1..64})"; d="$(printf 'd%.0s' {1..64})"
e="$(printf 'e%.0s' {1..64})"
fake_docker() {
    case "$1" in
        ps) [[ "${LIST_FAIL:-0}" == 0 ]] || return 1; printf '%s\n' "$inventory" ;;
        inspect)
            [[ "${INSPECT_FAIL:-0}" == 0 ]] || return 1
            local fmt="$3" id="$4"
            case "$fmt" in
                *working_dir*) if [[ "$id" == "$b" ]]; then printf '/other\n'; else printf '%s\n' "$INSTALL_DIR"; fi ;;
                *State.Status*) if [[ "$id" == "$e" ]]; then printf 'running\n'; else printf 'created\n'; fi ;;
                *) if [[ "$id" == "$c" ]]; then printf 'other\n'; elif [[ "$id" == "$d" ]]; then printf '\n'; else printf 'ods\n'; fi ;;
            esac ;;
        start) printf '%s\n' "$2" >> "$tmp/started"; return "${START_FAIL:-0}" ;;
        *) return 1 ;;
    esac
}
inventory="$(printf '%s\n' "$a" "$b" "$c" "$d" "$e")"
_phase11_start_created_owned
[[ "$(cat "$tmp/started")" == "$a" ]] || fail 'foreign or running container started'
pass 'only this installation starts; foreign roots, projects, missing labels and running state remain untouched'
for mode in list inspect malformed; do
    : > "$tmp/started"
    LIST_FAIL=0; INSPECT_FAIL=0
    case "$mode" in list) LIST_FAIL=1 ;; inspect) INSPECT_FAIL=1 ;; malformed) inventory="$a"$'\nnot-an-id' ;; esac
    if _phase11_start_created_owned; then fail "$mode accepted"; fi
    [[ ! -s "$tmp/started" ]] || fail "$mode failure started a container"
    pass "$mode evidence failure refuses before any start"
done
LIST_FAIL=0; INSPECT_FAIL=0; inventory=''
_phase11_start_created_owned
[[ ! -s "$tmp/started" ]] || fail 'empty inventory started a container'
pass 'empty inventory does nothing'
inventory="$a"; START_FAIL=1
if _phase11_start_created_owned; then fail 'start failure swallowed'; fi
pass 'owned start failure is returned'
