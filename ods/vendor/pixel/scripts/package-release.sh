#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
pixel_require_command git
pixel_require_command node
pixel_require_command sha256sum
pixel_require_command uname
[[ $(uname -s) == Linux ]] || pixel_die "Release packaging requires Linux so archive ownership and executable modes are deterministic"
version=$(tr -d '[:space:]' < "$ROOT/VERSION")
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || pixel_die "VERSION must be semantic versioning"
node -e 'const f=require("./RELEASE-MANIFEST.json"); const v=require("fs").readFileSync("VERSION","utf8").trim(); if(f.pixel!==v) process.exit(1)' || pixel_die "RELEASE-MANIFEST.json and VERSION disagree"
[[ -z $(git -C "$ROOT" status --porcelain --untracked-files=normal) ]] || pixel_die "Release packaging requires a clean worktree"

output="$ROOT/dist/pixel-$version.tar.gz"
checksum="$output.sha256"
sbom="$ROOT/dist/pixel-$version.cdx.json"
sbom_checksum="$sbom.sha256"
provenance="$ROOT/dist/pixel-$version.intoto.jsonl"
provenance_checksum="$provenance.sha256"
update="$ROOT/dist/pixel-$version.update.json"
update_checksum="$update.sha256"
update_signature="$update.sig"
[[ ! -e "$update_signature" && ! -L "$update_signature" ]] || pixel_die "Archive the existing release signature before rebuilding this version"
stage=$(mktemp -d)
artifact_stage=""
trap 'rm -rf -- "$stage"; [[ -z "$artifact_stage" ]] || rm -f -- "$artifact_stage"' EXIT

write_checksum() {
  local source=$1 destination=$2 temporary
  temporary=$(mktemp "$ROOT/dist/.pixel-checksum.XXXXXXXXXXXX")
  (cd "$(dirname "$source")" && sha256sum "$(basename "$source")") > "$temporary"
  chmod 600 "$temporary"
  mv -fT -- "$temporary" "$destination"
}

git -C "$ROOT" archive --format=tar --prefix="pixel-$version/" HEAD | tar -xf - -C "$stage"
node "$ROOT/scripts/release-identity.mjs" --output "$stage/pixel-$version/RELEASE-IDENTITY.json"
find "$stage/pixel-$version" -type d -exec chmod 755 {} +
find "$stage/pixel-$version" -type f -exec chmod 644 {} +
for file in pixel scripts/*.sh workspace-template/scripts/*.sh tests/*.sh tests/fixtures/bin/*; do
  [[ -f "$stage/pixel-$version/$file" ]] && chmod 755 "$stage/pixel-$version/$file"
done
mkdir -p "$ROOT/dist"
[[ -d "$ROOT/dist" && ! -L "$ROOT/dist" ]] || pixel_die "dist must be a real directory"
node "$ROOT/scripts/generate-release-sbom.mjs" --output "$sbom"
install -m 644 "$sbom" "$stage/pixel-$version/SBOM.cdx.json"
artifact_stage=$(mktemp "$ROOT/dist/.pixel-$version.XXXXXXXXXXXX.tar.gz")
tar --sort=name --mtime='UTC 2026-01-01' --owner=0 --group=0 --numeric-owner -C "$stage" -cf - "pixel-$version" | gzip -n > "$artifact_stage"
chmod 600 "$artifact_stage"
mv -fT -- "$artifact_stage" "$output"
artifact_stage=""
write_checksum "$output" "$checksum"
node "$ROOT/scripts/generate-release-provenance.mjs" --artifact "$output" --sbom "$sbom" --output "$provenance"
node "$ROOT/scripts/generate-release-update.mjs" --artifact "$output" --sbom "$sbom" --provenance "$provenance" --output "$update"
write_checksum "$sbom" "$sbom_checksum"
write_checksum "$provenance" "$provenance_checksum"
write_checksum "$update" "$update_checksum"
pixel_log "Release artifact: $output"
pixel_log "Checksum: $checksum"
pixel_log "SBOM: $sbom"
pixel_log "SBOM checksum: $sbom_checksum"
pixel_log "Provenance: $provenance"
pixel_log "Provenance checksum: $provenance_checksum"
pixel_log "Update envelope: $update"
pixel_log "Update envelope checksum: $update_checksum"
