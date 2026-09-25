#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
# shellcheck source=scripts/lib/bootstrap-packages.sh
source "$ROOT/scripts/lib/bootstrap-packages.sh"
# shellcheck source=scripts/lib/bootstrap-download.sh
source "$ROOT/scripts/lib/bootstrap-download.sh"
# shellcheck source=scripts/generated/release.env
source "$ROOT/scripts/generated/release.env"

apply=0
for arg in "$@"; do
  case "$arg" in --apply|--confirm) apply=1 ;; --help|-h)
    echo "Usage: ./pixel bootstrap [--apply]"; exit 0 ;; *) pixel_die "Unknown bootstrap option: $arg" ;; esac
done

pixel_require_linux
pixel_version=$PIXEL_GENERATED_RELEASE_VERSION
node_requirement=$PIXEL_GENERATED_NODE_REQUIREMENT
openclaw_version=$PIXEL_GENERATED_OPENCLAW_VERSION
discord_plugin_version=$PIXEL_GENERATED_DISCORD_PLUGIN_VERSION
searxng_plugin_version=$PIXEL_GENERATED_SEARXNG_PLUGIN_VERSION
llama_plugin_version=$PIXEL_GENERATED_LLAMA_PLUGIN_VERSION
openclaw_installer_url=$PIXEL_GENERATED_OPENCLAW_INSTALLER_URL
openclaw_installer_sha256=$PIXEL_GENERATED_OPENCLAW_INSTALLER_SHA256
if [[ ${PIXEL_GENERATED_OPENCLAW_INSTALLER_MIRRORS+x} == x ]]; then
  openclaw_installer_mirrors=("${PIXEL_GENERATED_OPENCLAW_INSTALLER_MIRRORS[@]}")
else
  openclaw_installer_mirrors=()
fi
openclaw_package_url=$PIXEL_GENERATED_OPENCLAW_PACKAGE_URL
openclaw_package_sha256=$PIXEL_GENERATED_OPENCLAW_PACKAGE_SHA256
openclaw_package_integrity=$PIXEL_GENERATED_OPENCLAW_PACKAGE_INTEGRITY
discord_plugin_url=$PIXEL_GENERATED_DISCORD_PLUGIN_URL
discord_plugin_sha256=$PIXEL_GENERATED_DISCORD_PLUGIN_SHA256
discord_plugin_integrity=$PIXEL_GENERATED_DISCORD_PLUGIN_INTEGRITY
searxng_plugin_url=$PIXEL_GENERATED_SEARXNG_PLUGIN_URL
searxng_plugin_sha256=$PIXEL_GENERATED_SEARXNG_PLUGIN_SHA256
searxng_plugin_integrity=$PIXEL_GENERATED_SEARXNG_PLUGIN_INTEGRITY
llama_plugin_url=$PIXEL_GENERATED_LLAMA_PLUGIN_URL
llama_plugin_sha256=$PIXEL_GENERATED_LLAMA_PLUGIN_SHA256
llama_plugin_integrity=$PIXEL_GENERATED_LLAMA_PLUGIN_INTEGRITY
sandbox_image=$PIXEL_GENERATED_SANDBOX_IMAGE
install_dir=${PIXEL_INSTALL_DIR:-$HOME/.local/share/pixel}
web_courier_enabled=1
web_courier_wheelhouse="$install_dir/bootstrap/web-courier/wheelhouse"
web_courier_browser_path="$install_dir/browsers"
if [[ -f "$ROOT/.env" ]]; then
  pixel_load_env
  [[ ${PIXEL_RELEASE_VERSION:-$pixel_version} == "$pixel_version" ]] || pixel_die ".env Pixel version differs from the release manifest; rerun ./pixel configure"
  [[ ${PIXEL_OPENCLAW_VERSION:-$openclaw_version} == "$openclaw_version" ]] || pixel_die ".env OpenClaw version differs from the release manifest; rerun ./pixel configure"
  [[ ${PIXEL_DISCORD_PLUGIN_VERSION:-$discord_plugin_version} == "$discord_plugin_version" ]] || pixel_die ".env Discord plugin version differs from the release manifest; rerun ./pixel configure"
  [[ ${PIXEL_SEARXNG_PLUGIN_VERSION:-$searxng_plugin_version} == "$searxng_plugin_version" ]] || pixel_die ".env SearXNG plugin version differs from the release manifest; rerun ./pixel configure"
  [[ ${PIXEL_LLAMA_CPP_PLUGIN_VERSION:-$llama_plugin_version} == "$llama_plugin_version" ]] || pixel_die ".env llama.cpp plugin version differs from the release manifest; rerun ./pixel configure"
  [[ ${PIXEL_SANDBOX_IMAGE:-$sandbox_image} == "$sandbox_image" ]] || pixel_die ".env sandbox image differs from the release manifest; rerun ./pixel configure"
  install_dir=${PIXEL_INSTALL_DIR:-$install_dir}
  web_courier_enabled=${PIXEL_WEB_COURIER_ENABLED:-1}
  web_courier_wheelhouse=${PIXEL_WEB_COURIER_WHEELHOUSE:-$web_courier_wheelhouse}
  web_courier_browser_path=${PIXEL_WEB_COURIER_BROWSER_PATH:-$web_courier_browser_path}
