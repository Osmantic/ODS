#!/usr/bin/env bash
set -u
set -o pipefail

failure=0
probe=${BASH_SOURCE[0]:-}

[[ ${PIXEL_QUALIFICATION_CUSTODY:-} == 1 ]] || exit 2

case "$probe" in
  /*/scripts/qualification-candidate-probe.sh) ;;
  *) exit 2 ;;
esac

scripts_dir=${probe%/*}
candidate_root=${scripts_dir%/*}
[[ "$scripts_dir" != "$candidate_root" && "$candidate_root" == /* && "$candidate_root" != / ]] || exit 2
[[ -d "$candidate_root" && ! -L "$candidate_root" ]] || exit 2
[[ -d "$scripts_dir" && ! -L "$scripts_dir" ]] || exit 2
[[ -d "$scripts_dir/generated" && ! -L "$scripts_dir/generated" ]] || exit 2

check_file() {
  local relative=$1
  local path="$candidate_root/$relative"
  case "$relative" in
    ''|/*|*'..'*) failure=1; return ;;
  esac
  [[ -f "$path" && ! -L "$path" && -r "$path" && -s "$path" ]] || failure=1
}

for relative in \
  VERSION \
  RELEASE-MANIFEST.json \
  scripts/generated/release-constants.json \
  pixel \
  scripts/apply.sh \
  scripts/preflight.sh \
  scripts/verify.sh \
  scripts/qualification-candidate-probe.sh
do
  check_file "$relative"
done

version=''
version_path="$candidate_root/VERSION"
if [[ -f "$version_path" && ! -L "$version_path" && -r "$version_path" ]]; then
  declare -a version_lines=()
  mapfile -t version_lines < "$version_path" || failure=1
  if (( ${#version_lines[@]} == 1 )); then
    version=${version_lines[0]}
  else
    failure=1
  fi
fi
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || failure=1

check_json_pixel_version() {
  local relative=$1
  local path="$candidate_root/$relative"
  local line=''
  local matched=''
  local count=0
  local pixel_pattern='^  "pixel"[[:space:]]*:[[:space:]]*"([0-9]+\.[0-9]+\.[0-9]+)"[,]?[[:space:]]*$'
  [[ -f "$path" && ! -L "$path" && -r "$path" ]] || { failure=1; return; }
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ $pixel_pattern ]]; then
      matched=${BASH_REMATCH[1]}
      ((count += 1))
    fi
  done < "$path"
  [[ $count -eq 1 && -n "$version" && "$matched" == "$version" ]] || failure=1
}

check_json_pixel_version RELEASE-MANIFEST.json
check_json_pixel_version scripts/generated/release-constants.json

for relative in \
  pixel \
  scripts/apply.sh \
  scripts/preflight.sh \
  scripts/verify.sh \
  scripts/qualification-candidate-probe.sh
do
  path="$candidate_root/$relative"
  if [[ -f "$path" && ! -L "$path" && -r "$path" && -s "$path" ]]; then
    /bin/bash -n -- "$path" || failure=1
  else
    failure=1
  fi
done

exit "$failure"
