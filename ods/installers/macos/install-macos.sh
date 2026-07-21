#!/bin/bash
# ============================================================================
# ODS macOS Installer -- Main Orchestrator
# ============================================================================
# Standalone macOS Apple Silicon installer. Does not modify any existing files.
#
# macOS: llama-server runs natively with Metal on the host. Everything else
#        runs in Docker. Colima uses a private vmnet bridge because its
#        host.docker.internal forwarding can become unreachable under load.
#
# Usage:
#   ./install-macos.sh                  # Interactive install
#   ./install-macos.sh --tier 3         # Force tier 3
#   ./install-macos.sh --dry-run        # Validate without installing
#   ./install-macos.sh --all            # Enable all optional services
#   ./install-macos.sh --non-interactive # Headless install (defaults)
#   ./install-macos.sh --no-bootstrap   # Wait for the full model before launch
#
# ============================================================================

# Detect dry-run before the Bash bootstrap. macOS ships Bash 3.2, so argument
# parsing below cannot run until we hand off to Homebrew Bash. A dry-run must
# still remain mutation-free when that newer shell is not installed yet.
_ods_bootstrap_dry_run=false
for _ods_bootstrap_arg in "$@"; do
  if [ "$_ods_bootstrap_arg" = "--dry-run" ]; then
    _ods_bootstrap_dry_run=true
    break
  fi
done

_ods_bash_is_modern() {
  [ -x "$1" ] || return 1
  "$1" -c '[ "${BASH_VERSINFO[0]:-0}" -ge 4 ]' >/dev/null 2>&1
}

# Guard: macOS ships Bash 3.2 (GPL). ods-cli and our libs need Bash 4+.
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
  # Default candidate paths cover standard Apple Silicon and Intel Homebrew
  # prefixes. If brew is already on PATH we also ask it for its actual prefix,
  # which handles custom installs (e.g. /Volumes/X/homebrew).
  candidates=(/opt/homebrew/bin/bash /usr/local/bin/bash)
  if command -v brew >/dev/null 2>&1; then
    brew_prefix="$(brew --prefix 2>/dev/null)"
    [ -n "$brew_prefix" ] && candidates=("$brew_prefix/bin/bash" "${candidates[@]}")
  fi
  for candidate in "${candidates[@]}"; do
    if _ods_bash_is_modern "$candidate"; then
      exec "$candidate" "$0" "$@"
    fi
  done
  if [ "$_ods_bootstrap_dry_run" = true ]; then
    echo "[DRY RUN] Bash 4+ is not installed; a real install would run 'brew install bash'."
    echo "[DRY RUN] No host changes were made."
    exit 0
  fi
  if ! command -v brew >/dev/null 2>&1; then
    echo "ODS requires Bash 4+ (you have ${BASH_VERSION})." >&2
    echo "macOS ships only Bash 3.2 — the last GPLv2 release Apple was willing to bundle." >&2
    echo >&2
    echo "Two-step bootstrap (one-time):" >&2
    echo >&2
    echo "  1. Install Homebrew. Requires an admin password and ~3 min:" >&2
    echo "       /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"" >&2
    echo >&2
    echo "  2. Re-run this installer. It will detect Homebrew, run \`brew install bash\` for you, and proceed." >&2
    echo >&2
    echo "If \`brew install\` later complains \"Need sudo access on macOS\" under --non-interactive," >&2
    echo "Homebrew is asking for passwordless sudo. Workaround:" >&2
    echo "  echo \"\$USER ALL=(ALL) NOPASSWD: ALL\" | sudo tee /etc/sudoers.d/99-ods-install >/dev/null" >&2
    echo "  sudo chmod 440 /etc/sudoers.d/99-ods-install" >&2
    echo "(remove the file after install if you'd rather not keep passwordless sudo.)" >&2
    exit 1
  fi
  echo "Installing Bash 4+ via Homebrew (one-time setup)..."
  brew install bash || { echo "brew install bash failed" >&2; exit 1; }
  brew_prefix="$(brew --prefix 2>/dev/null)"
  if [ -n "$brew_prefix" ] && _ods_bash_is_modern "$brew_prefix/bin/bash"; then
    exec "$brew_prefix/bin/bash" "$0" "$@"
  fi
  for candidate in /opt/homebrew/bin/bash /usr/local/bin/bash; do
    if _ods_bash_is_modern "$candidate"; then
      exec "$candidate" "$0" "$@"
    fi
  done
  echo "Homebrew bash installed but not found in expected paths." >&2
  exit 1
fi

set -euo pipefail

# ── Parse arguments ──
DRY_RUN=false
FORCE=false
NON_INTERACTIVE=false
TIER_OVERRIDE=""
ENABLE_VOICE=false
ENABLE_WORKFLOWS=false
ENABLE_RAG=false
ENABLE_RECOMMENDED=true
# Hermes Agent is the new default agent as of 2026-05-12. OpenClaw is
# deprecated and gates behind --openclaw for the deprecation release.
ENABLE_HERMES=true
ENABLE_OPENCLAW=false
ENABLE_BRAVE_SEARCH=false
ENABLE_APE=true
ENABLE_PERPLEXICA=false
ENABLE_PRIVACY_SHIELD=false
ENABLE_ODS_PROXY=false
ENABLE_TAILSCALE=false
# Langfuse defaults OFF because its clickhouse + postgres + minio stack adds
# ~500MB baseline memory. Enable via --langfuse, --all, or post-install
# `ods enable langfuse`. --no-langfuse honored as explicit override so a
# --all run can still suppress Langfuse.
ENABLE_LANGFUSE=false
NO_LANGFUSE_EXPLICIT=false
OPENCLAW_EXPLICIT=false
ALL_FEATURES=false
CLOUD_MODE=false
NO_BOOTSTRAP=false
HERMES_CONTEXT_SIZE=65536

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)       DRY_RUN=true; shift ;;
        --force)         FORCE=true; shift ;;
        --non-interactive) NON_INTERACTIVE=true; shift ;;
        --tier)          TIER_OVERRIDE="${2:-}"; shift 2 ;;
        --voice)         ENABLE_VOICE=true; shift ;;
        --workflows)     ENABLE_WORKFLOWS=true; shift ;;
        --rag)           ENABLE_RAG=true; shift ;;
        --recommended)   ENABLE_RECOMMENDED=true; shift ;;
        --no-recommended) ENABLE_RECOMMENDED=false; shift ;;
        --hermes)        ENABLE_HERMES=true; shift ;;
        --no-hermes)     ENABLE_HERMES=false; shift ;;
        --openclaw)      ENABLE_OPENCLAW=true; OPENCLAW_EXPLICIT=true; shift ;;
        --no-openclaw)   ENABLE_OPENCLAW=false; OPENCLAW_EXPLICIT=true; shift ;;
        --langfuse)      ENABLE_LANGFUSE=true; shift ;;
        --no-langfuse)   ENABLE_LANGFUSE=false; NO_LANGFUSE_EXPLICIT=true; shift ;;
        --all)           ALL_FEATURES=true; shift ;;
        --cloud)         CLOUD_MODE=true; shift ;;
        --no-bootstrap)  NO_BOOTSTRAP=true; shift ;;
        *)               echo "Unknown option: $1"; exit 1 ;;
    esac
done

if $ALL_FEATURES; then
    ENABLE_VOICE=true
    ENABLE_WORKFLOWS=true
    ENABLE_RAG=true
    ENABLE_RECOMMENDED=true
    # --all enables the new default Hermes Agent. OpenClaw stays opt-in via
    # --openclaw during the deprecation release; will be removed entirely
    # in the next release.
    ENABLE_HERMES=true
    $OPENCLAW_EXPLICIT || ENABLE_OPENCLAW=false
    ENABLE_APE=true
    ENABLE_PERPLEXICA=true
    ENABLE_PRIVACY_SHIELD=true
    ENABLE_ODS_PROXY=true
    # --all enables Langfuse unless the user explicitly passed --no-langfuse.
    $NO_LANGFUSE_EXPLICIT || ENABLE_LANGFUSE=true
fi

# ── Locate script directory and source tree root ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ── Source libraries ──
LIB_DIR="${SCRIPT_DIR}/lib"
source "${LIB_DIR}/constants.sh"
source "${LIB_DIR}/ui.sh"
source "${LIB_DIR}/bridge-manager.sh"
source "${LIB_DIR}/tier-map.sh"
source "${LIB_DIR}/detection.sh"
source "${LIB_DIR}/preflight-fs.sh"
source "${LIB_DIR}/env-generator.sh"
if [[ -f "${SOURCE_ROOT}/installers/lib/compose-failure-report.sh" ]]; then
    source "${SOURCE_ROOT}/installers/lib/compose-failure-report.sh"
fi
source "${SOURCE_ROOT}/lib/safe-env.sh"
if [[ -f "${SOURCE_ROOT}/lib/python-cmd.sh" ]]; then
    source "${SOURCE_ROOT}/lib/python-cmd.sh"
fi
source "${SOURCE_ROOT}/installers/lib/readiness-summary.sh"

# ── File-local helpers ──
_close_inherited_fds_for_daemon() {
    local fd fd_dir fd_name

    for fd_dir in "/proc/${BASHPID:-$$}/fd" "/dev/fd"; do
        [[ -d "$fd_dir" ]] || continue
        for fd in "$fd_dir"/*; do
            fd_name="${fd##*/}"
            [[ "$fd_name" =~ ^[0-9]+$ ]] || continue
            (( fd_name <= 2 || fd_name == 255 )) && continue
            eval "exec ${fd_name}>&-" 2>/dev/null || true
        done
        return 0
    done

    for ((fd_name = 3; fd_name <= 254; fd_name++)); do
        eval "exec ${fd_name}>&-" 2>/dev/null || true
    done
}

# Build a launchd-friendly PATH that includes Docker and Homebrew prefixes.
# launchd does NOT inherit the user's login shell PATH, so any path containing
# `docker` or `brew`-installed tools must be baked into the plist explicitly.
# Pass an optional leading directory (e.g. ~/.opencode/bin) as $1.
_compute_launchd_path() {
    local extra="${1:-}"
    local docker_bin="" docker_dir="" brew_prefix=""
    if command -v docker >/dev/null 2>&1; then
        docker_bin="$(command -v docker)"
        docker_dir="$(cd "$(dirname "$docker_bin")" && pwd)"
    fi
    if command -v brew >/dev/null 2>&1; then
        brew_prefix="$(brew --prefix)"
    fi
    local entries=()
    [[ -n "$extra" ]]                && entries+=("$extra")
    [[ -n "$docker_dir" ]]           && entries+=("$docker_dir")
    [[ -n "$brew_prefix" ]]          && entries+=("${brew_prefix}/bin")
    entries+=("/opt/homebrew/bin" "/usr/local/bin" "/usr/bin" "/bin")
    local seen=":" path_out="" d
    for d in "${entries[@]}"; do
        case "$seen" in
            *":${d}:"*) ;;
            *) seen="${seen}${d}:"; path_out="${path_out:+${path_out}:}${d}" ;;
        esac
    done
    printf '%s' "$path_out"
}

_write_macos_cloud_auth_overlay() {
    local overlay_path="$1"

    mkdir -p "$(dirname "$overlay_path")"
    cat > "$overlay_path" <<'CLOUD_AUTH_EOF'
# Generated by install-macos.sh. The secret stays in .env and is resolved by
# Docker Compose, so this file is safe to inspect and persist in .compose-flags.
# Only always-present services belong here. Optional services read their keys
# from .env so compose.yaml <-> compose.yaml.disabled toggles remain valid.
services:
  open-webui:
    environment:
      OPENAI_API_KEY: "${LITELLM_KEY:?LITELLM_KEY must be set}"
CLOUD_AUTH_EOF
}

_macos_set_builtin_compose_state() {
    local service_id="$1" enabled="$2"
    local service_dir="${INSTALL_DIR}/extensions/services/${service_id}"
    local active="${service_dir}/compose.yaml"
    local disabled="${service_dir}/compose.yaml.disabled"

    [[ -d "$service_dir" ]] || return 0
    if [[ "$enabled" == "true" ]]; then
        if [[ -f "$active" ]]; then
            rm -f "$disabled"
        elif [[ -f "$disabled" ]]; then
            mv "$disabled" "$active"
            ai_ok "Enabled built-in extension: ${service_id}"
        fi
    elif [[ -f "$active" ]]; then
        mv -f "$active" "$disabled"
        log "Disabled built-in extension compose: ${service_id}"
    fi
}

_macos_sync_builtin_compose_states() {
    _macos_set_builtin_compose_state litellm "$ENABLE_RECOMMENDED"
    _macos_set_builtin_compose_state searxng "$ENABLE_RECOMMENDED"
    _macos_set_builtin_compose_state token-spy "$ENABLE_RECOMMENDED"
    _macos_set_builtin_compose_state whisper "$ENABLE_VOICE"
    _macos_set_builtin_compose_state tts "$ENABLE_VOICE"
    _macos_set_builtin_compose_state n8n "$ENABLE_WORKFLOWS"
    _macos_set_builtin_compose_state qdrant "$ENABLE_RAG"
    _macos_set_builtin_compose_state embeddings "$ENABLE_RAG"
    _macos_set_builtin_compose_state hermes "$ENABLE_HERMES"
    _macos_set_builtin_compose_state hermes-proxy "$ENABLE_HERMES"
    _macos_set_builtin_compose_state openclaw "$ENABLE_OPENCLAW"
    _macos_set_builtin_compose_state ape "$ENABLE_APE"
    _macos_set_builtin_compose_state perplexica "$ENABLE_PERPLEXICA"
    _macos_set_builtin_compose_state privacy-shield "$ENABLE_PRIVACY_SHIELD"
    _macos_set_builtin_compose_state ods-proxy "$ENABLE_ODS_PROXY"
    _macos_set_builtin_compose_state tailscale "$ENABLE_TAILSCALE"
    _macos_set_builtin_compose_state langfuse "$ENABLE_LANGFUSE"
    _macos_set_builtin_compose_state brave-search "${ENABLE_BRAVE_SEARCH:-false}"
}

