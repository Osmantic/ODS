#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${ROOT_DIR}/installers/macos/lib/installed-footprint.sh"

if ! command -v rsync >/dev/null 2>&1; then
    echo "[SKIP] macOS installed-footprint contract requires rsync"
    exit 0
fi

test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT

source_root="${test_root}/source"
install_dir="${test_root}/install"
outside_target="${test_root}/outside-symlink-target"
mkdir -p \
    "${source_root}/tests" \
    "${source_root}/docs" \
    "${source_root}/examples" \
    "${source_root}/.github" \
    "${source_root}/extensions/services/demo/docs" \
    "${source_root}/config" \
    "${source_root}/data" \
    "${install_dir}/docs" \
    "${install_dir}/data" \
    "${install_dir}/models" \
    "${install_dir}/config" \
    "${install_dir}/extensions/user" \
    "$outside_target"

printf '%s\n' "root development file" > "${source_root}/README.md"
printf '%s\n' "root development file" > "${source_root}/docs/guide.txt"
printf '%s\n' "nested runtime asset" > "${source_root}/extensions/services/demo/README.md"
printf '%s\n' "nested runtime asset" > "${source_root}/extensions/services/demo/docs/runtime.txt"
printf '%s\n' "runtime" > "${source_root}/config/runtime.yaml"

printf '%s\n' "modified documentation" > "${install_dir}/docs/stale.txt"
printf '%s\n' "modified readme" > "${install_dir}/README.md"
printf '%s\n' "ODS_VERSION=2.6.0" > "${install_dir}/.env"
printf '%s\n' "{}" > "${install_dir}/manifest.json"
printf '%s\n' "services: {}" > "${install_dir}/docker-compose.base.yml"
printf '%s\n' "user data" > "${install_dir}/data/preserve.db"
printf '%s\n' "model" > "${install_dir}/models/preserve.gguf"
printf '%s\n' "user config" > "${install_dir}/config/user.yaml"
printf '%s\n' "user extension" > "${install_dir}/extensions/user/keep.txt"
printf '%s\n' "outside data" > "${outside_target}/outside.txt"
ln -s "$outside_target" "${install_dir}/examples"

dev_only_dirs=(tests docs examples .github)
dev_only_files=(
    CHANGELOG.md
    CODE_OF_CONDUCT.md
    CONTRIBUTING.md
    EDGE-QUICKSTART.md
    FAQ.md
    QUICKSTART.md
    SECURITY.md
    README.md
    .shellcheckrc
    PSScriptAnalyzerSettings.psd1
    test-stack.sh
    .gitignore
)
dev_rsync_excludes=()
for dev_path in "${dev_only_dirs[@]}"; do
    dev_rsync_excludes+=(--exclude="/${dev_path}/")
done
for dev_path in "${dev_only_files[@]}"; do
    dev_rsync_excludes+=(--exclude="/${dev_path}")
done

copy_runtime_tree() {
    local destination="$1"
    rsync -a --quiet \
        --exclude='.git' \
        --exclude='data' \
        --exclude='logs' \
        --exclude='models' \
        --exclude='node_modules' \
        --exclude='dist' \
        --exclude='.env' \
        --exclude='*.log' \
        --exclude='.current-mode' \
        --exclude='.profiles' \
        --exclude='.target-model' \
        --exclude='.target-quantization' \
        --exclude='.offline-mode' \
        "${dev_rsync_excludes[@]}" \
        "${source_root}/" "${destination}/"
}

fresh_install_dir="${test_root}/fresh install"
mkdir -p "$fresh_install_dir"
copy_runtime_tree "$fresh_install_dir"
[[ -f "${fresh_install_dir}/config/runtime.yaml" ]]
for dev_path in tests docs examples .github README.md; do
    [[ ! -e "${fresh_install_dir}/${dev_path}" ]]
done
[[ ! -e "${fresh_install_dir}/data/installer-backups" ]]

copy_runtime_tree "$install_dir"

backup_dir="$(
    ods_quarantine_development_paths \
        "$install_dir" \
        "${dev_only_dirs[@]}" \
        "${dev_only_files[@]}"
)"

[[ -d "$backup_dir" ]]
[[ ! -e "${install_dir}/docs" ]]
[[ ! -L "${install_dir}/examples" ]]
[[ ! -e "${install_dir}/README.md" ]]
[[ "$(cat "${backup_dir}/README.md")" == "modified readme" ]]
[[ "$(cat "${backup_dir}/docs/stale.txt")" == "modified documentation" ]]
[[ -L "${backup_dir}/examples" ]]
[[ "$(readlink "${backup_dir}/examples")" == "$outside_target" ]]
[[ "$(cat "${outside_target}/outside.txt")" == "outside data" ]]

for preserved_path in \
    "data/preserve.db" \
    "models/preserve.gguf" \
    "config/user.yaml" \
    "extensions/user/keep.txt" \
    "extensions/services/demo/README.md" \
    "extensions/services/demo/docs/runtime.txt"; do
    [[ -e "${install_dir}/${preserved_path}" ]] \
        || { echo "[FAIL] missing preserved path: ${preserved_path}"; exit 1; }
done

unmanaged_dir="${test_root}/unmanaged install"
mkdir -p "${unmanaged_dir}/docs"
printf '%s\n' "personal readme" > "${unmanaged_dir}/README.md"
printf '%s\n' "personal docs" > "${unmanaged_dir}/docs/personal.txt"
copy_runtime_tree "$unmanaged_dir"
[[ "$(cat "${unmanaged_dir}/README.md")" == "personal readme" ]]
[[ "$(cat "${unmanaged_dir}/docs/personal.txt")" == "personal docs" ]]

if ods_quarantine_development_paths "$install_dir" "../outside" >/dev/null 2>&1; then
    echo "[FAIL] backup helper accepted a path outside the installation root"
    exit 1
fi

echo "[PASS] macOS installed-footprint contract"
