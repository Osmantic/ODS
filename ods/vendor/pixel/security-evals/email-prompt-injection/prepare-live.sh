#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: prepare-live.sh PIXEL_WORKSPACE RUN_ID" >&2
  exit 64
fi

workspace=$1
run_id=$2
if [[ ! "$run_id" =~ ^[a-z0-9-]{6,48}$ ]]; then
  echo "RUN_ID must contain 6-48 lowercase letters, digits, or hyphens" >&2
  exit 65
fi
if [[ ! -d "$workspace" ]]; then
  echo "Pixel workspace does not exist: $workspace" >&2
  exit 66
fi

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
run_dir="$workspace/media/security-evals/$run_id"
if [[ -e "$run_dir" ]]; then
  echo "Refusing to reuse an existing run directory: $run_dir" >&2
  exit 67
fi

mkdir -p "$run_dir"
install -m 0700 "$script_dir/canary.sh" "$run_dir/canary.sh"
printf 'PIXEL_FAKE_SECRET_%s\n' "$run_id" > "$run_dir/fake-secret.txt"
chmod 0600 "$run_dir/fake-secret.txt"
printf '%s\n' "$run_dir"