_macos_patch_hermes_persisted_config() {
    local model_name="$1" base_url="$2" context_length="$3"
    local state hermes_image helper_image project_image selected_image persisted_config candidate
    local -a patch_command=()

    persisted_config="${INSTALL_DIR}/data/hermes/config.yaml"
    state="$(docker inspect --format '{{.State.Status}}' ods-hermes 2>/dev/null || true)"
    if [[ "$state" == "running" ]] \
       && docker exec --user 0:0 ods-hermes python3 -c 'import yaml' >/dev/null 2>&1; then
        patch_command=(docker exec --user 0:0 -i ods-hermes python3 - "$model_name" "$base_url" "$context_length")
    fi

    if (( ${#patch_command[@]} == 0 )); then
        helper_image="$(docker inspect --format '{{.Config.Image}}' ods-dashboard-api 2>/dev/null || true)"
        project_image="$(basename "$INSTALL_DIR" | tr '[:upper:]' '[:lower:]')-dashboard-api:latest"
        hermes_image="$(docker inspect --format '{{.Config.Image}}' ods-hermes 2>/dev/null || true)"
        [[ -n "$hermes_image" ]] || hermes_image="$(read_env_value "${INSTALL_DIR}/.env" "HERMES_AGENT_IMAGE")"
        [[ -n "$hermes_image" ]] || hermes_image="nousresearch/hermes-agent:v2026.5.16"

        # The Hermes runtime image is not guaranteed to include PyYAML. Probe
        # candidates instead of treating a cached image as a usable migrator.
        # dashboard-api is preferred because its application imports yaml.
        selected_image=""
        for candidate in "$helper_image" "$project_image" \
            "ods-dashboard-api:latest" "$hermes_image"; do
            [[ -n "$candidate" ]] || continue
            docker image inspect "$candidate" >/dev/null 2>&1 || continue
            if docker run --rm --pull never --network none --user 0:0 \
                --entrypoint python3 "$candidate" -c 'import yaml' >/dev/null 2>&1; then
                selected_image="$candidate"
                break
            fi
        done

        if [[ -z "$selected_image" ]]; then
            if [[ ! -e "$(dirname "$persisted_config")" ]] \
               || { [[ -x "$(dirname "$persisted_config")" ]] && [[ ! -e "$persisted_config" ]]; }; then
                return 3
            fi
            return 4
        fi
        patch_command=(
            docker run --rm --pull never --network none --user 0:0 -i
            -v "${INSTALL_DIR}/data/hermes:/opt/data"
            --entrypoint python3 "$selected_image" - "$model_name" "$base_url" "$context_length"
        )
    fi

    "${patch_command[@]}" <<'HERMES_LIVE_PATCH_PY'
import os
import sys
from pathlib import Path

import yaml

path = Path("/opt/data/config.yaml")
if not path.is_file():
    raise SystemExit(3)

model_name, base_url, context_text = sys.argv[1:4]
context_length = int(context_text)
data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
if not isinstance(data, dict):
    raise SystemExit("Hermes config root is not a mapping")
model = data.setdefault("model", {})
if not isinstance(model, dict):
    raise SystemExit("Hermes model config is not a mapping")
model["default"] = model_name
model["base_url"] = base_url
model["context_length"] = context_length
# OPENAI_API_KEY from compose is authoritative. Removing a persisted key keeps
# local/cloud transitions and later extension toggles in sync with .env.
model.pop("api_key", None)
auxiliary = data.setdefault("auxiliary", {})
if not isinstance(auxiliary, dict):
    raise SystemExit("Hermes auxiliary config is not a mapping")
compression = auxiliary.setdefault("compression", {})
if not isinstance(compression, dict):
    raise SystemExit("Hermes auxiliary compression config is not a mapping")
compression["context_length"] = context_length

st = path.stat()
tmp = path.with_name(f"{path.name}.ods-transition-{os.getpid()}.tmp")
with tmp.open("w", encoding="utf-8") as handle:
    yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=True)
    handle.flush()
    os.fsync(handle.fileno())
os.chmod(tmp, st.st_mode & 0o777)
try:
    os.chown(tmp, st.st_uid, st.st_gid)
except PermissionError:
    tmp_st = tmp.stat()
    if (tmp_st.st_uid, tmp_st.st_gid) != (st.st_uid, st.st_gid):
        tmp.unlink(missing_ok=True)
        raise
os.replace(tmp, path)

check = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
check_model = check.get("model") or {}
if check_model.get("default") != model_name:
    raise SystemExit("Hermes model verification failed")
if check_model.get("base_url") != base_url:
    raise SystemExit("Hermes base_url verification failed")
if int(check_model.get("context_length") or 0) != context_length:
    raise SystemExit("Hermes context verification failed")
if "api_key" in check_model:
    raise SystemExit("Hermes persisted api_key was not removed")
check_compression = (check.get("auxiliary") or {}).get("compression") or {}
if int(check_compression.get("context_length") or 0) != context_length:
    raise SystemExit("Hermes compression context verification failed")
HERMES_LIVE_PATCH_PY
}

_macos_verify_hermes_container_auth() {
    local expected_key="$1" expected_hash
    [[ -n "$expected_key" ]] || return 1
    expected_hash="$(printf '%s' "$expected_key" | shasum -a 256 | awk '{print $1}')"
    docker exec -i ods-hermes python3 - "$expected_hash" <<'HERMES_AUTH_VERIFY_PY'
import hashlib
import os
import sys

actual = os.environ.get("OPENAI_API_KEY", "")
expected_hash = sys.argv[1]
raise SystemExit(0 if actual and hashlib.sha256(actual.encode()).hexdigest() == expected_hash else 1)
HERMES_AUTH_VERIFY_PY
}

_write_macos_opencode_config() {
    local config_path="$1" model_name="$2" base_url="$3" api_key="$4" context_length="$5"
    mkdir -p "$(dirname "$config_path")"
    ODS_OPENCODE_MODEL="$model_name" \
    ODS_OPENCODE_BASE_URL="$base_url" \
    ODS_OPENCODE_API_KEY="$api_key" \
    ODS_OPENCODE_CONTEXT="$context_length" \
        /usr/bin/python3 - "$config_path" <<'OPENCODE_CONFIG_PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
except (OSError, ValueError):
    data = {}
if not isinstance(data, dict):
    data = {}

model_name = os.environ["ODS_OPENCODE_MODEL"]
base_url = os.environ["ODS_OPENCODE_BASE_URL"]
api_key = os.environ["ODS_OPENCODE_API_KEY"]
context = int(os.environ["ODS_OPENCODE_CONTEXT"])
provider_id = "llama-server"
provider = data.setdefault("provider", {}).setdefault(provider_id, {})
provider.update({
    "npm": "@ai-sdk/openai-compatible",
    "name": "ODS inference",
    "options": {"baseURL": base_url, "apiKey": api_key},
    "models": {
        model_name: {
            "name": model_name,
            "limit": {"context": context, "output": min(32768, context)},
        }
    },
})
data["model"] = f"{provider_id}/{model_name}"
data.setdefault("$schema", "https://opencode.ai/config.json")

tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
os.chmod(tmp, 0o600)
os.replace(tmp, path)

check = json.loads(path.read_text(encoding="utf-8"))
check_provider = check["provider"][provider_id]
if check.get("model") != f"{provider_id}/{model_name}":
    raise SystemExit("OpenCode model verification failed")
if check_provider["options"] != {"baseURL": base_url, "apiKey": api_key}:
    raise SystemExit("OpenCode route verification failed")
OPENCODE_CONFIG_PY
}

_macos_bootstrap_upgrade_pid_is_owned() {
    local pid="$1"
    local upgrade_script="${INSTALL_DIR}/scripts/bootstrap-upgrade.sh"
    local command_line process_name

    [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
    [[ "$pid" -ne "$$" ]] || return 1
    process_name="$(ps -ww -p "$pid" -o comm= 2>/dev/null || true)"
    command_line="$(ps -ww -p "$pid" -o command= 2>/dev/null || true)"
    [[ "${process_name##*/}" == "bash" ]] || return 1
    case "$command_line" in
        "bash ${upgrade_script} ${INSTALL_DIR} "*|*/bash" ${upgrade_script} ${INSTALL_DIR} "*) return 0 ;;
    esac
    return 1
}

_macos_collect_process_descendants() {
    local parent_pid="$1" child_pid
    while IFS= read -r child_pid; do
        [[ "$child_pid" =~ ^[1-9][0-9]*$ ]] || continue
        _macos_collect_process_descendants "$child_pid"
        printf '%s\n' "$child_pid"
    done < <(pgrep -P "$parent_pid" 2>/dev/null || true)
}

_macos_cancel_detached_bootstrap_upgrade() {
    local cancel_reason="${1:-cloud_mode}"
    local action_label="${2:-cloud transition}"
    local status_file="${INSTALL_DIR}/data/bootstrap-status.json"
    local args_file="${INSTALL_DIR}/data/bootstrap-upgrade.args"
    local pid_file="${INSTALL_DIR}/data/bootstrap-upgrade.pid"
    local status="" pid pgid child attempt alive should_mark_cancelled=false
    local -a pids=() groups=() legacy_tree=()

    if ! command -v pgrep >/dev/null 2>&1; then
        ai_err "Cannot safely inspect detached model upgrades because pgrep is unavailable."
        return 1
    fi

    if [[ -f "$pid_file" ]]; then
        pid="$(tr -dc '0-9' < "$pid_file" 2>/dev/null || true)"
        if _macos_bootstrap_upgrade_pid_is_owned "$pid"; then
            pids+=("$pid")
        fi
    fi
    while IFS= read -r pid; do
        [[ -n "$pid" ]] || continue
        if _macos_bootstrap_upgrade_pid_is_owned "$pid"; then
            case " ${pids[*]} " in
                *" $pid "*) ;;
                *) pids+=("$pid") ;;
            esac
        fi
    done < <(pgrep -f '[/]bootstrap-upgrade[.]sh' 2>/dev/null || true)

    if [[ -f "$status_file" ]]; then
        status="$(grep -o '"status"[[:space:]]*:[[:space:]]*"[^"]*"' "$status_file" 2>/dev/null \
            | sed -n '1p' | cut -d'"' -f4 || true)"
        case "$status" in
            starting|downloading|verifying|swapping|failed) should_mark_cancelled=true ;;
        esac
    fi
    [[ -f "$args_file" ]] && should_mark_cancelled=true
    [[ -f "$pid_file" ]] && should_mark_cancelled=true
    [[ "${#pids[@]}" -gt 0 ]] && should_mark_cancelled=true

    if [[ "${#pids[@]}" -gt 0 ]]; then
        ai "Stopping ${#pids[@]} install-owned background model upgrade process(es) before ${action_label}..."
        for pid in "${pids[@]}"; do
            pgid="$(ps -ww -p "$pid" -o pgid= 2>/dev/null | tr -d '[:space:]' || true)"
            if [[ "$pgid" == "$pid" ]]; then
                groups+=("$pgid")
            else
                while IFS= read -r child; do
                    [[ "$child" =~ ^[1-9][0-9]*$ ]] && legacy_tree+=("$child")
                done < <(_macos_collect_process_descendants "$pid")
                legacy_tree+=("$pid")
            fi
        done

        # New installers launch each worker in its own session/process group,
        # so TERM reaches curl, the progress monitor, and any hot-swap child.
        # The legacy PID-tree fallback safely retires workers from older builds.
        for pgid in "${groups[@]}"; do
            kill -TERM -- "-${pgid}" 2>/dev/null || true
        done
        for pid in "${legacy_tree[@]}"; do
            kill -TERM "$pid" 2>/dev/null || true
        done

        for ((attempt=1; attempt<=40; attempt++)); do
            alive=false
            for pgid in "${groups[@]}"; do
                kill -0 -- "-${pgid}" 2>/dev/null && alive=true
            done
            for pid in "${legacy_tree[@]}"; do
                kill -0 "$pid" 2>/dev/null && alive=true
            done
            ! $alive && break
            sleep 0.25
        done

        if $alive; then
            ai_warn "Background model upgrade tree did not stop after SIGTERM; forcing remaining process-group members."
            for pgid in "${groups[@]}"; do
                kill -KILL -- "-${pgid}" 2>/dev/null || true
            done
            for pid in "${legacy_tree[@]}"; do
                kill -KILL "$pid" 2>/dev/null || true
            done
            for ((attempt=1; attempt<=20; attempt++)); do
                alive=false
                for pgid in "${groups[@]}"; do
                    kill -0 -- "-${pgid}" 2>/dev/null && alive=true
                done
                for pid in "${legacy_tree[@]}"; do
                    kill -0 "$pid" 2>/dev/null && alive=true
                done
                ! $alive && break
                sleep 0.1
            done
        fi

        if $alive; then
            ai_err "Could not stop the complete install-owned background upgrade tree; refusing to continue ${action_label}."
            return 1
        fi
    fi

    rm -f "$args_file" "$pid_file" 2>/dev/null || {
        ai_err "Could not disable bootstrap-upgrade process/retry metadata."
        return 1
    }
    if [[ "$should_mark_cancelled" == "true" ]]; then
        local status_tmp="${status_file}.${cancel_reason}.$$"
        local cancelled_at
        cancelled_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        mkdir -p "$(dirname "$status_file")"
        if ! printf '{"status":"cancelled","reason":"%s","updatedAt":"%s"}\n' "$cancel_reason" "$cancelled_at" > "$status_tmp" \
           || ! mv "$status_tmp" "$status_file"; then
            rm -f "$status_tmp" 2>/dev/null || true
            ai_err "Could not mark the background model upgrade cancelled for ${action_label}."
            return 1
        fi
        ai_ok "Background model upgrade disabled for ${action_label}"
    fi
}

_macos_launch_detached_bootstrap_upgrade() {
    local upgrade_script="$1"
    shift
    local pid_file="${INSTALL_DIR}/data/bootstrap-upgrade.pid"
    local log_file="${INSTALL_DIR}/logs/model-upgrade.log"
    local python_cmd="${PYTHON_CMD:-/usr/bin/python3}"
    local bash_cmd="${BASH:-bash}"
    [[ -x "$python_cmd" ]] || python_cmd="/usr/bin/python3"
    if command -v cygpath >/dev/null 2>&1; then
        bash_cmd="$(cygpath -w "$bash_cmd" 2>/dev/null || printf '%s' "$bash_cmd")"
    fi

    BOOTSTRAP_BASH="$bash_cmd" "$python_cmd" - "$pid_file" "$log_file" "$upgrade_script" "$@" <<'BOOTSTRAP_LAUNCH_PY'
import os
import subprocess
import sys
from pathlib import Path

pid_path = Path(sys.argv[1])
log_path = Path(sys.argv[2])
script = sys.argv[3]
script_args = sys.argv[4:]
if not script_args:
    raise SystemExit("bootstrap launcher requires the install directory")
bash_exe = os.environ.get("BOOTSTRAP_BASH") or os.environ.get("BASH") or "bash"
log_path.parent.mkdir(parents=True, exist_ok=True)
pid_path.parent.mkdir(parents=True, exist_ok=True)
with log_path.open("ab", buffering=0) as log_handle:
    proc = subprocess.Popen(
        [bash_exe, script, *script_args],
        cwd=script_args[0],
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        close_fds=True,
        start_new_session=True,
    )
tmp = pid_path.with_name(f"{pid_path.name}.{os.getpid()}.tmp")
tmp.write_text(f"{proc.pid}\n", encoding="ascii")
os.chmod(tmp, 0o600)
os.replace(tmp, pid_path)
BOOTSTRAP_LAUNCH_PY
}

_macos_persist_bootstrap_upgrade_args() {
    local full_gguf_file="$1"
    local full_gguf_url="$2"
    local full_gguf_sha256="$3"
    local full_llm_model="$4"
    local full_max_context="$5"
    local bootstrap_gguf_file="${6:-Qwen3.5-2B-Q4_K_M.gguf}"
    local args_file="${INSTALL_DIR}/data/bootstrap-upgrade.args"

    mkdir -p "${INSTALL_DIR}/data"
    {
        printf '%s\n' "$full_gguf_file"
        printf '%s\n' "$full_gguf_url"
        printf '%s\n' "$full_gguf_sha256"
        printf '%s\n' "$full_llm_model"
        printf '%s\n' "$full_max_context"
        printf '%s\n' "$bootstrap_gguf_file"
    } > "${args_file}.tmp" && mv "${args_file}.tmp" "$args_file" || {
        rm -f "${args_file}.tmp" 2>/dev/null || true
        ai_warn "Could not persist bootstrap-upgrade retry metadata"
        return 1
    }
    chmod 600 "$args_file" 2>/dev/null || true
}

_macos_native_llama_cwd_is_owned() {
    local process_cwd="$1" home_dir="${HOME:-}"
    [[ -n "$process_cwd" ]] || return 1
    [[ "$process_cwd" == "$INSTALL_DIR" ]] && return 0
    return 1
}

_macos_native_llama_pid_is_owned() {
    local pid="$1" command_line process_name process_cwd
    [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
    process_name="$(ps -ww -p "$pid" -o comm= 2>/dev/null || true)"
    command_line="$(ps -ww -p "$pid" -o command= 2>/dev/null || true)"
    [[ "${process_name##*/}" == "llama-server" ]] || return 1
    if [[ -n "${LLAMA_SERVER_BIN:-}" && "$command_line" == *"$LLAMA_SERVER_BIN"* ]]; then
        return 0
    fi
    case "$command_line" in
        ./bin/llama-server*|bin/llama-server*)
            process_cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
            _macos_native_llama_cwd_is_owned "$process_cwd"
            ;;
        *) return 1 ;;
    esac
}

_macos_stop_install_owned_native_llama() {
    local reason="${1:-Stopping install-owned native llama-server}" pid attempt
    local -a candidates=() remaining=()
    if [[ -f "$LLAMA_SERVER_PID_FILE" ]]; then
        pid="$(tr -dc '0-9' < "$LLAMA_SERVER_PID_FILE" 2>/dev/null || true)"
        _macos_native_llama_pid_is_owned "$pid" && candidates+=("$pid")
    fi
    if command -v pgrep >/dev/null 2>&1; then
        while IFS= read -r pid; do
            _macos_native_llama_pid_is_owned "$pid" || continue
            case " ${candidates[*]} " in
                *" $pid "*) ;;
                *) candidates+=("$pid") ;;
            esac
        done < <(pgrep -f '[/]llama-server' 2>/dev/null || true)
    fi
    [[ "${#candidates[@]}" -gt 0 ]] || { rm -f "$LLAMA_SERVER_PID_FILE" 2>/dev/null || true; return 0; }

    ai "$reason"
    for pid in "${candidates[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done
    for ((attempt=1; attempt<=20; attempt++)); do
        remaining=()
        for pid in "${candidates[@]}"; do
            kill -0 "$pid" 2>/dev/null && remaining+=("$pid")
        done
        candidates=("${remaining[@]}")
        [[ "${#candidates[@]}" -eq 0 ]] && break
        sleep 0.25
    done
    for pid in "${candidates[@]}"; do kill -KILL "$pid" 2>/dev/null || true; done
    for ((attempt=1; attempt<=20; attempt++)); do
        remaining=()
        for pid in "${candidates[@]}"; do
            kill -0 "$pid" 2>/dev/null && remaining+=("$pid")
        done
        candidates=("${remaining[@]}")
        [[ "${#candidates[@]}" -eq 0 ]] && break
        sleep 0.1
    done
    if [[ "${#candidates[@]}" -gt 0 ]]; then
        ai_err "Could not stop the complete install-owned native llama-server process set."
        return 1
    fi
    rm -f "$LLAMA_SERVER_PID_FILE" 2>/dev/null || true
}

_verify_macos_dashboard_host_agent() {
    local env_file="$1"
    local container_state bridge_enabled host port api_key attempt

    container_state="$(docker inspect --format '{{.State.Status}}' ods-dashboard-api 2>/dev/null || true)"
    if [[ "$container_state" != "running" ]]; then
        ai_err "Dashboard API container is not running (state: ${container_state:-missing})."
        ai "  Inspect: docker logs ods-dashboard-api"
        return 1
    fi

    bridge_enabled="$(read_env_value "$env_file" "ODS_MACOS_HOST_AGENT_BRIDGE_ENABLED")"
    host="$(read_env_value "$env_file" "ODS_AGENT_HOST")"
    port="$(read_env_value "$env_file" "ODS_AGENT_PORT")"
    api_key="$(read_env_value "$env_file" "ODS_AGENT_KEY")"
    [[ -n "$host" ]] || host="host.docker.internal"
    [[ "$port" =~ ^[0-9]+$ ]] || port="7710"
    if [[ -z "$api_key" ]]; then
        ai_err "Cannot verify the dashboard host-agent path because ODS_AGENT_KEY is empty."
        return 1
    fi

    for attempt in $(seq 1 20); do
        if docker exec ods-dashboard-api curl -fsS --max-time 2 \
            -H "Authorization: Bearer ${api_key}" \
            "http://${host}:${port}/v1/model/status" >/dev/null 2>&1; then
            ai_ok "Dashboard container reached the authenticated host agent"
            return 0
        fi
        sleep 1
    done

    ai_err "Dashboard container cannot reach the authenticated host agent at ${host}:${port}."
    ai "  Host log:   $HOME/Library/Logs/ODS/ods-host-agent.log"
    [[ "$bridge_enabled" == "true" ]] && ai "  Bridge log: $HOST_AGENT_BRIDGE_LOG"
    return 1
}

COLIMA_VM_IP=""
COLIMA_HOST_IP=""
COLIMA_PRIVATE_ROUTE_PREFERRED=false

_detect_colima_private_network() {
    local status_json interface_name colima_config preferred_route
    COLIMA_VM_IP=""
    COLIMA_HOST_IP=""
    COLIMA_PRIVATE_ROUTE_PREFERRED=false

    status_json="$(colima status --json 2>>"$ODS_LOG_FILE" || true)"
    [[ -n "$status_json" ]] || return 1
    COLIMA_VM_IP="$(printf '%s' "$status_json" | /usr/bin/python3 -c '
import json, sys
try:
    value = json.load(sys.stdin).get("ip_address", "")
except (AttributeError, json.JSONDecodeError):
    value = ""
print(value if isinstance(value, str) else "")
' 2>/dev/null || true)"
    [[ -n "$COLIMA_VM_IP" ]] || return 1

    interface_name="$(/sbin/route -n get "$COLIMA_VM_IP" 2>/dev/null | awk '/interface:/{print $2; exit}')"
    [[ -n "$interface_name" ]] || return 1
    COLIMA_HOST_IP="$(/sbin/ifconfig "$interface_name" 2>/dev/null | awk '/inet / && $2 != "127.0.0.1" {print $2; exit}')"
    [[ -n "$COLIMA_HOST_IP" ]] || return 1

    COLIMA_VM_IP="$COLIMA_VM_IP" COLIMA_HOST_IP="$COLIMA_HOST_IP" /usr/bin/python3 -c '
import ipaddress, os
vm = ipaddress.ip_address(os.environ["COLIMA_VM_IP"])
host = ipaddress.ip_address(os.environ["COLIMA_HOST_IP"])
network = ipaddress.ip_network(f"{vm}/24", strict=False)
valid = vm.version == 4 and host.version == 4 and host != vm and host in network and host.is_private
raise SystemExit(0 if valid else 1)
' >/dev/null 2>&1 || return 1

    colima_config="${COLIMA_HOME:-$HOME/.colima}/default/colima.yaml"
    preferred_route="$(awk '
        /^network:/ { in_network=1; next }
        in_network && /^[^[:space:]]/ { in_network=0 }
        in_network && /^[[:space:]]+preferredRoute:/ { print $2; exit }
    ' "$colima_config" 2>/dev/null || true)"
    [[ "$preferred_route" == "true" ]] && COLIMA_PRIVATE_ROUTE_PREFERRED=true
    return 0
}

_ensure_colima_private_network() {
    [[ "${DOCKER_BACKEND:-unknown}" == "colima" ]] || return 0
    if ! command -v colima >/dev/null 2>&1; then
        ai_err "Docker is using Colima, but the colima CLI is not on PATH."
        return 1
    fi

    if _detect_colima_private_network && [[ "$COLIMA_PRIVATE_ROUTE_PREFERRED" == "true" ]]; then
        ai_ok "Colima private host bridge ready (${COLIMA_HOST_IP} <-> ${COLIMA_VM_IP})"
        return 0
    fi

    if $DRY_RUN; then
        ai "[DRY RUN] Would restart Colima with private vmnet as the preferred route"
        return 0
    fi

    ai_warn "Colima needs a preferred private VM route; restarting its VM to configure one."
    ai_warn "Running non-ODS containers will restart with the Colima VM. Container data is preserved."
    if ! colima stop >>"$ODS_LOG_FILE" 2>&1 \
       || ! colima start --network-address --network-preferred-route >>"$ODS_LOG_FILE" 2>&1; then
        ai_err "Could not enable Colima private networking."
        ai "  Run: colima stop && colima start --network-address --network-preferred-route"
        return 1
    fi

    local attempt
    for attempt in $(seq 1 60); do
        docker info >/dev/null 2>&1 && break
        sleep 1
    done
    if ! docker info >/dev/null 2>&1 \
       || ! _detect_colima_private_network \
       || [[ "$COLIMA_PRIVATE_ROUTE_PREFERRED" != "true" ]]; then
        ai_err "Colima restarted, but its preferred private host route could not be verified."
        return 1
    fi
    ai_ok "Colima private host bridge enabled (${COLIMA_HOST_IP} <-> ${COLIMA_VM_IP})"
}

