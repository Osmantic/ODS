#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
install="$(mktemp -d)"
trap 'rmdir -- "$install"' EXIT
export INSTALL_DIR="$install"

# Exercise the actual command function without dispatching the full CLI.
source <(sed -n '/^cmd_stop() {/,/^}/p' "$root/ods-cli")
check_install() { :; }
load_env() { :; }
get_compose_flags() {
    printf '%s\n' '-f docker-compose.base.yml -f docker-compose.cpu.yml'
}
resolve_service() { printf '%s\n' "$1"; }
calls=()
_compose_run_with_summary() { calls=("$@"); }

cmd_stop
[[ "${calls[*]}" == 'Stopping all services -f docker-compose.base.yml -f docker-compose.cpu.yml stop' ]] ||
    { printf '%s\n' 'Whole-stack stop removed container ownership proof'; exit 1; }

cmd_stop perplexica
[[ "${calls[*]}" == 'Stopping perplexica -f docker-compose.base.yml -f docker-compose.cpu.yml stop perplexica' ]] ||
    { printf '%s\n' 'Service stop changed unexpectedly'; exit 1; }

printf '%s\n' 'PASS: whole-stack and service stop preserve Compose containers'