fi
[[ "$web_courier_enabled" == 0 || "$web_courier_enabled" == 1 ]] || pixel_die "PIXEL_WEB_COURIER_ENABLED must be 0 or 1"
pixel_safe_absolute_dir "$install_dir" PIXEL_INSTALL_DIR
pixel_safe_absolute_dir "$web_courier_wheelhouse" PIXEL_WEB_COURIER_WHEELHOUSE
pixel_safe_absolute_dir "$web_courier_browser_path" PIXEL_WEB_COURIER_BROWSER_PATH
[[ "$web_courier_wheelhouse" == "$install_dir/"* ]] || pixel_die "Web Courier wheelhouse must be under PIXEL_INSTALL_DIR"
[[ "$web_courier_browser_path" == "$install_dir/"* ]] || pixel_die "Web Courier browser cache must be under PIXEL_INSTALL_DIR"
PIXEL_INSTALL_DIR=$install_dir
pixel_acquire_deployment_lock exclusive

base_commands=(age curl git jq python3 openssl docker rg setfacl flock)
missing=()
for command in "${base_commands[@]}"; do command -v "$command" >/dev/null 2>&1 || missing+=("$command"); done
if [[ ${#missing[@]} -gt 0 ]]; then
  if [[ $apply != 1 ]]; then
    pixel_die "Missing host commands: ${missing[*]}. Re-run with --apply to install supported Ubuntu/Debian packages."
  fi
  command -v sudo >/dev/null 2>&1 || pixel_die "sudo is required to install host packages"
  pixel_log "Installing base packages for: ${missing[*]}"
  mapfile -t missing_packages < <(pixel_packages_for_commands "${missing[@]}")
  [[ ${#missing_packages[@]} -gt 0 ]] || pixel_die "Missing commands did not resolve to supported packages"
  sudo apt-get update
  sudo apt-get install -y "${missing_packages[@]}"
fi
docker info >/dev/null 2>&1 || pixel_die "Docker daemon is unavailable to the current user"

if [[ "$web_courier_enabled" == 1 ]]; then
  venv_probe=$(mktemp -d)
  if ! python3 -m venv "$venv_probe/check" >/dev/null 2>&1; then
    rm -rf -- "$venv_probe"
    if [[ $apply != 1 ]]; then
      pixel_die "python3-venv is required for Web Courier. Re-run with --apply."
    fi
    command -v sudo >/dev/null 2>&1 || pixel_die "sudo is required to install python3-venv"
    sudo apt-get update
    sudo apt-get install -y python3-venv
  else
    rm -rf -- "$venv_probe"
  fi
fi

openclaw_bin=${OPENCLAW_BIN:-}
if [[ -z "$openclaw_bin" || ! -x "$openclaw_bin" ]]; then
  if [[ -x "$HOME/.npm-global/bin/openclaw" ]]; then openclaw_bin="$HOME/.npm-global/bin/openclaw"; else openclaw_bin=$(command -v openclaw || true); fi
fi
installed_version=""
if [[ -n "$openclaw_bin" && -x "$openclaw_bin" ]]; then installed_version=$("$openclaw_bin" --version 2>/dev/null | grep -Eo '[0-9]{4}\.[0-9]+\.[0-9]+(-[0-9]+)?' | head -n1); fi
if [[ "$installed_version" != "$openclaw_version" ]]; then
  if [[ $apply != 1 ]]; then
    pixel_die "OpenClaw $openclaw_version is required; found ${installed_version:-none}. Re-run with --apply."
  fi
  installer=$(mktemp)
  openclaw_package=$(mktemp --suffix=.tgz)
  trap 'rm -f -- "$installer" "$openclaw_package"' EXIT
  pixel_log "Downloading the pinned OpenClaw installer and package"
  download_verified "OpenClaw installer" "$openclaw_installer_sha256" 2000000 "$installer" "" "$openclaw_installer_url" "${openclaw_installer_mirrors[@]}"
  download_verified "OpenClaw package" "$openclaw_package_sha256" 30000000 "$openclaw_package" "$openclaw_package_integrity" "$openclaw_package_url"
  bash "$installer" --no-onboard --version "$openclaw_package"
  rm -f -- "$installer" "$openclaw_package"
  trap - EXIT
  hash -r
  if [[ -x "$HOME/.npm-global/bin/openclaw" ]]; then openclaw_bin="$HOME/.npm-global/bin/openclaw"; else openclaw_bin=$(command -v openclaw || true); fi
  [[ -x "$openclaw_bin" ]] || pixel_die "OpenClaw installer completed but the executable was not found"
fi
installed_version=$("$openclaw_bin" --version 2>/dev/null | grep -Eo '[0-9]{4}\.[0-9]+\.[0-9]+(-[0-9]+)?' | head -n1)
[[ "$installed_version" == "$openclaw_version" ]] || pixel_die "OpenClaw install verification failed: expected $openclaw_version, found ${installed_version:-none}"
pixel_require_command node
node_major=$(node -p 'Number(process.versions.node.split(".")[0])')
node_minimum=${node_requirement#>=}
[[ "$node_minimum" =~ ^[0-9]+$ && "$node_major" -ge "$node_minimum" ]] || pixel_die "Node.js $node_requirement is required; found $(node --version)"

install_plugin() {
  local id=$1 version=$2 integrity=$3 url=$4 sha256=$5 package_name=$6 archive stage observed
  archive="$install_dir/bootstrap/openclaw-plugins/$id-$version.tgz"
  observed=""
  [[ -f "$archive" && ! -L "$archive" ]] && observed=$(sha256sum "$archive" | awk '{print $1}')
  if [[ "$observed" != "$sha256" ]]; then
    [[ $apply == 1 ]] || pixel_die "Verified package cache for $id@$version is missing or invalid. Re-run with --apply."
    install -d -m 700 "$(dirname "$archive")"
    stage=$(mktemp "$(dirname "$archive")/.$id.XXXXXX.tgz")
    download_verified "OpenClaw plugin $id" "$sha256" 50000000 "$stage" "$integrity" "$url"
    chmod 600 "$stage"
    mv -f -- "$stage" "$archive"
  fi
  OPENCLAW_BIN=$openclaw_bin pixel_plugin_release_matches "$id" "$version" "$integrity" "$sha256" "$archive" && {
    pixel_log "Pinned plugin available: $id@$version"
    return
  }
  [[ $apply == 1 ]] || pixel_die "OpenClaw plugin $id is missing or differs from the pinned release. Re-run with --apply."
  pixel_log "Installing verified plugin $id@$version"
  "$openclaw_bin" plugins install --pin --force "$package_name@$version"
  PIXEL_PLUGIN_RECEIPT_MODE=record OPENCLAW_BIN=$openclaw_bin \
    pixel_plugin_release_matches "$id" "$version" "$integrity" "$sha256" "$archive" || pixel_die "Plugin verification failed after install: $id@$version"
}
install_plugin discord "$discord_plugin_version" "$discord_plugin_integrity" "$discord_plugin_url" "$discord_plugin_sha256" '@openclaw/discord'
install_plugin searxng "$searxng_plugin_version" "$searxng_plugin_integrity" "$searxng_plugin_url" "$searxng_plugin_sha256" '@openclaw/searxng-plugin'
install_plugin llama-cpp "$llama_plugin_version" "$llama_plugin_integrity" "$llama_plugin_url" "$llama_plugin_sha256" '@openclaw/llama-cpp-provider'

sandbox_uid=$(id -u)
[[ "$sandbox_uid" =~ ^[1-9][0-9]*$ ]] || pixel_die "Pixel must build its sandbox as a non-root deployment owner"
candidate_sandbox_ref=$(pixel_sandbox_candidate_tag "$pixel_version" "$sandbox_uid")
if docker image inspect "$candidate_sandbox_ref" >/dev/null 2>&1; then
  pixel_validate_sandbox_image "$candidate_sandbox_ref" "$pixel_version" "$sandbox_uid" >/dev/null \
    || pixel_die "Existing candidate sandbox image is unsafe and will not be replaced"
  pixel_log "Pinned Pixel sandbox candidate is already available"
else
  [[ $apply == 1 ]] || pixel_die "Sandbox candidate image is missing. Re-run with --apply."
  pixel_log "Building the pinned Pixel sandbox candidate image"
  docker build --build-arg "PIXEL_SANDBOX_UID=$sandbox_uid" \
    --label "org.osmantic.pixel.sandbox-version=$pixel_version" \
    --label "org.osmantic.pixel.sandbox-uid=$sandbox_uid" \
    -t "$candidate_sandbox_ref" "$ROOT/deploy/sandbox"
fi
pixel_validate_sandbox_image "$candidate_sandbox_ref" "$pixel_version" "$sandbox_uid" >/dev/null \
  || pixel_die "Sandbox candidate image verification failed"

if [[ "$web_courier_enabled" == 1 ]]; then
  requirements="$ROOT/deploy/web-courier/requirements.lock"
  requirements_hash=$(sha256sum "$requirements" | awk '{print $1}')
  cache_root="$install_dir/bootstrap/web-courier"
  bootstrap_venv="$cache_root/venv"
  browser_marker="$web_courier_browser_path/.pixel-chromium-ready"
  verify_wheelhouse() {
    local verify_stage status=0
    verify_stage=$(mktemp -d)
    "$bootstrap_venv/bin/python" -m pip download --disable-pip-version-check --no-index --no-deps --only-binary=:all: --require-hashes --find-links "$web_courier_wheelhouse" --dest "$verify_stage" --requirement "$requirements" >/dev/null 2>&1 || status=$?
    rm -rf -- "$verify_stage"
    return "$status"
  }
  courier_ready=1
  if [[ ! -f "$web_courier_wheelhouse/.requirements.sha256" ]] || [[ $(cat "$web_courier_wheelhouse/.requirements.sha256") != "$requirements_hash" ]]; then courier_ready=0; fi
  [[ -x "$bootstrap_venv/bin/python" ]] || courier_ready=0
  if [[ $courier_ready == 1 ]] && ! "$bootstrap_venv/bin/python" -c 'import playwright, trafilatura' >/dev/null 2>&1; then courier_ready=0; fi
  if [[ $courier_ready == 1 ]] && ! verify_wheelhouse; then courier_ready=0; fi
  if [[ ! -f "$browser_marker" ]] || [[ $(cat "$browser_marker") != "$requirements_hash" ]]; then courier_ready=0; fi
  if [[ $courier_ready == 1 ]] && ! find "$web_courier_browser_path" -type f \( -name chrome -o -name chrome-headless-shell \) -perm -u+x -print -quit | grep -q .; then courier_ready=0; fi
  if [[ $courier_ready != 1 ]]; then
    [[ $apply == 1 ]] || pixel_die "Pinned Web Courier wheels/Chromium are missing. Re-run with --apply."
    install -d -m 700 "$cache_root" "$web_courier_browser_path"
    wheel_stage="$cache_root/.wheelhouse.stage.$$"
    venv_stage="$cache_root/.venv.stage.$$"
    [[ "$wheel_stage" == "$cache_root/"* && "$venv_stage" == "$cache_root/"* ]] || pixel_die "Unsafe Web Courier cache staging path"
    rm -rf -- "$wheel_stage" "$venv_stage"
    python3 -m venv "$venv_stage"
    pixel_log "Downloading the version-locked Web Courier Python wheel set"
    "$venv_stage/bin/python" -m pip download --disable-pip-version-check --no-deps --only-binary=:all: --require-hashes --dest "$wheel_stage" --requirement "$requirements"
    printf '%s\n' "$requirements_hash" > "$wheel_stage/.requirements.sha256"
    "$venv_stage/bin/python" -m pip install --disable-pip-version-check --require-hashes --only-binary=:all: --no-index --no-deps --find-links "$wheel_stage" --requirement "$requirements"
    "$venv_stage/bin/python" -m pip check
    rm -rf -- "$web_courier_wheelhouse" "$bootstrap_venv"
    mv "$wheel_stage" "$web_courier_wheelhouse"
    mv "$venv_stage" "$bootstrap_venv"
    pixel_log "Installing Chromium host libraries and the pinned Playwright browser"
    "$bootstrap_venv/bin/python" -m playwright install-deps chromium
    PLAYWRIGHT_BROWSERS_PATH="$web_courier_browser_path" "$bootstrap_venv/bin/python" -m playwright install chromium
    printf '%s\n' "$requirements_hash" > "$browser_marker"
  fi
fi

pixel_log "Bootstrap passed: OpenClaw $openclaw_version, required plugins, Docker, sandbox image, and optional Web Courier dependencies are available."
if [[ ! -f "$ROOT/.env" ]]; then pixel_log "Next: ./pixel configure"; fi