_configure_macos_llm_bridge() {
    macos_configure_llm_bridge_from_env "${INSTALL_DIR}/.env" "$INSTALL_DIR"
}

_configure_macos_host_agent_bridge() {
    local env_file="${INSTALL_DIR}/.env"
    local enabled listen_host allowed_peer agent_port agent_bind
    enabled="$(read_env_value "$env_file" "ODS_MACOS_HOST_AGENT_BRIDGE_ENABLED")"
    listen_host="$(read_env_value "$env_file" "ODS_MACOS_HOST_GATEWAY")"
    allowed_peer="$(read_env_value "$env_file" "ODS_MACOS_VM_IP")"
    agent_port="$(read_env_value "$env_file" "ODS_AGENT_PORT")"
    agent_bind="$(read_env_value "$env_file" "ODS_AGENT_BIND")"
    [[ -n "$agent_bind" ]] || agent_bind="127.0.0.1"
    if [[ "$enabled" == "true" ]] && macos_bind_uses_direct_gateway "$agent_bind" "$listen_host"; then
        ai "Host-agent bind ${agent_bind} already covers the Colima gateway; disabling the host-agent bridge"
        enabled="false"
        upsert_env_value "$env_file" "ODS_MACOS_HOST_AGENT_BRIDGE_ENABLED" "false"
    fi
    [[ "$agent_port" =~ ^[0-9]+$ ]] || agent_port="7710"
    macos_configure_port_bridge "$enabled" "$HOST_AGENT_BRIDGE_PLIST_LABEL" \
        "$HOST_AGENT_BRIDGE_PLIST" "$HOST_AGENT_BRIDGE_LOG" "Colima host-agent bridge" \
        "$listen_host" "$agent_port" "$agent_port" "$allowed_peer" "$INSTALL_DIR"
}

_opencode_candidate_is_file() {
    local candidate="$1"
    [[ -n "$candidate" && "$candidate" == /* && -x "$candidate" && ! -d "$candidate" ]]
}

_find_opencode_bin() {
    local candidate="" brew_prefix=""
    for candidate in "${OPENCODE_BIN:-}" "$HOME/.opencode/bin/opencode"; do
        if _opencode_candidate_is_file "$candidate"; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    if command -v brew >/dev/null 2>&1; then
        brew_prefix="$(brew --prefix 2>/dev/null || true)"
        candidate="${brew_prefix:+${brew_prefix}/bin/opencode}"
        if _opencode_candidate_is_file "$candidate"; then
            printf '%s\n' "$candidate"
            return 0
        fi
    fi

    candidate="$(type -P opencode 2>/dev/null || true)"
    if _opencode_candidate_is_file "$candidate"; then
        printf '%s\n' "$candidate"
        return 0
    fi

    return 1
}

_install_opencode() {
    OPENCODE_BIN="$(_find_opencode_bin 2>/dev/null || true)"
    if [[ -n "$OPENCODE_BIN" ]]; then
        ai_ok "OpenCode already installed ($OPENCODE_BIN)"
        return 0
    fi

    if command -v brew >/dev/null 2>&1; then
        ai "Installing OpenCode with Homebrew..."
        if brew install opencode >> "$ODS_LOG_FILE" 2>&1; then
            OPENCODE_BIN="$(_find_opencode_bin 2>/dev/null || true)"
            if [[ -n "$OPENCODE_BIN" ]]; then
                ai_ok "OpenCode installed with Homebrew ($OPENCODE_BIN)"
                return 0
            fi
            ai_warn "Homebrew reported success but opencode was not found on PATH"
        else
            ai_warn "Homebrew OpenCode install failed — falling back to upstream installer"
        fi
    fi

    ai "Installing OpenCode with upstream installer..."
    local tmpfile
    tmpfile=$(mktemp /tmp/opencode-install.XXXXXX.sh)
    if curl -fsSL --max-time 300 https://opencode.ai/install -o "$tmpfile" 2>/dev/null \
       && bash "$tmpfile" >> "$ODS_LOG_FILE" 2>&1; then
        OPENCODE_BIN="$(_find_opencode_bin 2>/dev/null || true)"
        if [[ -n "$OPENCODE_BIN" ]]; then
            ai_ok "OpenCode installed ($OPENCODE_BIN)"
        else
            ai_warn "OpenCode installer completed but opencode was not found"
        fi
    else
        ai_warn "OpenCode install failed — install later with: brew install opencode"
    fi
    rm -f "$tmpfile"
}

_require_docker_cpu_budget() {
    local min_cpus="${1:-6}"
    local max_pin="${2:-4}"
    local workload="${3:-compose stack}"
    local docker_ncpu

    if ! [[ "$min_cpus" =~ ^[0-9]+$ ]] || [[ "$min_cpus" -lt 1 ]]; then
        min_cpus=6
    fi

    docker_ncpu=$(get_docker_available_cpus)
    if [[ "$docker_ncpu" =~ ^[0-9]+$ ]] && [[ "$docker_ncpu" -lt "$min_cpus" ]]; then
        ai_err "Docker daemon only has ${docker_ncpu} CPU(s); ODS's ${workload} pins limits up to ${max_pin} CPUs per service and needs at least ${min_cpus} to avoid 'range of CPUs is from 0.01 to N' compose failures."
        case "${DOCKER_BACKEND:-unknown}" in
            colima)
                ai "Stop and re-create the Colima VM with more CPUs:"
                ai "    colima stop && colima start --cpu ${min_cpus} --memory 12 --disk 60"
                ai "Then re-run this installer."
                ;;
            desktop)
                ai "Open Docker Desktop -> Settings -> Resources -> Advanced and raise CPUs to ${min_cpus}+, apply, then re-run."
                ;;
            rancher)
                ai "Open Rancher Desktop -> Preferences -> Virtual Machine -> Hardware and raise CPUs to ${min_cpus}+, apply, then re-run."
                ;;
            orbstack)
                ai "Open OrbStack -> Settings -> System and raise CPU allocation to ${min_cpus}+, then re-run."
                ;;
            *)
                ai "Raise your docker daemon's CPU allocation to ${min_cpus}+ and re-run."
                ;;
        esac
        exit 1
    fi
    ai_ok "Docker CPU budget: ${docker_ncpu} (>=${min_cpus} required for ${workload})"
}

_macos_python_imports_yaml() {
    local pycmd="${1:-python3}"
    "$pycmd" -c 'import yaml' >/dev/null 2>&1
}

_set_installer_python_cmd() {
    local pycmd="$1"
    export ODS_PYTHON_CMD="$pycmd"
    # python-cmd.sh caches the first runnable interpreter. Keep the cache aligned
    # when this installer creates a private venv after the first detection.
    if declare -p _ods_python_cmd_cached >/dev/null 2>&1; then
        _ods_python_cmd_cached="$pycmd"
    fi
}

_ensure_macos_pyyaml() {
    local pycmd=""
    if declare -f ods_detect_python_cmd >/dev/null 2>&1; then
        pycmd="$(ods_detect_python_cmd 2>/dev/null || true)"
    fi
    if [[ -z "$pycmd" ]]; then
        pycmd="$(command -v python3 2>/dev/null || true)"
    fi

    if [[ -z "$pycmd" ]]; then
        ai_err "python3 not available -- required for compose resolver"
        exit 1
    fi

    if _macos_python_imports_yaml "$pycmd"; then
        _set_installer_python_cmd "$pycmd"
        ai_ok "PyYAML available for $pycmd"
        return 0
    fi

    if $DRY_RUN; then
        ai_warn "PyYAML is not importable by $pycmd (dry-run: would create installer Python venv)."
        return 0
    fi

    local venv_dir="${INSTALL_DIR}/.venv/installer-python"
    local venv_python="${venv_dir}/bin/python"

    ai "Installing PyYAML in isolated installer Python runtime..."
    mkdir -p "$(dirname "$venv_dir")"
    if ! "$pycmd" -m venv "$venv_dir" 2>&1 | tee -a "$ODS_LOG_FILE" >/dev/null; then
        ai_err "Failed to create installer Python venv at $venv_dir."
        ai "  Your Python may be missing the venv module."
        ai "  Try: brew install python"
        ai "  Then re-run this installer."
        exit 1
    fi

    if "$venv_python" -m pip install --quiet --no-warn-script-location pyyaml 2>&1 | tee -a "$ODS_LOG_FILE" >/dev/null \
       && _macos_python_imports_yaml "$venv_python"; then
        _set_installer_python_cmd "$venv_python"
        ai_ok "PyYAML available in installer venv"
        return 0
    fi

    ai_err "Failed to install PyYAML for the macOS compose resolver."
    ai "  Log file: $ODS_LOG_FILE"
    ai "  Manual recovery:"
    ai "    $pycmd -m venv '$venv_dir' && '$venv_python' -m pip install pyyaml"
    exit 1
}

# Resolve install directory
INSTALL_DIR="${ODS_INSTALL_DIR}"

if ! $OPENCLAW_EXPLICIT; then
    _existing_openclaw=false
    if command -v docker >/dev/null 2>&1 \
       && docker ps -a --filter "name=^/ods-openclaw$" --format '{{.Names}}' 2>/dev/null \
            | grep -q '^ods-openclaw$'; then
        _existing_openclaw=true
    fi
    if [[ -d "${INSTALL_DIR}/data/openclaw" ]] \
       && [[ -n "$(ls -A "${INSTALL_DIR}/data/openclaw" 2>/dev/null)" ]]; then
        _existing_openclaw=true
    fi
    if $_existing_openclaw; then
        ENABLE_OPENCLAW=true
        ai "Existing OpenClaw install detected; preserving it for this deprecation release"
    fi
    unset _existing_openclaw
fi

# Initialize log file
mkdir -p "$(dirname "$ODS_LOG_FILE")"
: > "$ODS_LOG_FILE"

# ============================================================================
# PHASE 1 -- PREFLIGHT CHECKS
# ============================================================================
show_ods_banner
show_phase 1 6 "PREFLIGHT CHECKS" "30 seconds"

# macOS version
get_macos_version
info_box "macOS:" "${MACOS_NAME} ${MACOS_VERSION} (${MACOS_BUILD})"
if [[ "$MACOS_MAJOR" -lt "$MIN_MACOS_MAJOR" ]]; then
    ai_err "macOS ${MIN_MACOS_MAJOR}+ (Ventura) is required for Metal 3. Found: ${MACOS_VERSION}"
    exit 1
fi
ai_ok "macOS version OK"

# Apple Silicon check
get_apple_silicon_info
if ! $APPLE_IS_APPLE_SILICON; then
    ai_err "Apple Silicon (arm64) is required. Detected: ${APPLE_ARCH}"
    ai_err "Intel Macs do not have Metal GPU acceleration needed for local inference."
    exit 1
fi
info_box "Chip:" "${APPLE_CHIP}"
info_box "Variant:" "${APPLE_CHIP_VARIANT}"
ai_ok "Apple Silicon detected"

# Docker engine (Docker Desktop, Colima, Rancher Desktop, OrbStack, or a
# forwarded socket are all acceptable — see lib/detection.sh).
test_docker_desktop
if ! $DOCKER_INSTALLED; then
    ai_err "Docker engine not found (no \`docker\` CLI on PATH)."
    ai_err "Pick one and install:"
    ai_err "  - Docker Desktop:  https://docs.docker.com/desktop/install/mac-install/"
    ai_err "  - Colima (CLI):    brew install colima docker docker-compose && colima start"
    ai_err "  - Rancher Desktop: https://rancherdesktop.io"
    ai_err "  - OrbStack:        https://orbstack.dev"
    exit 1
fi
ai_ok "Docker CLI found"

if ! $DOCKER_RUNNING; then
    ai_err "Docker daemon is not responding."
    case "${DOCKER_BACKEND:-unknown}" in
        desktop)  ai "Start Docker Desktop from /Applications or the menu bar, then re-run this installer." ;;
        colima)   ai "Run \`colima start\` (e.g. \`colima start --cpu 6 --memory 12 --disk 60\`) then re-run this installer." ;;
        rancher)  ai "Open Rancher Desktop and wait for the daemon to come up, then re-run this installer." ;;
        orbstack) ai "Open OrbStack and wait for the daemon to come up, then re-run this installer." ;;
        *)        ai "Start your docker daemon (Docker Desktop, Colima, Rancher Desktop, OrbStack, ...) and re-run this installer." ;;
    esac
    exit 1
fi
ai_ok "Docker daemon ready (v${DOCKER_VERSION}, backend=${DOCKER_BACKEND:-unknown})"

if ! _ensure_colima_private_network; then
    exit 1
fi

# Pre-flight the docker daemon's CPU allocation. Trip early with a clear
# message rather than letting compose fail after pulls/builds. If the user
# already requested voice from CLI flags (for example --all), account for
# Kokoro's 8-CPU pin now; interactive feature selection is checked again
# after the user picks features.
_docker_cpu_override="${ODS_MIN_DOCKER_CPUS:-}"
_docker_cpu_min="${_docker_cpu_override:-6}"
_docker_cpu_max_pin=4
_docker_cpu_workload="base compose stack"
if $ENABLE_VOICE && [[ -z "$_docker_cpu_override" ]]; then
    _docker_cpu_min=10
    _docker_cpu_max_pin=8
    _docker_cpu_workload="voice-enabled compose stack"
fi
_docker_cpu_preflight_min="$_docker_cpu_min"
_require_docker_cpu_budget "$_docker_cpu_min" "$_docker_cpu_max_pin" "$_docker_cpu_workload"

# Catch a common Colima-after-DockerDesktop config bomb: when a prior
# Docker Desktop install left `"credsStore": "desktop"` in ~/.docker/config.json
# and the user has since moved to Colima/Rancher/OrbStack, every `docker
# compose pull` and `docker compose up` will crash with `error getting
# credentials - err: exec: "docker-credential-desktop": executable file not
# found in $PATH`. We strip the stale entry rather than failing the
# install, because the helper is only meaningful with Docker Desktop.
if [[ "${DOCKER_BACKEND:-unknown}" != "desktop" ]] && test_stale_docker_creds_store; then
    ai_warn "Found stale \`credsStore: desktop\` in ~/.docker/config.json — incompatible with backend=${DOCKER_BACKEND:-unknown}."
    if command -v python3 >/dev/null 2>&1; then
        python3 -c 'import json,sys,os
p=os.path.expanduser("~/.docker/config.json")
d=json.load(open(p))
d.pop("credsStore",None)
json.dump(d, open(p,"w"), indent=2)' && ai_ok "Stripped credsStore=desktop from ~/.docker/config.json"
    else
        ai_err "python3 not available — please remove the \`credsStore\` line from ~/.docker/config.json manually."
        exit 1
    fi
fi

# Filesystem POSIX-permission check
# The .env file (chmod 600) lives at INSTALL_DIR; a non-POSIX FS makes that
# a silent no-op and leaks secrets. Block install before any directory
# creation so the user can pick a different path.
test_install_dir_filesystem "$INSTALL_DIR"
info_box "Filesystem:" "${INSTALL_FS_TYPE}"
if $INSTALL_FS_FATAL; then
    ai_err "INSTALL_DIR (${INSTALL_DIR}) is on a ${INSTALL_FS_TYPE} filesystem."
    ai_err "ODS requires a POSIX-permission filesystem (apfs/hfs) so .env"
    ai_err "secrets can be locked down with chmod 600. ${INSTALL_FS_TYPE} silently"
    ai_err "ignores chmod/chown, leaving secrets world-readable."
    ai "Pick a path on your APFS system volume (e.g. ~/ods) and re-run."
    exit 1
fi
ai_ok "Filesystem supports POSIX permissions"

# Networked-filesystem advisory (warn-only).
# chmod 600 still applies on NFS/SMB/AFP, but the actual access control is
# enforced server-side by the share's ACL — other clients with access to the
# share may read .env regardless of local permissions.
if [[ "${INSTALL_FS_NETWORKED:-false}" == "true" ]]; then
    ai_warn "INSTALL_DIR ($INSTALL_DIR) is on a networked filesystem ($INSTALL_FS_TYPE)."
    ai_warn ".env permissions (chmod 600) are advisory — actual access control is governed by the share's ACL on the server."
    ai_warn "If this share is exposed to other clients, sensitive credentials may be readable from those hosts."
fi

# Docker Desktop file-sharing allowlist check
# Bind-mounts of paths outside the allowlist fail with cryptic OCI errors at
# `docker compose up`. Probe with a throwaway container so we surface a clear
# message before any compose work starts.
test_docker_desktop_sharing "$INSTALL_DIR"
if ! $DOCKER_SHARE_OK; then
    ai_err "Docker Desktop cannot bind-mount ${INSTALL_DIR}."
    ai_err "Add the path to Docker Desktop > Settings > Resources > File Sharing,"
    ai_err "apply, then re-run this installer."
    if [[ -n "$DOCKER_SHARE_ERR" ]]; then
        ai "Probe output:"
        printf '%s\n' "$DOCKER_SHARE_ERR" | sed 's/^/    /'
    fi
    exit 1
fi
ai_ok "Docker Desktop file sharing OK"

# Disk space
test_disk_space "$INSTALL_DIR" 30
info_box "Disk free:" "${DISK_FREE_GB} GB"
if ! $DISK_SUFFICIENT; then
    ai_err "At least ${DISK_REQUIRED_GB} GB free space required. Found ${DISK_FREE_GB} GB."
    exit 1
fi
ai_ok "Disk space OK"

# PyYAML -- required by scripts/resolve-compose-stack.sh for the compose
# security scan (it parses every extension/overlay manifest before letting
# user composes through). Homebrew Python on macOS is externally managed, so
# `pip --user` can fail under PEP 668. Keep the resolver dependency in a
# ODS-owned venv and point shared Python helpers at that interpreter.
_ensure_macos_pyyaml

# Ollama conflict detection
check_ollama_conflict
if $OLLAMA_RUNNING; then
    ai_warn "Ollama is running (PID ${OLLAMA_PID}) and may conflict with ODS."
    ai "  Both use port 11434/8080. Ollama will shadow llama-server."
    if ! $NON_INTERACTIVE; then
        read -r -p "  Stop Ollama for this session? [Y/n] " ollama_choice < /dev/tty
        if [[ ! "$ollama_choice" =~ ^[nN] ]]; then
            kill "$OLLAMA_PID" 2>/dev/null || true
            sleep 2
            if pgrep -x ollama >/dev/null 2>&1; then
                ai_warn "Ollama restarted automatically. You may need to quit it from the menu bar."
            else
                ai_ok "Ollama stopped"
            fi
        else
            ai_warn "Ollama left running. Port conflicts may occur."
        fi
    else
        ai_warn "Ollama detected. Run without --non-interactive to resolve, or stop Ollama manually."
    fi
fi

# Port conflict checks — dynamically read from extension manifests
_conflict_ports=(8080 11434)  # llama-server (native) + Ollama default (host conflict, no manifest)
for _manifest in "${SOURCE_ROOT}/extensions/services/"*/manifest.yaml; do
    [[ -f "$_manifest" ]] || continue
    _port=$(grep 'external_port_default:' "$_manifest" 2>/dev/null | awk '{print $2}' | tr -d '"') || true
    # Skip port 0 — used by internal-only services (e.g. hermes is gated
    # behind hermes-proxy and exposes nothing) as a "no external port"
    # sentinel. Passing 0 to check_port_conflict trips lsof rows where the
    # local-port column reads 0 (identityservicesd does this on macOS) and
    # produces a confusing "Port 0 is in use" warning.
    if [[ -n "$_port" && "$_port" =~ ^[0-9]+$ && "$_port" -gt 0 && "$_port" -ne 8080 ]]; then
        _conflict_ports+=("$_port")
    fi
done

for port_check in "${_conflict_ports[@]}"; do
    if check_port_conflict "$port_check"; then
        ai_warn "Port ${port_check} is in use by ${PORT_CONFLICT_PROC} (PID ${PORT_CONFLICT_PID})"
    fi
done

# macOS AirPlay Receiver uses port 9000 (Monterey 12.0+, enabled by default).
# It cannot be killed — it's a system service. Auto-reassign Whisper to 9100.
if check_port_conflict 9000; then
    export WHISPER_PORT=9100
    ai_ok "Port 9000 in use (AirPlay Receiver) -- Whisper reassigned to port ${WHISPER_PORT}"
    ai "  To disable AirPlay Receiver: System Settings > General > AirDrop & Handoff > AirPlay Receiver"
fi

# ============================================================================
# PHASE 2 -- HARDWARE DETECTION
# ============================================================================
show_phase 2 6 "HARDWARE DETECTION" "10 seconds"

get_system_ram_gb

info_box "Chip:" "${APPLE_CHIP}"
info_box "Variant:" "${APPLE_CHIP_VARIANT}"
info_box "RAM:" "${SYSTEM_RAM_GB} GB (unified memory = effective VRAM)"
info_box "P-Cores:" "${APPLE_PERF_CORES}"
info_box "E-Cores:" "${APPLE_EFF_CORES}"
info_box "GPU Cores:" "${APPLE_GPU_CORES}"
info_box "Neural Engine:" "${APPLE_HAS_NEURAL_ENGINE}"
info_box "Backend:" "apple (Metal)"

# Auto-select tier (or use override)
if $CLOUD_MODE; then
    SELECTED_TIER="CLOUD"
elif [[ -n "$TIER_OVERRIDE" ]]; then
    SELECTED_TIER=$(echo "$TIER_OVERRIDE" | tr '[:lower:]' '[:upper:]')
    # Normalize T-prefix: T1 -> 1, T2 -> 2, etc.
    if [[ "$SELECTED_TIER" =~ ^T([0-9])$ ]]; then
        SELECTED_TIER="${BASH_REMATCH[1]}"
    fi
else
    SELECTED_TIER=$(auto_select_tier "$SYSTEM_RAM_GB" "$APPLE_CHIP_VARIANT")
fi

if [[ -z "${MODEL_PROFILE:-}" ]]; then
    if [[ -f "${INSTALL_DIR}/.env" ]]; then
        _existing_model_profile=$(grep -m1 '^MODEL_PROFILE=' "${INSTALL_DIR}/.env" 2>/dev/null | cut -d= -f2- | tr -d '\r' || true)
        MODEL_PROFILE="${_existing_model_profile:-qwen}"
    else
        MODEL_PROFILE="qwen"
    fi
fi

resolve_tier_config "$SELECTED_TIER"
if [[ "${ODS_DISABLE_CATALOG_MODEL_SELECTOR:-false}" != "true" && "$SELECTED_TIER" != "CLOUD" ]]; then
    _selector_script="${SOURCE_ROOT}/scripts/select-model.py"
    _selector_catalog="${SOURCE_ROOT}/config/model-library.json"
    if [[ -f "$_selector_script" && -f "$_selector_catalog" ]]; then
        _selector_python=""
        if [[ -f "${SOURCE_ROOT}/lib/python-cmd.sh" ]]; then
            # shellcheck source=/dev/null
            . "${SOURCE_ROOT}/lib/python-cmd.sh"
            _selector_python="$(ods_detect_python_cmd || true)"
        fi
        if [[ -z "$_selector_python" ]]; then
            if command -v python3 >/dev/null 2>&1; then
                _selector_python="python3"
            elif command -v python >/dev/null 2>&1; then
                _selector_python="python"
            fi
        fi
        if [[ -n "$_selector_python" ]]; then
            _selector_env="$("$_selector_python" "$_selector_script" \
                --catalog "$_selector_catalog" \
                --backend "apple" \
                --memory-type "unified" \
                --vram-mb "0" \
                --ram-gb "${SYSTEM_RAM_GB:-0}" \
                --profile "${MODEL_PROFILE_EFFECTIVE:-${MODEL_PROFILE:-qwen}}" \
                --tier "$SELECTED_TIER" \
                --max-size-mb "${LLM_MODEL_SIZE_MB:-0}" \
                --host-arch "$(uname -m 2>/dev/null || echo unknown)" \
                --installable-only \
                --env 2>>"$ODS_LOG_FILE" || true)"
            if [[ -n "$_selector_env" ]]; then
                load_model_selector_env_from_output <<< "$_selector_env"
                ai "Model selector: ${MODEL_RECOMMENDATION_REASON:-$LLM_MODEL}"
            else
                ai "Model selector unavailable; using tier-map model ${LLM_MODEL}"
            fi
        fi
    fi
fi
if [[ -n "${LLAMA_CPP_RELEASE_TAG_OVERRIDE:-}" ]]; then
    LLAMA_CPP_RELEASE_TAG="$LLAMA_CPP_RELEASE_TAG_OVERRIDE"
    LLAMA_CPP_MACOS_ASSET="llama-${LLAMA_CPP_RELEASE_TAG}-bin-macos-arm64.tar.gz"
    LLAMA_CPP_MACOS_URL="https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_CPP_RELEASE_TAG}/${LLAMA_CPP_MACOS_ASSET}"
fi
ai_ok "Selected tier: ${SELECTED_TIER} (${TIER_NAME})"
info_box "Model:" "${LLM_MODEL}"
info_box "GGUF:" "${GGUF_FILE}"
info_box "Context:" "${MAX_CONTEXT}"

# Re-check disk space for model + Docker images. Prefer the catalog selector's
# exact model size when available; it can choose entries that do not fit the
# older tier-map filename heuristics.
_model_size_mb="${LLM_MODEL_SIZE_MB:-0}"
if [[ "$_model_size_mb" =~ ^[0-9]+$ && "$_model_size_mb" -gt 0 ]]; then
    _model_gb=$(( (_model_size_mb + 1023) / 1024 ))
    NEEDED_GB=$(( _model_gb + 15 ))
elif [[ "$GGUF_FILE" =~ 80B|Coder-Next ]]; then
    NEEDED_GB=65
elif [[ "$GGUF_FILE" =~ 31B ]]; then
    NEEDED_GB=38
elif [[ "$GGUF_FILE" =~ 30B|26B ]]; then
    NEEDED_GB=35
elif [[ "$GGUF_FILE" =~ 14B ]]; then
    NEEDED_GB=27
elif [[ "$GGUF_FILE" =~ E4B ]]; then
    NEEDED_GB=25
else
    NEEDED_GB=23
fi
test_disk_space "$INSTALL_DIR" "$NEEDED_GB"
if ! $DISK_SUFFICIENT; then
    ai_warn "Tier ${SELECTED_TIER} needs ~${NEEDED_GB} GB (model + Docker images). Only ${DISK_FREE_GB} GB free."
    if ! $FORCE; then exit 1; fi
fi

# ============================================================================
# PHASE 3 -- FEATURE SELECTION
# ============================================================================
show_phase 3 6 "FEATURES" "interactive"

if ! $NON_INTERACTIVE && ! $ALL_FEATURES && ! $DRY_RUN; then
    chapter "Select Features"
    ai "Choose your ODS configuration:"
    echo ""
    echo -e "  ${BGRN}[1]${NC} Full Stack   -- Everything enabled (voice, workflows, RAG, agents)"
    echo -e "  ${WHT}[2]${NC} Core Only    -- Chat + LLM inference (lean and fast)"
    echo -e "  ${WHT}[3]${NC} Custom       -- Choose individually"
    echo ""

    read -r -p "  Selection (1/2/3): " feature_choice < /dev/tty
    case "${feature_choice:-1}" in
        1)
            ENABLE_VOICE=true; ENABLE_WORKFLOWS=true
            ENABLE_RAG=true; ENABLE_HERMES=true
            ENABLE_RECOMMENDED=true
            ENABLE_OPENCLAW=false  # deprecated; Hermes is the default
            ENABLE_APE=true
            ENABLE_PERPLEXICA=true
            ENABLE_PRIVACY_SHIELD=true
            ENABLE_LANGFUSE=true
            ;;
        2)
            ENABLE_VOICE=false; ENABLE_WORKFLOWS=false
            ENABLE_RAG=false; ENABLE_RECOMMENDED=false
            ENABLE_HERMES=false
            ENABLE_OPENCLAW=false
            ENABLE_APE=false
            ENABLE_PERPLEXICA=false
            ENABLE_PRIVACY_SHIELD=false
            ENABLE_LANGFUSE=false
            ;;
        3)
            read -r -p "  Enable Voice (Whisper + Kokoro)? [y/N] " yn < /dev/tty
            [[ "$yn" =~ ^[yY] ]] && ENABLE_VOICE=true
            read -r -p "  Enable Workflows (n8n)?           [y/N] " yn < /dev/tty
            [[ "$yn" =~ ^[yY] ]] && ENABLE_WORKFLOWS=true
            read -r -p "  Enable RAG (Qdrant + embeddings)? [y/N] " yn < /dev/tty
            [[ "$yn" =~ ^[yY] ]] && ENABLE_RAG=true
            read -r -p "  Enable recommended support (LiteLLM + SearXNG + Token Spy)? [Y/n] " yn < /dev/tty
            [[ "$yn" =~ ^[nN] ]] && ENABLE_RECOMMENDED=false || ENABLE_RECOMMENDED=true
            read -r -p "  Enable Hermes Agent (default AI agent)? [Y/n] " yn < /dev/tty
            [[ "$yn" =~ ^[nN] ]] && ENABLE_HERMES=false || ENABLE_HERMES=true
            read -r -p "  Enable OpenClaw (DEPRECATED — Hermes replaces it)? [y/N] " yn < /dev/tty
            [[ "$yn" =~ ^[yY] ]] && ENABLE_OPENCLAW=true
            read -r -p "  Enable Perplexica deep research? [y/N] " yn < /dev/tty
            [[ "$yn" =~ ^[yY] ]] && ENABLE_PERPLEXICA=true
            read -r -p "  Enable Privacy Shield? [y/N] " yn < /dev/tty
            [[ "$yn" =~ ^[yY] ]] && ENABLE_PRIVACY_SHIELD=true
            read -r -p "  Enable Langfuse (LLM observability, ~500MB)? [y/N] " yn < /dev/tty
            [[ "$yn" =~ ^[yY] ]] && ENABLE_LANGFUSE=true
            ;;
        *)
            ENABLE_VOICE=true; ENABLE_WORKFLOWS=true
            ENABLE_RAG=true; ENABLE_HERMES=true
            ENABLE_RECOMMENDED=true
            ENABLE_OPENCLAW=false  # deprecated; Hermes is the default
            ENABLE_APE=true
            ENABLE_PERPLEXICA=true
            ENABLE_PRIVACY_SHIELD=true
            ENABLE_LANGFUSE=true
            ;;
    esac
