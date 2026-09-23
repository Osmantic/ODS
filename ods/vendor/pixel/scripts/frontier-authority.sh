#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"

usage() {
  cat >&2 <<'EOF'
Usage:
  ./pixel frontier-authority show
  ./pixel frontier-authority audit [LIMIT]
  ./pixel frontier-authority grant GRANT.json TTL_MINUTES --confirm
  ./pixel frontier-authority revoke GRANT_ID --confirm
  ./pixel frontier-pause REASON --confirm
  ./pixel frontier-resume REASON --confirm
EOF
  exit 2
}

pixel_load_env
[[ ${PIXEL_FRONTIER_BROKER_ENABLED:-0} == 1 ]] || pixel_die "Frontier limb is disabled"
broker=(sudo -u "$PIXEL_FRONTIER_BROKER_USER" "$PIXEL_FRONTIER_BROKER_INSTALL_DIR/broker.py" --policy "$PIXEL_FRONTIER_POLICY_PATH" --state "$PIXEL_FRONTIER_BROKER_STATE_DIR")
command=${1:-}
[[ -n "$command" ]] || usage
shift

case "$command" in
  show)
    [[ $# == 0 ]] || usage
    "${broker[@]}" --authority-show
    ;;
  audit)
    [[ $# -le 1 ]] || usage
    limit=${1:-100}
    [[ "$limit" =~ ^[0-9]+$ && "$limit" -ge 1 && "$limit" -le 1000 ]] || pixel_die "Audit limit must be 1..1000"
    "${broker[@]}" --authority-audit --authority-audit-limit "$limit"
    ;;
  grant)
    [[ $# == 3 && $3 == --confirm ]] || usage
    grant_file=$1
    ttl=$2
    [[ -f "$grant_file" && ! -L "$grant_file" ]] || pixel_die "Grant input must be a regular non-symlink file"
    [[ "$ttl" =~ ^[0-9]+$ && "$ttl" -ge 1 && "$ttl" -le 10080 ]] || pixel_die "Lease TTL must be 1..10080 minutes"
    for required_command in sudo mktemp install; do pixel_require_command "$required_command"; done
    # Stage under a root-owned runtime directory. A compromised broker identity cannot
    # predict, replace, or redirect the operator's privileged copy destination.
    sudo install -d -o root -g "$PIXEL_FRONTIER_BROKER_GROUP" -m 0750 /run/pixel-frontier-operator
    staged=$(sudo mktemp /run/pixel-frontier-operator/grant.XXXXXXXXXX.json)
    trap 'sudo rm -f -- "$staged"' EXIT
    sudo install -o root -g "$PIXEL_FRONTIER_BROKER_GROUP" -m 0640 "$grant_file" "$staged"
    "${broker[@]}" --authority-grant "$staged" --lease-ttl-minutes "$ttl"
    ;;
  revoke)
    [[ $# == 2 && $2 == --confirm ]] || usage
    [[ "$1" =~ ^[a-z][a-z0-9_.-]{1,127}$ ]] || pixel_die "Unsafe Frontier grant ID"
    "${broker[@]}" --authority-revoke "$1"
    ;;
  pause|resume)
    [[ $# == 2 && $2 == --confirm ]] || usage
    reason=$1
    [[ -n "$reason" && ${#reason} -le 500 ]] || pixel_die "Pause/resume reason must be 1..500 characters"
    if [[ "$command" == pause ]]; then
      "${broker[@]}" --authority-pause --reason "$reason"
    else
      "${broker[@]}" --authority-resume --reason "$reason"
    fi
    ;;
  *) usage ;;
esac
