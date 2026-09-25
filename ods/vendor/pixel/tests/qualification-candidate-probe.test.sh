#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PROBE="$ROOT/scripts/qualification-candidate-probe.sh"
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT

make_fixture() {
  local name=$1
  local fixture="$scratch/$name"
  install -d -m 700 "$fixture/scripts/generated"
  install -m 600 "$ROOT/VERSION" "$fixture/VERSION"
  install -m 600 "$ROOT/RELEASE-MANIFEST.json" "$fixture/RELEASE-MANIFEST.json"
  install -m 600 "$ROOT/scripts/generated/release-constants.json" "$fixture/scripts/generated/release-constants.json"
  install -m 600 "$ROOT/pixel" "$fixture/pixel"
  install -m 600 "$ROOT/scripts/apply.sh" "$fixture/scripts/apply.sh"
  install -m 600 "$ROOT/scripts/preflight.sh" "$fixture/scripts/preflight.sh"
  install -m 600 "$ROOT/scripts/verify.sh" "$fixture/scripts/verify.sh"
  install -m 600 "$PROBE" "$fixture/scripts/qualification-candidate-probe.sh"
  printf '%s\n' "$fixture"
}

run_probe() {
  local fixture=$1
  (
    cd /
    env -i \
      HOME=/nonexistent \
      PATH=/usr/bin:/bin \
      LANG=C.UTF-8 \
      PIXEL_QUALIFICATION_CUSTODY=1 \
      /bin/bash "$fixture/scripts/qualification-candidate-probe.sh"
  )
}

expect_rejection() {
  local name=$1
  local fixture=$2
  if run_probe "$fixture"; then
    printf 'qualification probe unexpectedly accepted case: %s\n' "$name" >&2
    exit 1
  fi
}

positive=$(make_fixture positive)
run_probe "$positive"

if env -i HOME=/nonexistent PATH=/usr/bin:/bin LANG=C.UTF-8 \
  /bin/bash "$positive/scripts/qualification-candidate-probe.sh"
then
  custody_exit=0
else
  custody_exit=$?
fi
if [[ $custody_exit -ne 2 ]]; then
  printf 'qualification probe returned %s outside qualification custody; expected 2\n' \
    "$custody_exit" >&2
  exit 1
fi

missing_version=$(make_fixture missing-version)
mv "$missing_version/VERSION" "$missing_version/VERSION.absent"
expect_rejection missing-version "$missing_version"

version_symlink=$(make_fixture version-symlink)
mv "$version_symlink/VERSION" "$version_symlink/VERSION.real"
ln -s VERSION.real "$version_symlink/VERSION"
expect_rejection version-symlink "$version_symlink"

version_mismatch=$(make_fixture version-mismatch)
version_mismatch_value=0.0.0
if [[ $(< "$version_mismatch/VERSION") == "$version_mismatch_value" ]]; then
  version_mismatch_value=0.0.1
fi
printf '%s\n' "$version_mismatch_value" > "$version_mismatch/VERSION"
expect_rejection version-mismatch "$version_mismatch"

malformed_version=$(make_fixture malformed-version)
printf '4.3.14\n4.3.14\n' > "$malformed_version/VERSION"
expect_rejection malformed-version "$malformed_version"

manifest_symlink=$(make_fixture manifest-symlink)
mv "$manifest_symlink/RELEASE-MANIFEST.json" "$manifest_symlink/RELEASE-MANIFEST.json.real"
ln -s RELEASE-MANIFEST.json.real "$manifest_symlink/RELEASE-MANIFEST.json"
expect_rejection manifest-symlink "$manifest_symlink"

duplicate_manifest_pixel=$(make_fixture duplicate-manifest-pixel)
sed '/"pixel"[[:space:]]*:/p' "$duplicate_manifest_pixel/RELEASE-MANIFEST.json" \
  > "$duplicate_manifest_pixel/RELEASE-MANIFEST.json.duplicate"
mv "$duplicate_manifest_pixel/RELEASE-MANIFEST.json.duplicate" \
  "$duplicate_manifest_pixel/RELEASE-MANIFEST.json"
expect_rejection duplicate-manifest-pixel "$duplicate_manifest_pixel"

nested_manifest_pixel=$(make_fixture nested-manifest-pixel)
printf '{\n  "nested": {\n    "pixel": "%s"\n  }\n}\n' \
  "$(< "$nested_manifest_pixel/VERSION")" > "$nested_manifest_pixel/RELEASE-MANIFEST.json"
expect_rejection nested-manifest-pixel "$nested_manifest_pixel"

nested_constants_pixel=$(make_fixture nested-constants-pixel)
printf '{\n  "nested": {\n    "pixel": "%s"\n  }\n}\n' \
  "$(< "$nested_constants_pixel/VERSION")" \
  > "$nested_constants_pixel/scripts/generated/release-constants.json"
expect_rejection nested-constants-pixel "$nested_constants_pixel"

missing_constants=$(make_fixture missing-constants)
mv "$missing_constants/scripts/generated/release-constants.json" \
  "$missing_constants/scripts/generated/release-constants.json.absent"
expect_rejection missing-constants "$missing_constants"

root_symlink_real=$(make_fixture root-symlink-real)
ln -s "${root_symlink_real##*/}" "$scratch/root-symlink"
expect_rejection root-directory-symlink "$scratch/root-symlink"

scripts_symlink=$(make_fixture scripts-symlink)
mv "$scripts_symlink/scripts" "$scripts_symlink/scripts.real"
ln -s scripts.real "$scripts_symlink/scripts"
expect_rejection scripts-directory-symlink "$scripts_symlink"

generated_symlink=$(make_fixture generated-symlink)
mv "$generated_symlink/scripts/generated" "$generated_symlink/scripts/generated.real"
ln -s generated.real "$generated_symlink/scripts/generated"
expect_rejection generated-directory-symlink "$generated_symlink"

empty_apply=$(make_fixture empty-apply)
truncate -s 0 "$empty_apply/scripts/apply.sh"
expect_rejection empty-apply "$empty_apply"

invalid_apply=$(make_fixture invalid-apply)
printf 'if\n' > "$invalid_apply/scripts/apply.sh"
expect_rejection invalid-apply-syntax "$invalid_apply"

probe_symlink=$(make_fixture probe-symlink)
mv "$probe_symlink/scripts/qualification-candidate-probe.sh" "$probe_symlink/scripts/probe.real"
ln -s probe.real "$probe_symlink/scripts/qualification-candidate-probe.sh"
expect_rejection probe-symlink "$probe_symlink"

echo 'Qualification candidate probe tests passed.'