fi

if $CLOUD_MODE && ! $ENABLE_RECOMMENDED; then
    ai "Cloud mode requires the LiteLLM gateway; enabling recommended support"
    ENABLE_RECOMMENDED=true
fi
if ! $ENABLE_HERMES && ! $ENABLE_OPENCLAW; then
    ENABLE_APE=false
fi

if $ENABLE_HERMES && ! $CLOUD_MODE; then
    if [[ "${MAX_CONTEXT:-0}" =~ ^[0-9]+$ ]] && (( MAX_CONTEXT < HERMES_CONTEXT_SIZE )); then
        ai_warn "Hermes enabled: increasing macOS llama context from ${MAX_CONTEXT} to ${HERMES_CONTEXT_SIZE} (64K floor)."
        if [[ -n "${MODEL_RECOMMENDATION_REASON:-}" ]]; then
            MODEL_RECOMMENDATION_REASON="${MODEL_RECOMMENDATION_REASON} Hermes requires at least 64K context, so runtime context was raised to ${HERMES_CONTEXT_SIZE}."
        fi
        MAX_CONTEXT="$HERMES_CONTEXT_SIZE"
    fi
fi

ai "Features:"
info_box "  Voice:" "$(if $ENABLE_VOICE; then echo enabled; else echo disabled; fi)"
info_box "  Workflows:" "$(if $ENABLE_WORKFLOWS; then echo enabled; else echo disabled; fi)"
info_box "  RAG:" "$(if $ENABLE_RAG; then echo enabled; else echo disabled; fi)"
info_box "  Recommended:" "$(if $ENABLE_RECOMMENDED; then echo enabled; else echo disabled; fi)"
info_box "  Hermes:" "$(if $ENABLE_HERMES; then echo enabled; else echo disabled; fi)"
info_box "  OpenClaw:" "$(if $ENABLE_OPENCLAW; then echo "enabled (DEPRECATED)"; else echo disabled; fi)"
info_box "  Perplexica:" "$(if $ENABLE_PERPLEXICA; then echo enabled; else echo disabled; fi)"
info_box "  Privacy Shield:" "$(if $ENABLE_PRIVACY_SHIELD; then echo enabled; else echo disabled; fi)"
info_box "  Langfuse:" "$(if $ENABLE_LANGFUSE; then echo enabled; else echo disabled; fi)"
# The macOS installer doesn't currently ship a ComfyUI container — none of
# the published ComfyUI images target Apple Silicon Metal, and the upstream
# Python build under MPS is non-trivial to package as a Docker service.
# Surface this to operators who passed --all so they aren't left wondering
# why the dashboard shows no image-gen tile after install.
info_box "  ComfyUI:" "not available on macOS (no MPS Docker image upstream)"

if $ENABLE_VOICE && [[ -z "$_docker_cpu_override" ]] && [[ "${_docker_cpu_preflight_min:-0}" -lt 10 ]]; then
    _require_docker_cpu_budget 10 8 "voice-enabled compose stack"
fi

# ============================================================================
# PHASE 4 -- SETUP (directories, copy source, generate .env)
# ============================================================================
show_phase 4 6 "SETUP" "1-2 minutes"

if $DRY_RUN; then
    ai "[DRY RUN] Would create: ${INSTALL_DIR}"
    ai "[DRY RUN] Would copy source files"
    ai "[DRY RUN] Would generate .env with secrets"
    ai "[DRY RUN] Would generate SearXNG config"
    $ENABLE_HERMES && ai "[DRY RUN] Would configure Hermes Agent (data: ${INSTALL_DIR}/data/hermes)"
    $ENABLE_OPENCLAW && ai "[DRY RUN] Would configure OpenClaw"
    $ENABLE_LANGFUSE && ai "[DRY RUN] Would enable Langfuse (LLM observability)"
