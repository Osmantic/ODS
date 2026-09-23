#!/usr/bin/env bash
set -euo pipefail
workspace=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source_dir=${PIXEL_INGEST_FROM:?Set PIXEL_INGEST_FROM to a directory of approved documents}
library_name=${PIXEL_LIBRARY_NAME:-private-library}
destination="$workspace/library/$library_name"
[[ -d "$source_dir" ]] || { echo "Source directory does not exist: $source_dir" >&2; exit 1; }
mkdir -p "$destination"
rsync -a --exclude '.git/' --exclude '*.key' --exclude '*.pem' --exclude '.env' "$source_dir/" "$destination/"
if git -C "$workspace" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C "$workspace" add -- "library/$library_name"
  if ! git -C "$workspace" diff --cached --quiet; then
    [[ -n "${PIXEL_GIT_NAME:-}" && -n "${PIXEL_GIT_EMAIL:-}" ]] || { echo "Set PIXEL_GIT_NAME and PIXEL_GIT_EMAIL to commit ingested material." >&2; exit 1; }
    git -C "$workspace" -c user.name="$PIXEL_GIT_NAME" -c user.email="$PIXEL_GIT_EMAIL" commit -m "update $library_name"
  fi
fi
echo "$destination"