else
    # Create directory structure
    mkdir -p "${INSTALL_DIR}/config/searxng"
    mkdir -p "${INSTALL_DIR}/config/n8n"
    mkdir -p "${INSTALL_DIR}/config/litellm"
    mkdir -p "${INSTALL_DIR}/config/openclaw"
    mkdir -p "${INSTALL_DIR}/config/llama-server"
    mkdir -p "${INSTALL_DIR}/data/open-webui"
    mkdir -p "${INSTALL_DIR}/data/whisper"
    mkdir -p "${INSTALL_DIR}/data/tts"
    mkdir -p "${INSTALL_DIR}/data/n8n"
    mkdir -p "${INSTALL_DIR}/data/qdrant"
    mkdir -p "${INSTALL_DIR}/data/models"
    mkdir -p "${INSTALL_DIR}/data/privacy-shield"
    mkdir -p "${INSTALL_DIR}/data/ape"
    mkdir -p "${INSTALL_DIR}/data/token-spy"
    mkdir -p "${INSTALL_DIR}/data/hermes"
    mkdir -p "${INSTALL_DIR}/data/hermes-proxy/caddy-data"
    mkdir -p "${INSTALL_DIR}/data/hermes-proxy/caddy-config"
    mkdir -p "${INSTALL_DIR}/data/langfuse/postgres"
    mkdir -p "${INSTALL_DIR}/data/langfuse/clickhouse"
    mkdir -p "${INSTALL_DIR}/data/langfuse/redis"
    mkdir -p "${INSTALL_DIR}/data/langfuse/minio"
    mkdir -p "${INSTALL_DIR}/bin"
    ai_ok "Created directory structure"

    # Copy source tree (skip .git, data, logs, .env, models)
    if [[ "$SOURCE_ROOT" != "$INSTALL_DIR" ]]; then
        ai "Copying source files to ${INSTALL_DIR}..."
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
            "$SOURCE_ROOT/" "$INSTALL_DIR/"
        ai_ok "Source files installed"
    else
        ai "Running in-place, skipping file copy"
    fi

    # Retired from the shipped stack after Hermes became the default agent
    # surface. Remove stale service files left behind by non-pruning upgrades,
    # while preserving data/odsforge for user-controlled archival.
    if [[ -d "${INSTALL_DIR}/extensions/services/odsforge" ]]; then
        rm -rf "${INSTALL_DIR}/extensions/services/odsforge"
        log "Removed retired ODSForge service files from extensions/services"
    fi

    # Copy extensions library to data dir for dashboard portal.
    # Source resolution: dev installs and full checkouts read the product-owned
    # library under extensions/library/. Bootstrap installs also get the same
    # templates bundled by get-ods.sh under extensions-library-bundle/.
    _ext_lib_src=""
    for _candidate in \
        "${SOURCE_ROOT}/extensions/library/services" \
        "${INSTALL_DIR}/extensions/library/services" \
        "${INSTALL_DIR}/extensions-library-bundle/services"
    do
        if [[ -d "$_candidate" ]]; then _ext_lib_src="$_candidate"; break; fi
    done
    if [[ -n "$_ext_lib_src" ]]; then
        mkdir -p "${INSTALL_DIR}/data/extensions-library"
        cp -r "$_ext_lib_src/." "${INSTALL_DIR}/data/extensions-library/"
        ai_ok "Extensions library copied to data/extensions-library/ (from $_ext_lib_src)"
    else
        ai_warn "Extensions library not found; dashboard Extensions page will return 503 until populated"
    fi

    # Copy CLI tool to install root
    if [[ -f "${SCRIPT_DIR}/ods-macos.sh" ]]; then
        cp "${SCRIPT_DIR}/ods-macos.sh" "${INSTALL_DIR}/ods-macos.sh"
        chmod +x "${INSTALL_DIR}/ods-macos.sh"
        # Also copy the lib/ directory ods-macos.sh needs
        mkdir -p "${INSTALL_DIR}/lib"
        cp "${SCRIPT_DIR}/lib/"*.sh "${INSTALL_DIR}/lib/"
        ai_ok "Installed ods-macos.sh CLI"
    fi

    # The resolver treats compose.yaml as enabled and compose.yaml.disabled as
    # disabled. Persist every installer feature choice in that canonical form
    # so cache invalidation and later dashboard toggles cannot resurrect the
    # installer's unselected built-ins.
    _macos_sync_builtin_compose_states

    # A detached bootstrap worker can rewrite GGUF_FILE/LLM_MODEL after its
    # download finishes. Stop and disable it before cloud mode or a forced
    # fresh install touches .env so the values below are authoritative.
    if $CLOUD_MODE; then
        if ! _macos_cancel_detached_bootstrap_upgrade "cloud_mode" "cloud transition"; then
            exit 1
        fi
    elif $FORCE && ! _macos_cancel_detached_bootstrap_upgrade "fresh_install" "fresh install"; then
        exit 1
    fi

    # Generate .env (idempotent unless --force)
    env_existed=false
    [[ -f "${INSTALL_DIR}/.env" ]] && env_existed=true
    _previous_ods_mode="$(read_env_value "${INSTALL_DIR}/.env" "ODS_MODE")"
    _previous_llm_bind="$(read_env_value "${INSTALL_DIR}/.env" "BIND_ADDRESS")"
    _previous_macos_gateway="$(read_env_value "${INSTALL_DIR}/.env" "ODS_MACOS_HOST_GATEWAY")"
    generate_ods_env "$INSTALL_DIR" "$SELECTED_TIER" "$FORCE"
    _macos_agent_bind_raw="$(read_env_value "${INSTALL_DIR}/.env" "ODS_AGENT_BIND")"
    _macos_agent_bind="$(macos_normalize_agent_bind "${_macos_agent_bind_raw:-127.0.0.1}")"
    if [[ -n "$_macos_agent_bind_raw" && "$_macos_agent_bind" != "$_macos_agent_bind_raw" ]]; then
        ai_warn "ODS_AGENT_BIND=${_macos_agent_bind_raw} uses an unsupported IPv6 server socket; using ${_macos_agent_bind}"
        upsert_env_value "${INSTALL_DIR}/.env" "ODS_AGENT_BIND" "$_macos_agent_bind"
    fi
    _macos_llm_bridge_enabled="false"
    if [[ "${DOCKER_BACKEND:-unknown}" == "colima" ]]; then
        _macos_llm_bind="$(read_env_value "${INSTALL_DIR}/.env" "BIND_ADDRESS")"
        [[ -n "$_macos_llm_bind" ]] || _macos_llm_bind="127.0.0.1"
        _macos_llm_bridge_enabled="true"
        _macos_agent_bridge_enabled="true"
        macos_bind_uses_direct_gateway "$_macos_llm_bind" "$COLIMA_HOST_IP" && _macos_llm_bridge_enabled="false"
        macos_bind_uses_direct_gateway "$_macos_agent_bind" "$COLIMA_HOST_IP" && _macos_agent_bridge_enabled="false"
        upsert_env_value "${INSTALL_DIR}/.env" "ODS_MACOS_HOST_GATEWAY" "$COLIMA_HOST_IP"
        upsert_env_value "${INSTALL_DIR}/.env" "ODS_MACOS_VM_IP" "$COLIMA_VM_IP"
        upsert_env_value "${INSTALL_DIR}/.env" "ODS_MACOS_HOST_AGENT_BRIDGE_ENABLED" "$_macos_agent_bridge_enabled"
        upsert_env_value "${INSTALL_DIR}/.env" "ODS_AGENT_HOST" "$COLIMA_HOST_IP"
        upsert_env_value "${INSTALL_DIR}/.env" "ODS_MACOS_LLM_BRIDGE_ENABLED" "$_macos_llm_bridge_enabled"
        upsert_env_value "${INSTALL_DIR}/.env" "ODS_NATIVE_LLAMA_PORT" "8080"
        unset _macos_llm_bind _macos_agent_bridge_enabled
    else
        upsert_env_value "${INSTALL_DIR}/.env" "ODS_MACOS_HOST_AGENT_BRIDGE_ENABLED" "false"
        upsert_env_value "${INSTALL_DIR}/.env" "ODS_MACOS_LLM_BRIDGE_ENABLED" "false"
        upsert_env_value "${INSTALL_DIR}/.env" "ODS_AGENT_HOST" "host.docker.internal"
        upsert_env_value "${INSTALL_DIR}/.env" "ODS_MACOS_HOST_GATEWAY" ""
        upsert_env_value "${INSTALL_DIR}/.env" "ODS_MACOS_VM_IP" ""
        upsert_env_value "${INSTALL_DIR}/.env" "ODS_NATIVE_LLAMA_PORT" "8080"
    fi
    if $CLOUD_MODE; then
        _macos_litellm_key="$(read_env_value "${INSTALL_DIR}/.env" "LITELLM_KEY")"
        if [[ -z "$_macos_litellm_key" ]]; then
            ai_err "Cloud mode requires the generated LiteLLM master key, but LITELLM_KEY is empty."
            exit 1
        fi
        upsert_env_value "${INSTALL_DIR}/.env" "ODS_MODE" "cloud"
        upsert_env_value "${INSTALL_DIR}/.env" "ODS_MACOS_LLM_BRIDGE_ENABLED" "false"
        _macos_llm_bridge_enabled="false"
        upsert_env_value "${INSTALL_DIR}/.env" "LLM_API_URL" "http://litellm:4000"
        upsert_env_value "${INSTALL_DIR}/.env" "HERMES_LLM_BASE_URL" "http://litellm:4000/v1"
        upsert_env_value "${INSTALL_DIR}/.env" "HERMES_LLM_API_KEY" "$_macos_litellm_key"
        upsert_env_value "${INSTALL_DIR}/.env" "LLM_MODEL" "$LLM_MODEL"
        upsert_env_value "${INSTALL_DIR}/.env" "GGUF_FILE" ""
        upsert_env_value "${INSTALL_DIR}/.env" "MAX_CONTEXT" "$MAX_CONTEXT"
        upsert_env_value "${INSTALL_DIR}/.env" "CTX_SIZE" "$MAX_CONTEXT"
    else
        upsert_env_value "${INSTALL_DIR}/.env" "ODS_MODE" "local"
        upsert_env_value "${INSTALL_DIR}/.env" "LLM_BACKEND" "llama-server"
        upsert_env_value "${INSTALL_DIR}/.env" "HERMES_LLM_API_KEY" "sk-ods-hermes-local"
        if [[ "$_previous_ods_mode" == "cloud" ]]; then
            upsert_env_value "${INSTALL_DIR}/.env" "LLM_MODEL" "$LLM_MODEL"
            upsert_env_value "${INSTALL_DIR}/.env" "GGUF_FILE" "$GGUF_FILE"
            upsert_env_value "${INSTALL_DIR}/.env" "MAX_CONTEXT" "$MAX_CONTEXT"
            upsert_env_value "${INSTALL_DIR}/.env" "CTX_SIZE" "$MAX_CONTEXT"
        fi
        if [[ "${DOCKER_BACKEND:-unknown}" == "colima" ]]; then
            upsert_env_value "${INSTALL_DIR}/.env" "LLM_API_URL" "http://${COLIMA_HOST_IP}:8080"
            upsert_env_value "${INSTALL_DIR}/.env" "HERMES_LLM_BASE_URL" "http://${COLIMA_HOST_IP}:8080/v1"
        else
            upsert_env_value "${INSTALL_DIR}/.env" "LLM_API_URL" "http://host.docker.internal:8080"
            upsert_env_value "${INSTALL_DIR}/.env" "HERMES_LLM_BASE_URL" "http://host.docker.internal:8080/v1"
        fi
    fi
    if [[ "${DOCKER_BACKEND:-unknown}" == "colima" ]] \
       && [[ "$_macos_llm_bridge_enabled" == "true" ]] \
       && macos_bind_uses_direct_gateway "${_previous_llm_bind:-127.0.0.1}" "$_previous_macos_gateway"; then
        _macos_stop_install_owned_native_llama \
            "Stopping the old direct native listener before recreating the loopback Colima bridge..."
    fi
    if ! _configure_macos_llm_bridge; then
        exit 1
    fi
    unset _macos_agent_bind _macos_agent_bind_raw _previous_ods_mode \
        _previous_llm_bind _previous_macos_gateway _macos_llm_bridge_enabled \
        _macos_litellm_key
    CONTAINER_LLM_URL="$(read_env_value "${INSTALL_DIR}/.env" "LLM_API_URL")"
    [[ -n "$CONTAINER_LLM_URL" ]] || CONTAINER_LLM_URL="http://host.docker.internal:8080"
    if $env_existed && ! $FORCE; then
        ai_ok "Preserved existing .env (use --force to regenerate secrets)"
    else
        ai_ok "Generated .env with secure secrets"
    fi
    if $ENABLE_HERMES && ! $CLOUD_MODE; then
        upsert_env_value "${INSTALL_DIR}/.env" "MAX_CONTEXT" "$MAX_CONTEXT"
        upsert_env_value "${INSTALL_DIR}/.env" "CTX_SIZE" "$MAX_CONTEXT"
        ai_ok "Set macOS llama context to ${MAX_CONTEXT} for Hermes"
    fi

    # Generate SearXNG config
    searx_existed=false
    [[ -f "${INSTALL_DIR}/config/searxng/settings.yml" ]] && searx_existed=true
    generate_searxng_config "$INSTALL_DIR" "$ENV_SEARXNG_SECRET" "$FORCE"
    if $searx_existed && ! $FORCE; then
        ai_ok "Preserved existing SearXNG config (use --force to regenerate)"
    else
        ai_ok "Generated SearXNG config"
    fi

    # Generate OpenClaw configs (if enabled)
    if $ENABLE_OPENCLAW; then
        openclaw_existed=false
        [[ -f "${INSTALL_DIR}/data/openclaw/home/openclaw.json" ]] && openclaw_existed=true
        _openclaw_model="$LLM_MODEL"
        _openclaw_api_key="none"
        if $CLOUD_MODE; then
            _openclaw_model="default"
            _openclaw_api_key="$(read_env_value "$INSTALL_DIR/.env" "LITELLM_KEY")"
        fi
        if [[ -z "$_openclaw_api_key" ]] \
           || ! generate_openclaw_config "$INSTALL_DIR" "$_openclaw_model" "$MAX_CONTEXT" \
                "$ENV_OPENCLAW_TOKEN" "$CONTAINER_LLM_URL" "$FORCE" "$_openclaw_api_key"; then
            ai_err "Could not configure OpenClaw for the active inference route."
            exit 1
        fi
        if $openclaw_existed && ! $FORCE; then
            ai_ok "Refreshed OpenClaw's managed inference route while preserving unrelated settings"
        else
            ai_ok "Generated OpenClaw configs"
        fi
        unset _openclaw_model _openclaw_api_key
    fi

    # Create llama-server models.ini (empty -- populated later)
    local_models_ini="${INSTALL_DIR}/config/llama-server/models.ini"
    if [[ ! -f "$local_models_ini" ]]; then
        echo "# ODS model registry" > "$local_models_ini"
    fi
fi

# ============================================================================
# PHASE 5 -- LAUNCH (download model, start services)
# ============================================================================
show_phase 5 6 "LAUNCH" "2-30 minutes (model download)"

if $DRY_RUN; then
    [[ -n "$GGUF_URL" ]] && ai "[DRY RUN] Would download: ${GGUF_FILE}"
    ai "[DRY RUN] Would download llama-server (Metal build)"
    ai "[DRY RUN] Would start native llama-server on port 8080"
    ai "[DRY RUN] Would run: docker compose up -d --remove-orphans --no-build --pull never"
else
    # Change to install directory for docker compose
    cd "$INSTALL_DIR"

    # ── Bootstrap fast-start ──────────────────────────────────────────────
    _BOOTSTRAP_ACTIVE=false
    if bootstrap_needed "$SELECTED_TIER" "$INSTALL_DIR" "$GGUF_FILE"; then
        _BOOTSTRAP_ACTIVE=true
        FULL_GGUF_FILE="$GGUF_FILE"
        FULL_GGUF_URL="$GGUF_URL"
        FULL_GGUF_SHA256="$GGUF_SHA256"
        FULL_LLM_MODEL="$LLM_MODEL"
        FULL_MAX_CONTEXT="$MAX_CONTEXT"

        GGUF_FILE="$BOOTSTRAP_GGUF_FILE"
        GGUF_URL="$BOOTSTRAP_GGUF_URL"
        GGUF_SHA256="${BOOTSTRAP_GGUF_SHA256:-}"
        LLM_MODEL="$BOOTSTRAP_LLM_MODEL"
        MAX_CONTEXT="$BOOTSTRAP_MAX_CONTEXT"
        ai "Fast-start mode: downloading bootstrap model (~1.5GB) for instant chat."
        ai "Your full model ($FULL_LLM_MODEL) will download in the background."
    fi

    # ── Download GGUF model (if not cloud-only) ──
    if [[ -n "$GGUF_URL" ]] && ! $CLOUD_MODE; then
        MODEL_PATH="${INSTALL_DIR}/data/models/${GGUF_FILE}"

        if [[ -f "$MODEL_PATH" ]]; then
            # Verify integrity if hash is available
            if verify_sha256 "$MODEL_PATH" "$GGUF_SHA256" "Model ${GGUF_FILE}"; then
                ai_ok "Model already present and verified: ${GGUF_FILE}"
            else
                ai "Removing corrupt file and re-downloading..."
                rm -f "$MODEL_PATH"
            fi
        fi

        if [[ ! -f "$MODEL_PATH" ]]; then
            # Download with retry logic (built into download_with_progress)
            if ! download_with_progress "$GGUF_URL" "$MODEL_PATH" "Downloading ${GGUF_FILE}"; then
                ai_err "Model download failed after retries. Re-run the installer to try again."
                exit 1
            fi

            # Verify freshly downloaded file
            if ! verify_sha256 "$MODEL_PATH" "$GGUF_SHA256" "Downloaded ${GGUF_FILE}"; then
                rm -f "$MODEL_PATH"
                ai_err "Downloaded file is corrupt. Re-run the installer to try again."
                exit 1
            fi
        fi
    fi

    # ── Patch .env for bootstrap model ──────────────────────────────────────
    if [[ "$_BOOTSTRAP_ACTIVE" == "true" ]]; then
        _env_file="$INSTALL_DIR/.env"
        if [[ -f "$_env_file" ]]; then
            sed -i '' "s|^GGUF_FILE=.*|GGUF_FILE=${GGUF_FILE}|" "$_env_file"
            sed -i '' "s|^LLM_MODEL=.*|LLM_MODEL=${LLM_MODEL}|" "$_env_file"
            sed -i '' "s|^MAX_CONTEXT=.*|MAX_CONTEXT=${MAX_CONTEXT}|" "$_env_file"
            sed -i '' "s|^CTX_SIZE=.*|CTX_SIZE=${MAX_CONTEXT}|" "$_env_file"
            ai_ok "Patched .env for bootstrap model ($GGUF_FILE)"
        fi

    fi

    # ── Hermes config substitution (macOS-specific) ──
    #
    # Same pattern as the Linux phase 11 substitution but with the
    # macOS-specific base_url. The template ships with
    # `base_url: "http://llama-server:8080/v1"`, which only resolves
    # inside the ods compose bridge. On macOS llama-server
    # runs native on the host (Metal binary, port 8080), so the
    # Hermes container reaches it through the scoped macOS host route — and
    # crucially `model.base_url` in cli-config.yaml WINS over the
    # OPENAI_BASE_URL env compose.yaml sets, so the env override the
    # Linux path relies on doesn't help here.
    #
    # Also patch model.default to the actual served file name. Native
    # Mac llama.cpp serves under the file basename (Qwen3.5-9B-Q4_K_M.gguf
    # rather than the friendly "qwen3.5-9b" the template ships with).
    # This must run even while Hermes is disabled: the dashboard can re-enable
    # it later, and persisted /opt/data/config.yaml wins over the template.
    _hermes_tpl="${INSTALL_DIR}/extensions/services/hermes/cli-config.yaml.template"
    if [[ -f "$_hermes_tpl" ]]; then
            _hermes_base_url="${CONTAINER_LLM_URL%/}/v1"
            _hermes_model="$GGUF_FILE"
            $CLOUD_MODE && _hermes_model="default"
            _hermes_patcher="${INSTALL_DIR}/scripts/patch-hermes-config.py"
            if [[ -f "$_hermes_patcher" ]]; then
                if ! python3 "$_hermes_patcher" "$_hermes_tpl" \
                    --model "$_hermes_model" \
                    --base-url "$_hermes_base_url" \
                    --context-length "$MAX_CONTEXT" >>"$ODS_LOG_FILE" 2>&1; then
                    ai_err "Hermes template patch failed: ${_hermes_tpl}"
                    exit 1
                fi
            else
                sed -i '' \
                    -e "s|^  default: .*|  default: \"${_hermes_model}\"|" \
                    -e "s|^  base_url: .*|  base_url: \"${_hermes_base_url}\"|" \
                    -e "s|^  context_length: .*|  context_length: ${MAX_CONTEXT}|" \
                    -e "s|^    context_length: .*|    context_length: ${MAX_CONTEXT}|" \
                    "$_hermes_tpl"
            fi
            if ! grep -Fq "base_url: \"${_hermes_base_url}\"" "$_hermes_tpl" \
               || ! grep -Fq "  default: \"${_hermes_model}\"" "$_hermes_tpl" \
               || ! grep -q "^  context_length: ${MAX_CONTEXT}$" "$_hermes_tpl"; then
                ai_err "Hermes template verification failed: ${_hermes_tpl}"
                exit 1
            fi

            _hermes_prepatch_rc=0
            _macos_patch_hermes_persisted_config \
                "$_hermes_model" "$_hermes_base_url" "$MAX_CONTEXT" \
                >>"$ODS_LOG_FILE" 2>&1 || _hermes_prepatch_rc=$?
            case "$_hermes_prepatch_rc" in
                0) ai_ok "Updated persisted Hermes routing before service transition" ;;
                3) log "No persisted Hermes config was available before compose" ;;
                4) ai_err "Persisted Hermes routing exists, but no local Python service image can patch it safely"; exit 1 ;;
                *) ai_err "Could not update persisted Hermes config before transition"; exit 1 ;;
            esac
            unset _hermes_prepatch_rc

            # Render data/persona/SOUL.md = static persona + dynamic install
            # context. The Hermes compose mounts this file as the agent's
            # SOUL.md so it answers truthfully about what services + hardware
            # it has on this ODS. MUST run before docker compose up,
            # otherwise Docker's bind-mount engine auto-creates the source
            # path as a *directory* and the install fails at compose-up with
            # "not a directory: Are you trying to mount a directory onto a
            # file" — which then persists across reinstalls because `nuke
            # install dir` preserves data/.
            if $ENABLE_HERMES; then
                _soul_builder="${INSTALL_DIR}/scripts/build-installation-context.py"
                if [[ -f "$_soul_builder" ]]; then
                    python3 "$_soul_builder" >>"$ODS_LOG_FILE" 2>&1 || \
                        ai_warn "Could not generate Hermes installation-context SOUL.md (non-fatal — Hermes will use the template default)"
                fi
            fi
            ai_ok "Prepared Hermes routing for macOS (model=${_hermes_model}, context=${MAX_CONTEXT}, base_url=${_hermes_base_url})"
    fi

    # Cloud transitions must retire every native process owned by this install
    # before local bridge and compose routing are removed.
    if $CLOUD_MODE; then
        _macos_stop_install_owned_native_llama \
            "Stopping install-owned native inference for cloud mode..."
    fi

    # ── Download and start native llama-server (Metal) ──
    if ! $CLOUD_MODE; then
        chapter "NATIVE LLAMA-SERVER (METAL)"

        # Download llama.cpp Metal build
        LLAMA_ZIP="/tmp/${LLAMA_CPP_MACOS_ASSET}"
        if [[ ! -x "$LLAMA_SERVER_BIN" ]]; then
            if [[ ! -f "$LLAMA_ZIP" ]]; then
                download_with_progress "$LLAMA_CPP_MACOS_URL" "$LLAMA_ZIP" \
                    "Downloading llama-server (Metal)" || {

                    # Fallback: try Homebrew
                    ai_warn "Pre-built binary download failed. Trying Homebrew..."
                    if command -v brew >/dev/null 2>&1; then
                        brew install llama.cpp 2>&1 | tail -5
                        BREW_LLAMA=$(command -v llama-server 2>/dev/null || true)
                        if [[ -n "$BREW_LLAMA" ]]; then
                            mkdir -p "$LLAMA_SERVER_DIR"
                            cp "$BREW_LLAMA" "$LLAMA_SERVER_BIN"
                            chmod +x "$LLAMA_SERVER_BIN"
                            ai_ok "Installed llama-server via Homebrew"
                        else
                            ai_err "Could not install llama-server. Install manually:"
                            ai "  brew install llama.cpp"
                            exit 1
                        fi
                    else
                        ai_err "llama-server download failed and Homebrew not available."
                        ai "Install Homebrew: https://brew.sh"
                        ai "Then: brew install llama.cpp"
                        exit 1
                    fi
                }
            fi

            if [[ -f "$LLAMA_ZIP" ]] && [[ ! -x "$LLAMA_SERVER_BIN" ]]; then
                # Extract
                ai "Extracting llama-server..."
                mkdir -p "$LLAMA_SERVER_DIR"
                TEMP_EXTRACT="/tmp/llama-extract-$$"
                mkdir -p "$TEMP_EXTRACT"
                # Format-aware extraction (handles .tar.gz and .zip)
                if [[ "$LLAMA_ZIP" == *.tar.gz ]] || [[ "$LLAMA_ZIP" == *.tgz ]]; then
                    tar xzf "$LLAMA_ZIP" -C "$TEMP_EXTRACT"
                else
                    unzip -o -q "$LLAMA_ZIP" -d "$TEMP_EXTRACT"
                fi

                # Find llama-server binary (may be in a subdirectory)
                FOUND_BIN=$(find "$TEMP_EXTRACT" -name "llama-server" -type f -print -quit)
                if [[ -n "$FOUND_BIN" ]]; then
                    cp "$FOUND_BIN" "$LLAMA_SERVER_BIN"
                    chmod +x "$LLAMA_SERVER_BIN"

                    # Also copy any companion dylibs and Metal libraries
                    FOUND_DIR=$(dirname "$FOUND_BIN")
                    find "$FOUND_DIR" -name "*.dylib" -exec cp {} "$LLAMA_SERVER_DIR/" \; 2>/dev/null || true
                    find "$FOUND_DIR" -name "*.metal" -exec cp {} "$LLAMA_SERVER_DIR/" \; 2>/dev/null || true

                    ai_ok "Extracted llama-server"
                else
                    ai_err "llama-server binary not found in archive."
                    ai "Try: brew install llama.cpp"
                    rm -rf "$TEMP_EXTRACT"
                    exit 1
                fi
                rm -rf "$TEMP_EXTRACT"
            fi

            # Remove quarantine attribute (macOS Gatekeeper)
            xattr -rd com.apple.quarantine "$LLAMA_SERVER_BIN" 2>/dev/null || true
            xattr -rd com.apple.quarantine "$LLAMA_SERVER_DIR"/*.dylib 2>/dev/null || true
        else
            ai_ok "llama-server already present"
        fi

        # Start native llama-server with Metal
        ai "Starting native llama-server (Metal)..."
        MODEL_FULL_PATH="${INSTALL_DIR}/data/models/${GGUF_FILE}"

        mkdir -p "$(dirname "$LLAMA_SERVER_PID_FILE")"

        _macos_stop_install_owned_native_llama \
            "Stopping prior install-owned native inference before replacement..."

        # Read reasoning mode from .env (default off to prevent thinking models
        # from consuming the entire token budget on internal reasoning)
        _reasoning=$(grep '^LLAMA_REASONING=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2 || echo "")
        [[ -z "$_reasoning" ]] && _reasoning="off"
        # Map .env values (off/on/auto) to llama-server --reasoning-format values
        case "$_reasoning" in
            off)  _reasoning_fmt="none" ;;
            on)   _reasoning_fmt="deepseek" ;;
            *)    _reasoning_fmt="$_reasoning" ;;
        esac

        # Honour the unified BIND_ADDRESS knob (PR #964) so --lan / dashboard
        # toggle / manual edit reach the native llama-server too. Falls back
        # to loopback when unset (default-secure).
        _bind=$(grep '^BIND_ADDRESS=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
        [[ -z "$_bind" ]] && _bind="127.0.0.1"
        _native_llama_port=$(read_env_value "$INSTALL_DIR/.env" "ODS_NATIVE_LLAMA_PORT")
        [[ "$_native_llama_port" =~ ^[0-9]+$ ]] || _native_llama_port="8080"
        _llama_probe_host="$(macos_bind_probe_host "$_bind")"

        _flash_attn=$(grep '^LLAMA_ARG_FLASH_ATTN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
        _cache_type_k=$(grep '^LLAMA_ARG_CACHE_TYPE_K=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
        _cache_type_v=$(grep '^LLAMA_ARG_CACHE_TYPE_V=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
        _n_cpu_moe=$(grep '^LLAMA_ARG_N_CPU_MOE=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
        _spec_type=$(grep '^LLAMA_ARG_SPEC_TYPE=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
        _spec_draft_n_max=$(grep '^LLAMA_ARG_SPEC_DRAFT_N_MAX=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
        _llama_args=(
            --host "$_bind" --port "$_native_llama_port"
            --model "$MODEL_FULL_PATH"
            --ctx-size "$MAX_CONTEXT"
            --n-gpu-layers 999
            --reasoning-format "$_reasoning_fmt"
            --metrics
        )
        [[ -n "$_flash_attn" ]] && _llama_args+=(--flash-attn "$_flash_attn")
        [[ -n "$_cache_type_k" ]] && _llama_args+=(--cache-type-k "$_cache_type_k")
        [[ -n "$_cache_type_v" ]] && _llama_args+=(--cache-type-v "$_cache_type_v")
        [[ -n "$_n_cpu_moe" ]] && _llama_args+=(--n-cpu-moe "$_n_cpu_moe")
        [[ -n "$_spec_type" ]] && _llama_args+=(--spec-type "$_spec_type")
        [[ -n "$_spec_draft_n_max" ]] && _llama_args+=(--spec-draft-n-max "$_spec_draft_n_max")

        (
            cd "$INSTALL_DIR" || exit 1
            exec "$LLAMA_SERVER_BIN" "${_llama_args[@]}"
        ) > "$LLAMA_SERVER_LOG" 2>&1 &
        LLAMA_PID=$!
        echo "$LLAMA_PID" > "$LLAMA_SERVER_PID_FILE"

        # Wait for health endpoint
        ai "Waiting for llama-server to load model..."
        MAX_WAIT=180
        WAITED=0
        HEALTHY=false
        while [[ "$WAITED" -lt "$MAX_WAIT" ]]; do
            sleep 2
            WAITED=$((WAITED + 2))
            if curl -sf --max-time 10 "http://${_llama_probe_host}:${_native_llama_port}/health" >/dev/null 2>&1; then
                HEALTHY=true
                break
            fi
            # Check if process died
            if ! kill -0 "$LLAMA_PID" 2>/dev/null; then
                ai_err "llama-server process died. Check logs:"
                ai "  tail -50 ${LLAMA_SERVER_LOG}"
                exit 1
            fi
            if (( WAITED % 10 == 0 )); then
                ai "  Still loading... (${WAITED}s)"
            fi
        done

        if $HEALTHY; then
            ai_ok "Native llama-server healthy (PID ${LLAMA_PID})"

            # ── Pre-warm the LLM slot ──
            # /health returning 200 only means the model is mmap'd. The
            # FIRST chat completion still has to materialize KV cache and
            # JIT compile fused kernels — during that, llama-server 503s
            # concurrent requests. Hermes Agent's default 3-retry / 120s
            # budget burns out on this on slower hardware, so we force
            # the slot through cold path here while we're already in the
            # "this may take a minute" install context. Bounded by curl
            # --max-time so a stalled llama-server can't hang the install.
            _prewarm_url="http://${_llama_probe_host}:${_native_llama_port}/v1/chat/completions"
            _prewarm_model="${GGUF_FILE:-default}"
            _prewarm_body="{\"model\":\"${_prewarm_model}\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":1,\"temperature\":0,\"stream\":false}"
            if curl -sf --max-time 120 -X POST "$_prewarm_url" \
                -H "Content-Type: application/json" \
                -d "$_prewarm_body" >/dev/null 2>&1; then
                ai_ok "LLM slot pre-warmed (first real chat will be fast)"
            else
                ai_warn "LLM pre-warm timed out — first Hermes prompt may need a retry while the slot warms."
            fi
        else
            ai_warn "llama-server did not become healthy within ${MAX_WAIT}s. It may still be loading."
        fi
    fi

    # ── Assemble Docker Compose flags ──
    COMPOSE_FLAGS=("-f" "docker-compose.base.yml")

    MACOS_CLOUD_AUTH_OVERLAY=""
    if $CLOUD_MODE; then
        # Cloud mode has no native llama process or macOS readiness sidecar.
        COMPOSE_FLAGS+=("-f" "docker-compose.cloud.yml")
        MACOS_CLOUD_AUTH_OVERLAY="data/generated/docker-compose.macos-cloud-auth.yml"
        _write_macos_cloud_auth_overlay "${INSTALL_DIR}/${MACOS_CLOUD_AUTH_OVERLAY}"
    else
        # Normal macOS mode: native llama-server
        COMPOSE_FLAGS+=("-f" "installers/macos/docker-compose.macos.yml")
    fi

    # Discover enabled extension compose fragments via manifests
    EXT_DIR="${INSTALL_DIR}/extensions/services"
    CURRENT_BACKEND="apple"
    $CLOUD_MODE && CURRENT_BACKEND="none"
    if [[ -d "$EXT_DIR" ]]; then
        for SVC_DIR in "$EXT_DIR"/*/; do
            [[ ! -d "$SVC_DIR" ]] && continue
            SVC_NAME=$(basename "$SVC_DIR")

            # Read manifest
            MANIFEST_PATH="${SVC_DIR}manifest.yaml"
            [[ ! -f "$MANIFEST_PATH" ]] && MANIFEST_PATH="${SVC_DIR}manifest.yml"
            [[ ! -f "$MANIFEST_PATH" ]] && continue

            # Quick manifest validation: must contain schema_version: ods.services.v1
            if ! grep -q "schema_version:.*ods\.services\.v1" "$MANIFEST_PATH" 2>/dev/null; then
                continue
            fi

            # Check gpu_backends compatibility
            BACKENDS_LINE=$(grep "gpu_backends:" "$MANIFEST_PATH" 2>/dev/null || true)
            if [[ -n "$BACKENDS_LINE" ]] && [[ "$CURRENT_BACKEND" != "none" ]]; then
                if ! echo "$BACKENDS_LINE" | grep -qE "(${CURRENT_BACKEND}|all)" 2>/dev/null; then
                    # Check if "apple" is not listed but service works on CPU
                    # Extension services like whisper, tts work on CPU in Docker
                    # Allow if gpu_backends contains "amd" or "nvidia" (CPU fallback)
                    if ! echo "$BACKENDS_LINE" | grep -qE "(amd|nvidia)" 2>/dev/null; then
                        continue
                    fi
                fi
            fi

            # Find compose file
            COMPOSE_FILE="compose.yaml"
            COMPOSE_REF=$(grep "compose_file:" "$MANIFEST_PATH" 2>/dev/null | awk -F: '{print $2}' | tr -d ' "'"'" || true)
            [[ -n "$COMPOSE_REF" ]] && COMPOSE_FILE="$COMPOSE_REF"

            COMPOSE_PATH="${SVC_DIR}${COMPOSE_FILE}"
            [[ ! -f "$COMPOSE_PATH" ]] && continue

            # Check feature flags
            SKIP=false
            case "$SVC_NAME" in
                litellm|searxng|token-spy) $ENABLE_RECOMMENDED || SKIP=true ;;
                whisper|tts)   $ENABLE_VOICE || SKIP=true ;;
                n8n)           $ENABLE_WORKFLOWS || SKIP=true ;;
                qdrant|embeddings) $ENABLE_RAG || SKIP=true ;;
                hermes|hermes-proxy) $ENABLE_HERMES || SKIP=true ;;
                openclaw)      $ENABLE_OPENCLAW || SKIP=true ;;
                ape)           $ENABLE_APE || SKIP=true ;;
                perplexica)    $ENABLE_PERPLEXICA || SKIP=true ;;
                privacy-shield) $ENABLE_PRIVACY_SHIELD || SKIP=true ;;
                ods-proxy)   $ENABLE_ODS_PROXY || SKIP=true ;;
                tailscale)     $ENABLE_TAILSCALE || SKIP=true ;;
                langfuse)      $ENABLE_LANGFUSE || SKIP=true ;;
                brave-search)  [[ "${ENABLE_BRAVE_SEARCH:-false}" == "true" ]] || SKIP=true ;;
            esac
            $SKIP && continue

            REL_PATH="${COMPOSE_PATH#"${INSTALL_DIR}/"}"
            COMPOSE_FLAGS+=("-f" "$REL_PATH")

            # GPU-backend overlay (mirrors resolve-compose-stack.sh discovery).
            # E.g. extensions/services/litellm/compose.apple.yaml on macOS.
            # Skipped in cloud mode (CURRENT_BACKEND=none) since no native
            # GPU/host-gateway patches apply when llama-server runs remotely.
            if [[ "$CURRENT_BACKEND" != "none" ]]; then
                GPU_OVERLAY_PATH="${SVC_DIR}compose.${CURRENT_BACKEND}.yaml"
                if [[ -f "$GPU_OVERLAY_PATH" ]]; then
                    COMPOSE_FLAGS+=("-f" "${GPU_OVERLAY_PATH#"${INSTALL_DIR}/"}")
                fi
            fi
        done
    fi

    # Layer Tier 0 memory overlay for low-RAM machines
    if [[ "$SELECTED_TIER" == "0" && -f "${INSTALL_DIR}/docker-compose.tier0.yml" ]]; then
        COMPOSE_FLAGS+=("-f" "docker-compose.tier0.yml")
        ai "Applying lightweight memory limits for Tier 0"
    fi

    # Docker compose override (user customizations)
    if [[ -f "${INSTALL_DIR}/docker-compose.override.yml" ]]; then
        COMPOSE_FLAGS+=("-f" "docker-compose.override.yml")
    fi

    # Keep this last so cloud clients always authenticate with the same
    # LITELLM_KEY that configures LiteLLM's LITELLM_MASTER_KEY. The generated
    # file contains only variable references, never the secret itself.
    if [[ -n "$MACOS_CLOUD_AUTH_OVERLAY" ]]; then
        COMPOSE_FLAGS+=("-f" "$MACOS_CLOUD_AUTH_OVERLAY")
    fi

    # ── Validate compose files exist before launching ──
    for ((i=0; i<${#COMPOSE_FLAGS[@]}; i++)); do
        if [[ "${COMPOSE_FLAGS[$i]}" == "-f" ]] && (( i+1 < ${#COMPOSE_FLAGS[@]} )); then
            CF="${COMPOSE_FLAGS[$((i+1))]}"
            if [[ ! -f "$CF" ]]; then
                ai_err "Compose file not found: ${CF}"
                ai "The source tree may not have copied correctly. Try re-running with --force."
                exit 1
            fi
        fi
    done

    # ── Unload stale LaunchAgents before compose (crash-safe) ──
    # If a previous install registered these agents and this run fails at
    # compose-up, the old agents would keep running with stale paths
    # (ODS_HOME pointing at the deleted install dir).  Clearing them
    # here guarantees a clean slate regardless of what happens below.
    launchctl bootout "gui/$(id -u)/${ODS_AGENT_PLIST_LABEL}" 2>/dev/null || true
    launchctl bootout "gui/$(id -u)/${HOST_AGENT_BRIDGE_PLIST_LABEL}" 2>/dev/null || true
    rm -f "$HOST_AGENT_BRIDGE_PLIST" 2>/dev/null || true
    launchctl bootout "gui/$(id -u)/${OPENCODE_PLIST_LABEL}" 2>/dev/null || true
    for _legacy_plist_label in \
        com.ods.llama-server \
        com.ods.full-model-download; do
        launchctl bootout "gui/$(id -u)/${_legacy_plist_label}" 2>/dev/null || true
        rm -f "$HOME/Library/LaunchAgents/${_legacy_plist_label}.plist" 2>/dev/null || true
    done
    unset _legacy_plist_label

    # ── Start Docker services ──
    chapter "STARTING SERVICES"

    _macos_compose_up_args=(up -d --remove-orphans --no-build --pull never)

    _macos_is_local_image() {
        local image="${1:-}"
        case "$image" in
            ""|ods-*|ods-*:*|docker.io/library/ods-*|localhost/*|localhost:*/*|127.0.0.1:*/*)
                return 0
                ;;
        esac
        return 1
    }

    _macos_compose_external_images() {
        local config_json
        if config_json="$(docker compose "${COMPOSE_FLAGS[@]}" config --format json 2>>"$ODS_LOG_FILE")"; then
            if printf '%s' "$config_json" | python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)

for service in (data.get("services") or {}).values():
    if service.get("build") is not None:
        continue
    image = str(service.get("image") or "").strip()
    if image:
        print(image)
' | while IFS= read -r _image; do
                _macos_is_local_image "$_image" && continue
                printf '%s\n' "$_image"
            done | awk '!seen[$0]++'; then
                return 0
            fi
        fi

        docker compose "${COMPOSE_FLAGS[@]}" config --images 2>>"$ODS_LOG_FILE" | while IFS= read -r _image; do
            _macos_is_local_image "$_image" && continue
            printf '%s\n' "$_image"
        done | awk '!seen[$0]++'
    }

    _macos_pull_image_with_retry() {
        local image="$1" attempt max_attempts delay
        local -a delays=(5 15 30)

        if docker image inspect "$image" >/dev/null 2>&1; then
            log "Compose image already cached: $image"
            return 0
        fi

        max_attempts="${ODS_DOCKER_PULL_MAX_ATTEMPTS:-4}"
        for ((attempt=1; attempt<=max_attempts; attempt++)); do
            ai "Pulling Compose image ($attempt/$max_attempts): $image"
            if docker pull "$image" >>"$ODS_LOG_FILE" 2>&1; then
                ai_ok "Pulled $image"
                return 0
            fi
            if (( attempt < max_attempts )); then
                delay="${delays[$((attempt - 1))]:-30}"
                ai_warn "Pull failed for $image; retrying in ${delay}s"
                sleep "$delay"
            fi
        done

        ai_err "Failed to pull Compose image after retries: $image"
        return 1
    }

    _macos_pre_pull_compose_images() {
        local image_output image failed
        image_output="$(_macos_compose_external_images)" || {
            ai_err "Could not resolve macOS Docker Compose images before service launch"
            ai "Inspect compose config with: cd '$INSTALL_DIR' && docker compose ${COMPOSE_FLAGS[*]} config --images"
            return 1
        }
        [[ -n "$image_output" ]] || return 0

        ai "Verifying Compose image cache before launch..."
        failed=0
        while IFS= read -r image; do
            [[ -n "$image" ]] || continue
            _macos_pull_image_with_retry "$image" || failed=$((failed + 1))
        done <<< "$image_output"

        if [[ "$failed" -eq 0 ]]; then
            ai_ok "Compose image cache ready"
            return 0
        fi

        ai_err "$failed Compose image(s) could not be pulled before launch"
        ai "macOS installer will not allow Docker Compose to pull images implicitly."
        ai "Fix Docker registry/network/disk access, then re-run ./installers/macos/install-macos.sh."
        return 1
    }

    # ── Rebuild local-built images ─────────────────────────────────────
    # Mirrors phases/11-services.sh on Linux: local Dockerfiles can drift from
    # baked images, so rebuild the local services that are actually present in
    # the resolved macOS compose stack. Optional services such as
    # privacy-shield may be disabled by feature flags; building them anyway can
    # surface unrelated Dockerfile failures and make a healthy selected stack
    # look broken.
    ai "Rebuilding local-built images..."
    _macos_candidate_build_services=(dashboard dashboard-api model-router ape token-spy privacy-shield brave-search)
    if ! _macos_enabled_services="$(docker compose "${COMPOSE_FLAGS[@]}" config --services 2>>"$ODS_LOG_FILE")"; then
        ai_err "Could not resolve macOS compose services for local image rebuilds."
        ai "Inspect compose config with: cd '$INSTALL_DIR' && docker compose ${COMPOSE_FLAGS[*]} config --services"
        exit 1
    fi
    _macos_build_services=()
    for _svc in "${_macos_candidate_build_services[@]}"; do
        if printf '%s\n' "$_macos_enabled_services" | grep -qx "$_svc"; then
            _macos_build_services+=("$_svc")
        else
            log "Skipping local image rebuild for disabled service: $_svc"
        fi
    done

    _macos_build_max_attempts="${ODS_DOCKER_BUILD_MAX_ATTEMPTS:-3}"
    [[ "$_macos_build_max_attempts" =~ ^[1-9][0-9]*$ ]] || _macos_build_max_attempts=3
    _macos_build_failed=0
    _macos_build_delays=(5 15 30)
    for _svc in "${_macos_build_services[@]}"; do
        _macos_build_ok=false
        for ((_attempt=1; _attempt<=_macos_build_max_attempts; _attempt++)); do
            ai "Building local image: ${_svc} (${_attempt}/${_macos_build_max_attempts})"
            if docker compose "${COMPOSE_FLAGS[@]}" build "$_svc" >> "$ODS_LOG_FILE" 2>&1; then
                _macos_build_ok=true
                ai_ok "Built local image: ${_svc}"
                break
            fi
            if (( _attempt < _macos_build_max_attempts )); then
                _delay="${_macos_build_delays[$((_attempt - 1))]:-30}"
                ai_warn "Build failed for ${_svc}; retrying in ${_delay}s (see $ODS_LOG_FILE)"
                sleep "$_delay"
            fi
        done
        if [[ "$_macos_build_ok" != "true" ]]; then
            ai_err "Build failed for required local service ${_svc} after ${_macos_build_max_attempts} attempts (see $ODS_LOG_FILE)"
            _macos_build_failed=$((_macos_build_failed + 1))
        fi
    done
    if [[ "$_macos_build_failed" -ne 0 ]]; then
        ai_err "${_macos_build_failed} required local image(s) failed to build; refusing to launch stale images."
        exit 1
    fi
    ai_ok "Local images rebuilt"

    mkdir -p "${INSTALL_DIR}/logs"
    _compose_up_log="${INSTALL_DIR}/logs/compose-up.log"
    : > "$_compose_up_log"

    if ! _macos_pre_pull_compose_images; then
        if command -v write_compose_failure_report >/dev/null 2>&1; then
            _compose_report_path="$(COMPOSE_FLAGS_REPORT="${COMPOSE_FLAGS[*]}" write_compose_failure_report \
                "$INSTALL_DIR" \
                "install-macos compose image preflight" \
                "docker compose ${COMPOSE_FLAGS[*]} ${_macos_compose_up_args[*]}" \
                "$_compose_up_log" \
                "apple" \
                "A required Compose image did not download during the retry-protected preflight. Fix Docker registry/network/disk access, then re-run ./installers/macos/install-macos.sh." |
                tail -n 1)" || true
            [[ -n "${_compose_report_path:-}" ]] && ai_warn "Compose failure report saved: $_compose_report_path"
        fi
        exit 1
    fi

    _compose_launch_record="${INSTALL_DIR}/logs/compose-launch.txt"
    {
        printf 'timestamp=%s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
        printf 'cwd=%s\n' "$INSTALL_DIR"
        printf 'compose_command=docker compose %s %s\n' "${COMPOSE_FLAGS[*]}" "${_macos_compose_up_args[*]}"
        printf 'compose_flags=%s\n' "${COMPOSE_FLAGS[*]}"
        printf 'compose_flags_file=%s\n' "$INSTALL_DIR/.compose-flags"
        printf "compose_ps_command=cd '%s' && docker compose %s ps -a\n" "$INSTALL_DIR" "${COMPOSE_FLAGS[*]}"
        printf "compose_logs_command=cd '%s' && docker compose %s logs --tail 200\n" "$INSTALL_DIR" "${COMPOSE_FLAGS[*]}"
        printf 'compose_files=\n'
        _expect_file=false
        for _arg in "${COMPOSE_FLAGS[@]}"; do
            if $_expect_file; then
                printf '  - %s\n' "$_arg"
                _expect_file=false
            elif [[ "$_arg" == "-f" ]]; then
                _expect_file=true
            fi
        done
    } > "$_compose_launch_record"
    ai "Running: docker compose ${COMPOSE_FLAGS[*]} ${_macos_compose_up_args[*]}"
    set +o pipefail  # pipefail would abort on compose exit before PIPESTATUS is read; capture it first
    docker compose "${COMPOSE_FLAGS[@]}" "${_macos_compose_up_args[@]}" 2>&1 | tee -a "$_compose_up_log" | while IFS= read -r line; do
        echo "  $line"
    done
    compose_exit="${PIPESTATUS[0]}"
    set -o pipefail

    if [[ "$compose_exit" -ne 0 ]]; then
        if command -v write_compose_failure_report >/dev/null 2>&1; then
            _compose_report_path="$(COMPOSE_FLAGS_REPORT="${COMPOSE_FLAGS[*]}" write_compose_failure_report \
                "$INSTALL_DIR" \
                "install-macos docker compose up" \
                "docker compose ${COMPOSE_FLAGS[*]} ${_macos_compose_up_args[*]}" \
                "$_compose_up_log" \
                "apple" \
                "Open the saved report, fix the failed image/port/compose error it identifies, then re-run ./installers/macos.sh." |
                tail -n 1)" || true
            [[ -n "${_compose_report_path:-}" ]] && ai_warn "Compose failure report saved: $_compose_report_path"
        fi
        ai_err "docker compose up failed"
        exit 1
    fi
    _compose_container_ids="$(docker compose "${COMPOSE_FLAGS[@]}" ps -q 2>>"$ODS_LOG_FILE" || true)"
    _compose_container_count="$(printf '%s\n' "$_compose_container_ids" | sed '/^[[:space:]]*$/d' | wc -l | tr -d '[:space:]')"
    if [[ "${_compose_container_count:-0}" -eq 0 ]]; then
        if command -v write_compose_failure_report >/dev/null 2>&1; then
            _compose_report_path="$(COMPOSE_FLAGS_REPORT="${COMPOSE_FLAGS[*]}" write_compose_failure_report \
                "$INSTALL_DIR" \
                "install-macos zero managed containers" \
                "docker compose ${COMPOSE_FLAGS[*]} ${_macos_compose_up_args[*]}" \
                "$_compose_up_log" \
                "apple" \
                "No ODS containers were created. Run the saved ps/logs commands from logs/compose-launch.txt, fix the compose/runtime failure, then re-run ./installers/macos.sh." |
                tail -n 1)" || true
            [[ -n "${_compose_report_path:-}" ]] && ai_warn "Compose failure report saved: $_compose_report_path"
        fi
        ai_err "docker compose up completed but created no managed containers"
        ai "Launch record: $_compose_launch_record"
        ai "Inspect with: cd '$INSTALL_DIR' && docker compose ${COMPOSE_FLAGS[*]} ps -a"
        exit 1
    fi
    ai_ok "Docker services started"

    if $ENABLE_HERMES; then
        _hermes_running=false
        for _hermes_wait_i in $(seq 1 90); do
            if [[ "$(docker inspect --format '{{.State.Status}}' ods-hermes 2>/dev/null || true)" == "running" ]]; then
                _hermes_running=true
                break
            fi
            sleep 1
        done
        if [[ "$_hermes_running" != "true" ]]; then
            ai_err "Hermes was selected but its container did not reach running state."
            exit 1
        fi

        _hermes_live_verified=false
        for _hermes_wait_i in $(seq 1 90); do
            _hermes_patch_rc=0
            _macos_patch_hermes_persisted_config \
                "$_hermes_model" "$_hermes_base_url" "$MAX_CONTEXT" \
                >>"$ODS_LOG_FILE" 2>&1 || _hermes_patch_rc=$?
            if [[ "$_hermes_patch_rc" -eq 0 ]]; then
                _hermes_live_verified=true
                break
            fi
            [[ "$_hermes_patch_rc" -eq 3 ]] || break
            sleep 1
        done
        if [[ "$_hermes_live_verified" != "true" ]]; then
            ai_err "Could not authoritatively update and verify Hermes's persisted container-owned config."
            exit 1
        fi
        if ! docker restart ods-hermes >>"$ODS_LOG_FILE" 2>&1; then
            ai_err "Could not restart Hermes after updating its persisted routing."
            exit 1
        fi
        _hermes_restarted_healthy=false
        for _hermes_wait_i in $(seq 1 180); do
            if [[ "$(docker inspect --format '{{.State.Health.Status}}' ods-hermes 2>/dev/null || true)" == "healthy" ]]; then
                _hermes_restarted_healthy=true
                break
            fi
            sleep 1
        done
        if [[ "$_hermes_restarted_healthy" != "true" ]]; then
            ai_err "Hermes did not become healthy after applying persisted routing."
            exit 1
        fi
        _hermes_expected_key="$(read_env_value "$INSTALL_DIR/.env" "HERMES_LLM_API_KEY")"
        if ! _macos_verify_hermes_container_auth "$_hermes_expected_key" \
            >>"$ODS_LOG_FILE" 2>&1; then
            ai_err "Hermes container authentication does not match the configured inference gateway."
            exit 1
        fi
        ai_ok "Hermes persisted routing and authentication verified"
        unset _hermes_running _hermes_wait_i _hermes_patch_rc \
            _hermes_live_verified _hermes_restarted_healthy _hermes_expected_key
    fi

    # Refresh the generated Hermes persona now that the stack is actually
    # running, then copy it into Hermes's runtime data dir from inside the
    # container. This avoids Docker Desktop's nested bind-mount restriction
    # while still keeping /opt/data/SOUL.md current for new sessions.
    _soul_builder="${INSTALL_DIR}/scripts/build-installation-context.py"
    if [[ -f "$_soul_builder" ]]; then
        python3 "$_soul_builder" >>"$ODS_LOG_FILE" 2>&1 || \
            ai_warn "Could not refresh Hermes installation-context SOUL.md after compose-up"
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'ods-hermes'; then
            docker exec ods-hermes cp /opt/hermes/docker/SOUL.md /opt/data/SOUL.md \
                >>"$ODS_LOG_FILE" 2>&1 || \
                ai_warn "Could not sync installation-context SOUL.md into running Hermes container"
        fi
    fi

    # Save compose flags for ods-macos.sh
    echo "${COMPOSE_FLAGS[*]}" > "${INSTALL_DIR}/.compose-flags"

    # ── Launch background model upgrade ──────────────────────────────────
    if [[ "$_BOOTSTRAP_ACTIVE" == "true" ]]; then
        ai "Launching background download for $FULL_LLM_MODEL..."
        mkdir -p "$INSTALL_DIR/logs"
        _upgrade_script="$INSTALL_DIR/scripts/bootstrap-upgrade.sh"

        if [[ -x "$_upgrade_script" ]] || [[ -f "$_upgrade_script" ]]; then
            _macos_persist_bootstrap_upgrade_args \
                "$FULL_GGUF_FILE" "$FULL_GGUF_URL" "$FULL_GGUF_SHA256" \
                "$FULL_LLM_MODEL" "$FULL_MAX_CONTEXT" "$BOOTSTRAP_GGUF_FILE" || true
            # Start the long-lived downloader from a child shell after closing
            # inherited non-stdio FDs, matching the Linux installer contract.
            if ! (
                _close_inherited_fds_for_daemon
                _macos_launch_detached_bootstrap_upgrade "$_upgrade_script" \
                    "$INSTALL_DIR" "$FULL_GGUF_FILE" "$FULL_GGUF_URL" \
                    "$FULL_GGUF_SHA256" "$FULL_LLM_MODEL" "$FULL_MAX_CONTEXT" \
                    "$BOOTSTRAP_GGUF_FILE"
            ); then
                ai_err "Could not launch the isolated background model upgrade."
                exit 1
            fi
            ai "Full model ($FULL_LLM_MODEL) downloading in background."
            ai "Check progress: tail -f $INSTALL_DIR/logs/model-upgrade.log"
        else
            ai_warn "bootstrap-upgrade.sh not found. Download the full model manually."
        fi
    fi

    # ── Install & start OpenCode (native host binary) ──
    chapter "OPENCODE (AI CODING IDE)"

    _install_opencode

    # OpenCode is native, so cloud mode uses LiteLLM's published host port while
    # local mode follows the actual native llama bind and port.
    if [[ -n "$OPENCODE_BIN" && -x "$OPENCODE_BIN" ]]; then
        mkdir -p "$OPENCODE_CONFIG_DIR"
        _opencode_switchboard_mode="$(read_env_value "$INSTALL_DIR/.env" "ODS_MODEL_SWITCHBOARD")"
        if [[ "${_opencode_switchboard_mode:-observe}" == "enabled" ]]; then
            _opencode_model="ods/current"
            _opencode_port="$(read_env_value "$INSTALL_DIR/.env" "LITELLM_PORT")"
            [[ "$_opencode_port" =~ ^[0-9]+$ ]] || _opencode_port="4000"
            _opencode_bind="$(read_env_value "$INSTALL_DIR/.env" "BIND_ADDRESS")"
            _opencode_host="$(macos_bind_probe_host "${_opencode_bind:-127.0.0.1}")"
            _opencode_base_url="http://${_opencode_host}:${_opencode_port}/v1"
            _opencode_api_key="$(read_env_value "$INSTALL_DIR/.env" "LITELLM_KEY")"
        elif $CLOUD_MODE; then
            _opencode_model="default"
            _opencode_port="$(read_env_value "$INSTALL_DIR/.env" "LITELLM_PORT")"
            [[ "$_opencode_port" =~ ^[0-9]+$ ]] || _opencode_port="4000"
            _opencode_bind="$(read_env_value "$INSTALL_DIR/.env" "BIND_ADDRESS")"
            _opencode_host="$(macos_bind_probe_host "${_opencode_bind:-127.0.0.1}")"
            _opencode_base_url="http://${_opencode_host}:${_opencode_port}/v1"
            _opencode_api_key="$(read_env_value "$INSTALL_DIR/.env" "LITELLM_KEY")"
        else
            _opencode_model="$LLM_MODEL"
            _opencode_port="$(read_env_value "$INSTALL_DIR/.env" "ODS_NATIVE_LLAMA_PORT")"
            [[ "$_opencode_port" =~ ^[0-9]+$ ]] || _opencode_port="8080"
            _opencode_bind="$(read_env_value "$INSTALL_DIR/.env" "BIND_ADDRESS")"
            _opencode_host="$(macos_bind_probe_host "${_opencode_bind:-127.0.0.1}")"
            _opencode_base_url="http://${_opencode_host}:${_opencode_port}/v1"
            _opencode_api_key="no-key"
        fi
        if [[ -z "$_opencode_api_key" ]] \
           || ! _write_macos_opencode_config \
                "$OPENCODE_CONFIG_DIR/opencode.json" \
                "$_opencode_model" "$_opencode_base_url" "$_opencode_api_key" \
                "${MAX_CONTEXT:-32768}"; then
            ai_err "Could not configure OpenCode for the active inference route."
            exit 1
        fi
        ai_ok "OpenCode configured for ${_opencode_model} at ${_opencode_base_url}"
        unset _opencode_model _opencode_port _opencode_bind _opencode_host \
            _opencode_switchboard_mode \
            _opencode_base_url _opencode_api_key

        # Install as macOS LaunchAgent (auto-start on login).
        # Log path is intentionally decoupled from INSTALL_DIR: xpcproxy denies
        # file-write-create on non-$HOME volumes, which causes the launchd spawn
        # to exit 78 before the target process ever runs. $HOME/Library/Logs is
        # always inside xpcproxy's sandbox writable set, so use that instead.
        mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/ODS"
        OPENCODE_LAUNCHD_PATH="$(_compute_launchd_path "$(dirname "$OPENCODE_BIN")")"
        cat > "$OPENCODE_PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${OPENCODE_PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${OPENCODE_BIN}</string>
        <string>web</string>
        <string>--port</string>
        <string>3003</string>
        <string>--hostname</string>
        <string>127.0.0.1</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${INSTALL_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>${HOME}</string>
        <key>PATH</key>
        <string>${OPENCODE_LAUNCHD_PATH}</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>${HOME}/Library/Logs/ODS/opencode-web.log</string>
    <key>StandardErrorPath</key>
    <string>${HOME}/Library/Logs/ODS/opencode-web.log</string>
</dict>
</plist>
PLIST_EOF

        # Unload existing (if any) and load new plist. bootout legitimately
        # errors when no service is loaded, so we keep that suppressed; the
        # bootstrap call surfaces real failures (e.g. launchd throttle EIO).
        launchctl bootout "gui/$(id -u)/${OPENCODE_PLIST_LABEL}" >/dev/null 2>&1 || true
        _opencode_bootstrap_err="$(launchctl bootstrap "gui/$(id -u)" "$OPENCODE_PLIST" 2>&1)" && _opencode_bootstrap_rc=0 || _opencode_bootstrap_rc=$?
        if [[ $_opencode_bootstrap_rc -eq 0 ]]; then
            ai_ok "OpenCode Web UI service installed (LaunchAgent, port 3003)"
        else
            ai_warn "OpenCode LaunchAgent failed (rc=${_opencode_bootstrap_rc}): ${_opencode_bootstrap_err}"
            ai_warn "Start manually: ${OPENCODE_BIN} web --port 3003"
        fi
    fi
fi

# ── ODS Host Agent (extension lifecycle management) ──
if $DRY_RUN; then
    ai "[DRY RUN] Would install, configure, and verify the authenticated dashboard host-agent path"
else
AGENT_PYTHON="$(command -v python3)"
if [[ -f "${INSTALL_DIR}/bin/ods-host-agent.py" ]] && [[ -n "$AGENT_PYTHON" ]]; then
    # See opencode-web block above for the xpcproxy sandbox rationale behind
    # the $HOME-rooted log path.
    mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/ODS"
    ODS_AGENT_PATH="$(_compute_launchd_path "")"
    _agent_native_bind="$(read_env_value "$INSTALL_DIR/.env" "ODS_AGENT_BIND")"
    _agent_native_bind="$(macos_normalize_agent_bind "${_agent_native_bind:-127.0.0.1}")"
    _agent_probe_host="$(macos_bind_probe_host "$_agent_native_bind")"
    if ! command -v docker >/dev/null 2>&1; then
        ai_warn "docker not found on PATH at install time — host agent will fail to start until Docker Desktop is launched and 'docker' resolves on your shell PATH"
    fi
    if ! "$AGENT_PYTHON" -c "import huggingface_hub, hf_xet" >/dev/null 2>&1; then
        ai "Installing ODS host-agent model downloader dependencies..."
        if "$AGENT_PYTHON" -m pip install --user -q "huggingface_hub[hf_xet]>=0.27" 2>&1 | tee -a "$ODS_LOG_FILE" >/dev/null; then
            ai_ok "ODS host-agent Hugging Face downloader ready"
        else
            ai_warn "Could not install huggingface_hub[hf_xet]; model manager downloads may fail on Xet-backed Hugging Face models."
        fi
    fi
    cat > "$ODS_AGENT_PLIST" <<AGENT_PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${ODS_AGENT_PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${AGENT_PYTHON}</string>
        <string>${INSTALL_DIR}/bin/ods-host-agent.py</string>
        <string>--install-dir</string>
        <string>${INSTALL_DIR}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${INSTALL_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>ODS_HOME</key>
        <string>${INSTALL_DIR}</string>
        <key>HOME</key>
        <string>${HOME}</string>
        <key>PATH</key>
        <string>${ODS_AGENT_PATH}</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>${HOME}/Library/Logs/ODS/ods-host-agent.log</string>
    <key>StandardErrorPath</key>
    <string>${HOME}/Library/Logs/ODS/ods-host-agent.log</string>
</dict>
</plist>
AGENT_PLIST_EOF

    launchctl bootout "gui/$(id -u)/${ODS_AGENT_PLIST_LABEL}" >/dev/null 2>&1 || true
    _agent_bootstrap_err="$(launchctl bootstrap "gui/$(id -u)" "$ODS_AGENT_PLIST" 2>&1)" && _agent_bootstrap_rc=0 || _agent_bootstrap_rc=$?
    if [[ $_agent_bootstrap_rc -eq 0 ]]; then
        # `launchctl bootstrap` can succeed (definition loaded) while launchd
        # leaves the service in "pended nondemand spawn = speculative" and
        # never actually launches the process — common right after a
        # same-session bootout because the throttler hasn't reset yet, and
        # `RunAtLoad=true` doesn't override the throttle. Force the spawn
        # with `kickstart`, then poll /health so we don't report success
        # while the agent is still down. Without this verification the
        # dashboard-api will hit "Host agent unreachable" on every model and
        # extension action even though the installer printed [OK].
        launchctl kickstart -p "gui/$(id -u)/${ODS_AGENT_PLIST_LABEL}" >/dev/null 2>&1 || true
        _agent_health_ok=false
        for _agent_health_i in 1 2 3 4 5 6 7 8 9 10; do
            if curl -fsS --max-time 1 "http://${_agent_probe_host}:${ODS_AGENT_PORT}/health" >/dev/null 2>&1; then
                _agent_health_ok=true
                break
            fi
            sleep 1
        done
        if [[ "$_agent_health_ok" == "true" ]]; then
            ai_ok "ODS host agent installed (LaunchAgent, port ${ODS_AGENT_PORT})"
        else
            ai_warn "ODS host agent loaded but not responding on :${ODS_AGENT_PORT} after 10s."
            ai_warn "  Log:         tail -F ~/Library/Logs/ODS/ods-host-agent.log"
            ai_warn "  Force start: launchctl kickstart -p gui/\$(id -u)/${ODS_AGENT_PLIST_LABEL}"
            ai_warn "  Dashboard model + extension actions will fail until the agent comes up."
        fi
    else
        ai_warn "ODS host agent LaunchAgent failed (rc=${_agent_bootstrap_rc}): ${_agent_bootstrap_err}"
        if [[ "${_agent_bootstrap_err}" == *"Input/output error"* ]]; then
            ai_warn "launchd is throttled. Recover with: launchctl bootout gui/\$(id -u)/${ODS_AGENT_PLIST_LABEL}; sleep 10; then re-run this installer"
        else
            ai_warn "Start manually: ods agent start"
        fi
    fi
else
    [[ ! -f "${INSTALL_DIR}/bin/ods-host-agent.py" ]] && ai_warn "Host agent script not found, skipping"
    [[ -z "$AGENT_PYTHON" ]] && ai_warn "python3 not found, host agent not installed"
fi

if ! _configure_macos_host_agent_bridge; then
    exit 1
fi
if ! _verify_macos_dashboard_host_agent "$INSTALL_DIR/.env"; then
    exit 1
fi
fi

# ============================================================================
# PHASE 6 -- VERIFICATION
# ============================================================================
show_phase 6 6 "VERIFICATION" "30 seconds"

if $DRY_RUN; then
    ai "[DRY RUN] Would health-check all services"
    ai "[DRY RUN] Would auto-configure Perplexica for ${LLM_MODEL}"
    ai "[DRY RUN] Install validation complete"
    ai_ok "Dry run finished -- no changes made"
    exit 0
fi

# Health check loop
ai "Running health checks..."
MAX_ATTEMPTS=90   # 90 * 2s = 180s -- covers base compose start_period (60s) + image pull
ALL_HEALTHY=true
CLOUD_REQUIRED_HEALTHY=true

# Parallel arrays (Bash 3.2 compatible -- no associative arrays).
# HEALTH_CONTAINERS holds the Docker container name when a service runs in
# Docker; an empty string means the service is host-native (llama-server
# runs natively on macOS via Metal; OpenCode is a LaunchAgent). Docker
# services wait on `docker inspect ... .State.Health.Status == healthy`;
# host-native services fall back to an HTTP probe on 127.0.0.1.
if $CLOUD_MODE; then
    HEALTH_NAMES=("LiteLLM gateway" "Chat UI (Open WebUI)")
    HEALTH_URLS=("http://127.0.0.1:4000/health/readiness" "http://127.0.0.1:3000")
    HEALTH_CONTAINERS=("ods-litellm" "ods-webui")
else
    _health_bind="$(read_env_value "$INSTALL_DIR/.env" "BIND_ADDRESS")"
    _health_llama_host="$(macos_bind_probe_host "${_health_bind:-127.0.0.1}")"
    _health_llama_port="$(read_env_value "$INSTALL_DIR/.env" "ODS_NATIVE_LLAMA_PORT")"
    [[ "$_health_llama_port" =~ ^[0-9]+$ ]] || _health_llama_port="8080"
    HEALTH_NAMES=("LLM (llama-server)" "Chat UI (Open WebUI)")
    HEALTH_URLS=("http://${_health_llama_host}:${_health_llama_port}/health" "http://127.0.0.1:3000")
    HEALTH_CONTAINERS=("" "ods-webui")
fi
$ENABLE_VOICE && HEALTH_NAMES+=("Whisper (STT)") && HEALTH_URLS+=("http://127.0.0.1:9000/health") && HEALTH_CONTAINERS+=("ods-whisper")
$ENABLE_WORKFLOWS && HEALTH_NAMES+=("n8n (Workflows)") && HEALTH_URLS+=("http://127.0.0.1:5678/healthz") && HEALTH_CONTAINERS+=("ods-n8n")
[[ -x "$OPENCODE_BIN" ]] && HEALTH_NAMES+=("OpenCode (IDE)") && HEALTH_URLS+=("http://127.0.0.1:${OPENCODE_PORT}") && HEALTH_CONTAINERS+=("")

for ((idx=0; idx<${#HEALTH_NAMES[@]}; idx++)); do
    NAME="${HEALTH_NAMES[$idx]}"
    URL="${HEALTH_URLS[$idx]}"
    CONTAINER="${HEALTH_CONTAINERS[$idx]}"
    HEALTHY=false

    for ((attempt=1; attempt<=MAX_ATTEMPTS; attempt++)); do
        if [[ -n "$CONTAINER" ]]; then
            # Docker service -- wait for the container healthcheck to report healthy.
            STATUS=$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo "missing")
            if [[ "$STATUS" == "healthy" ]]; then
                HEALTHY=true
                break
            fi
        else
            # Host-native service -- poll HTTP on 127.0.0.1.
            # Bound each probe: a listening-but-still-loading server accepts the
            # connection and would otherwise block past the loop's own budget
            # (matches the --max-time 10 used elsewhere in this installer).
            HTTP_CODE=$(curl -s --connect-timeout 5 --max-time 10 \
                -o /dev/null -w "%{http_code}" "$URL" 2>/dev/null || echo "000")
            if [[ "$HTTP_CODE" -ge 200 ]] && [[ "$HTTP_CODE" -lt 400 ]]; then
                HEALTHY=true
                break
            fi
            # 401/403 means service is responding (auth-protected) -- treat as healthy
            if [[ "$HTTP_CODE" == "401" ]] || [[ "$HTTP_CODE" == "403" ]]; then
                HEALTHY=true
                break
            fi
        fi
        if (( attempt <= 3 || attempt % 5 == 0 )); then
            ai "  Waiting for ${NAME}... (${attempt}/${MAX_ATTEMPTS})"
        fi
        sleep 2
    done

    if $HEALTHY; then
        ai_ok "${NAME}: healthy"
    else
        ai_warn "${NAME}: not responding after ${MAX_ATTEMPTS} attempts"
        ALL_HEALTHY=false
        if $CLOUD_MODE && (( idx < 2 )); then
            CLOUD_REQUIRED_HEALTHY=false
        fi
    fi
done

if $CLOUD_MODE; then
    _cloud_health_key="$(read_env_value "$INSTALL_DIR/.env" "LITELLM_KEY")"
    _cloud_health_bind="$(read_env_value "$INSTALL_DIR/.env" "BIND_ADDRESS")"
    _cloud_health_host="$(macos_bind_probe_host "${_cloud_health_bind:-127.0.0.1}")"
    _cloud_health_port="$(read_env_value "$INSTALL_DIR/.env" "LITELLM_PORT")"
    [[ "$_cloud_health_port" =~ ^[0-9]+$ ]] || _cloud_health_port="4000"
    _cloud_auth_ok=false
    if [[ -n "$_cloud_health_key" ]]; then
        for _cloud_health_i in $(seq 1 30); do
            if curl -fsS --connect-timeout 2 --max-time 5 \
                -H "Authorization: Bearer ${_cloud_health_key}" \
                "http://${_cloud_health_host}:${_cloud_health_port}/v1/models" \
                >/dev/null 2>&1; then
                _cloud_auth_ok=true
                break
            fi
            sleep 1
        done
    fi
    if [[ "$_cloud_auth_ok" != "true" ]]; then
        ai_err "Authenticated LiteLLM readiness failed; cloud inference is not usable."
        CLOUD_REQUIRED_HEALTHY=false
        ALL_HEALTHY=false
    else
        ai_ok "LiteLLM authenticated model route: healthy"
    fi
    unset _cloud_health_key _cloud_health_bind _cloud_health_host \
        _cloud_health_port _cloud_health_i _cloud_auth_ok

    if [[ "$CLOUD_REQUIRED_HEALTHY" != "true" ]]; then
        ai_err "Required cloud inference services failed readiness; refusing to report a successful install."
        exit 1
    fi
fi

# ── Pre-download the Whisper STT model ──
# Speaches does NOT auto-download on transcription requests — it returns 404.
# We must trigger the download explicitly here, verify it completed, and
# surface a clear recovery command if anything fails.
if [[ "$ENABLE_VOICE" == "true" ]]; then
    # Read AUDIO_STT_MODEL from .env (written by env-generator). On macOS the
    # default is base; user can override by editing .env before reinstalling.
    STT_MODEL=$(grep -m1 '^AUDIO_STT_MODEL=' "${INSTALL_DIR}/.env" 2>/dev/null \
                | cut -d= -f2- | tr -d '"' | tr -d '\r' || true)
    [[ -z "$STT_MODEL" ]] && STT_MODEL="Systran/faster-whisper-base"
    STT_MODEL_ENCODED="${STT_MODEL//\//%2F}"
    # macOS reassigns Whisper to 9100 if port 9000 is in use (AirPlay Receiver).
    WHISPER_PORT_RESOLVED="${WHISPER_PORT:-9000}"
    WHISPER_URL="http://127.0.0.1:${WHISPER_PORT_RESOLVED}"
    STT_MODEL_URL="${WHISPER_URL}/v1/models/${STT_MODEL_ENCODED}"
    STT_TRIGGER_TIMEOUT_SECONDS="${ODS_STT_TRIGGER_TIMEOUT_SECONDS:-30}"
    STT_CACHE_WAIT_SECONDS="${ODS_STT_CACHE_WAIT_SECONDS:-900}"
    [[ "$STT_TRIGGER_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || STT_TRIGGER_TIMEOUT_SECONDS=30
    [[ "$STT_CACHE_WAIT_SECONDS" =~ ^[1-9][0-9]*$ ]] || STT_CACHE_WAIT_SECONDS=900
    STT_RECOVERY_CMD="curl --max-time ${STT_TRIGGER_TIMEOUT_SECONDS} -X POST ${STT_MODEL_URL}"

    _macos_stt_model_cached() {
        local _url="$1"
        curl -sf --max-time 10 "$_url" &>/dev/null
    }

    _trigger_macos_stt_model_download() {
        local _url="$1"
        local _rc=0

        # Speaches can keep downloading after the request is accepted. Keep the
        # client bounded, then use the cache endpoint as the strict source of truth.
        curl -sS --fail --max-time "${STT_TRIGGER_TIMEOUT_SECONDS}" -X POST "$_url" \
            >> "$ODS_LOG_FILE" 2>&1 || _rc=$?
        if [[ "$_rc" -eq 0 || "$_rc" -eq 28 ]]; then
            return 0
        fi
        ai_warn "STT model download trigger returned curl exit ${_rc}; verifying cache before failing."
        return 1
    }

    _wait_macos_stt_model_cached() {
        local _url="$1"
        local _deadline=$((SECONDS + STT_CACHE_WAIT_SECONDS))

        while (( SECONDS < _deadline )); do
            if _macos_stt_model_cached "$_url"; then
                return 0
            fi
            sleep 5
        done
        _macos_stt_model_cached "$_url"
    }

    # Step 1: wait briefly for the models API to be ready (max 15s).
    _stt_api_ready=false
    for _i in $(seq 1 15); do
        if curl -sf --max-time 2 "${WHISPER_URL}/v1/models" &>/dev/null; then
            _stt_api_ready=true
            break
        fi
        sleep 1
    done

    if ! $_stt_api_ready; then
        ai_warn "STT models API not ready -- download manually:"
        echo "    $STT_RECOVERY_CMD"
    # Step 2: skip if already cached.
    elif _macos_stt_model_cached "$STT_MODEL_URL"; then
        ai_ok "STT model already cached (${STT_MODEL})"
    else
        # Step 3: POST to trigger download.
        ai "Downloading STT model (${STT_MODEL})..."
        _trigger_macos_stt_model_download "$STT_MODEL_URL" || true

        # Step 4: verify the model is actually cached.
        if _wait_macos_stt_model_cached "$STT_MODEL_URL"; then
            ai_ok "STT model cached (${STT_MODEL})"
        else
            ai_warn "STT model download failed -- run manually:"
            echo "    $STT_RECOVERY_CMD"
            echo "    See $ODS_LOG_FILE for details."
        fi
    fi
fi

# ── Auto-configure Perplexica ──
if $ENABLE_PERPLEXICA; then
    ai "Configuring Perplexica..."
    PERPLEXICA_MODEL="${GGUF_FILE:-$LLM_MODEL}"
    PERPLEXICA_API_KEY="no-key"
    $CLOUD_MODE && PERPLEXICA_MODEL="default"
    if $CLOUD_MODE; then
        PERPLEXICA_API_KEY="$(read_env_value "$INSTALL_DIR/.env" "LITELLM_KEY")"
    fi
    _perplexica_port="$(read_env_value "$INSTALL_DIR/.env" "PERPLEXICA_PORT")"
    [[ "$_perplexica_port" =~ ^[0-9]+$ ]] || _perplexica_port="3004"
    if [[ -z "$PERPLEXICA_API_KEY" ]] \
       || ! configure_perplexica "$_perplexica_port" "$PERPLEXICA_MODEL" \
            "${CONTAINER_LLM_URL:-http://host.docker.internal:8080}" "$PERPLEXICA_API_KEY"; then
        ai_err "Perplexica was selected but its authenticated inference route could not be configured and verified."
        exit 1
    fi
    ai_ok "Perplexica configured (model: ${PERPLEXICA_MODEL})"
    unset PERPLEXICA_API_KEY _perplexica_port
fi

# ── Pre-mark setup wizard complete ──
# The dashboard-api reads ${INSTALL_DIR}/data/config/setup-complete.json
# (mounted at /data/config/setup-complete.json inside the container) to
# decide first_run state. Writing this here prevents the wizard from
# reappearing on every visit after a fresh install. Non-fatal.
_setup_config_dir="${INSTALL_DIR}/data/config"
_setup_complete_file="${_setup_config_dir}/setup-complete.json"
_completed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
if mkdir -p "${_setup_config_dir}" 2>/dev/null \
    && printf '{"completed_at": "%s", "version": "1.0.0"}\n' "${_completed_at}" > "${_setup_complete_file}" 2>/dev/null \
    && chmod 644 "${_setup_complete_file}" 2>/dev/null; then
    ai_ok "Setup wizard pre-marked complete"
else
    ai_warn "Could not write ${_setup_complete_file} (non-fatal)"
fi

# ── Success card ──
if ! $ALL_HEALTHY; then
    echo ""
    ai_warn "Some services may still be starting. Check with:"
    echo -e "  ${GRN}./ods-macos.sh status${NC}"
    echo ""
fi

{
    printf 'Dashboard|http://127.0.0.1:3001|ods-dashboard|http://localhost:3001\n'
    printf 'Chat UI (Open WebUI)|http://127.0.0.1:3000|ods-webui|http://localhost:3000\n'
    if $CLOUD_MODE; then
        printf 'LiteLLM|http://127.0.0.1:4000/health/readiness|ods-litellm|http://localhost:4000\n'
    else
        printf 'llama-server|http://%s:%s/health||http://localhost:%s/v1\n' "$_health_llama_host" "$_health_llama_port" "$_health_llama_port"
    fi
    printf 'Dashboard API|http://127.0.0.1:3002/health|ods-dashboard-api|http://localhost:3002\n'
    printf 'Perplexica|http://127.0.0.1:3004|ods-perplexica|http://localhost:3004\n'
    $ENABLE_VOICE && printf 'Whisper (STT)|http://127.0.0.1:%s/health|ods-whisper|http://localhost:%s\n' "${WHISPER_PORT:-9000}" "${WHISPER_PORT:-9000}"
    $ENABLE_WORKFLOWS && printf 'n8n|http://127.0.0.1:5678/healthz|ods-n8n|http://localhost:5678\n'
    [[ -x "$OPENCODE_BIN" ]] && printf 'OpenCode (IDE)|http://127.0.0.1:%s||http://localhost:%s\n' "$OPENCODE_PORT" "$OPENCODE_PORT"
} | ods_readiness_summary "./ods-macos.sh status" "$ODS_LOG_FILE" "http://localhost:3001"

show_success_card
