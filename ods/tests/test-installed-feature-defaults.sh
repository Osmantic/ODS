#!/usr/bin/env bash
# A rerun must recover the selected optional services before it parses flags.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/installers/lib/constants.sh"
source "$ROOT/installers/lib/installed-feature-state.sh"
source "$ROOT/installers/lib/external-services.sh"
defaults="$(sed -n '/^DRY_RUN=false$/,/^INTERACTIVE=true$/p' "$ROOT/install-core.sh")"
[[ -n "$defaults" ]] || { echo 'FAIL: installer defaults block missing' >&2; exit 1; }

fixture="$(mktemp -d)"
trap 'rm -rf -- "$fixture"' EXIT
INSTALL_DIR="$fixture"
# Keep ordinary selection fixtures independent of the host user manager.
mkdir -p "$fixture/default-bin"
printf '#!/usr/bin/env bash\nexit 1\n' > "$fixture/default-bin/systemctl"
chmod +x "$fixture/default-bin/systemctl"
export PATH="$fixture/default-bin:$PATH"
: >"$INSTALL_DIR/.env"

mark() {
    local service="$1" selection="$2" dir
    dir="$INSTALL_DIR/extensions/services/$service"
    mkdir -p "$dir"
    case "$selection" in
        on) : >"$dir/compose.yaml" ;;
        off) : >"$dir/compose.yaml.disabled" ;;
    esac
}
for service in whisper tts n8n qdrant embeddings token-spy hermes hermes-proxy \
               comfyui ape perplexica privacy-shield langfuse ods-proxy tailscale brave-search; do
    mark "$service" off
done

eval "$defaults"
for flag in ENABLE_VOICE ENABLE_WORKFLOWS ENABLE_RAG ENABLE_RECOMMENDED \
            ENABLE_HERMES ENABLE_COMFYUI ENABLE_APE ENABLE_PERPLEXICA \
            ENABLE_PRIVACY_SHIELD ENABLE_LANGFUSE ENABLE_ODS_PROXY \
            ENABLE_TAILSCALE ENABLE_BRAVE_SEARCH; do
    [[ "${!flag}" == false ]] || { echo "FAIL: $flag reenabled on lean rerun" >&2; exit 1; }
done
[[ "$ENABLE_OPENCODE" == false ]] || exit 1

# Mixed selections and a stale opposite file follow the active Compose name.
mark n8n on
mark qdrant on
mark token-spy on
eval "$defaults"
[[ "$ENABLE_WORKFLOWS" == true && "$ENABLE_RAG" == true && "$ENABLE_RECOMMENDED" == true ]] || {
    echo 'FAIL: active selected features were lost on rerun' >&2; exit 1;
}
[[ "$ENABLE_VOICE" == false && "$ENABLE_PERPLEXICA" == false ]] || {
    echo 'FAIL: disabled selected features were enabled on rerun' >&2; exit 1;
}

printf 'ODS_GATEWAY_ONLY=true\nENABLE_OPEN_WEBUI=false\n' >"$INSTALL_DIR/.env"
eval "$defaults"
[[ "$ODS_GATEWAY_ONLY" == true && "$ENABLE_OPEN_WEBUI" == false ]] || {
    echo 'FAIL: API-only gateway selection was lost on rerun' >&2; exit 1;
}

# Counter-regression: an existing install (INSTALL_DIR/.env present) whose
# optional services are enabled must recover that ENABLE state on rerun, even
# though their helper fallback is false (ods-proxy/tailscale/langfuse). The fix
# must gate on .env existence alone, never on fallback!=true.
: >"$INSTALL_DIR/.env"
for service in whisper n8n qdrant token-spy hermes comfyui ape perplexica \
               privacy-shield langfuse ods-proxy tailscale brave-search; do
    mark "$service" on
done
eval "$defaults"
for flag in ENABLE_VOICE ENABLE_WORKFLOWS ENABLE_RAG ENABLE_RECOMMENDED \
            ENABLE_HERMES ENABLE_COMFYUI ENABLE_APE ENABLE_PERPLEXICA \
            ENABLE_PRIVACY_SHIELD ENABLE_LANGFUSE ENABLE_ODS_PROXY \
            ENABLE_TAILSCALE ENABLE_BRAVE_SEARCH; do
    [[ "${!flag}" == true ]] || {
        echo "FAIL: enabled fallback=false service $flag was not recovered on rerun" >&2
        exit 1
    }
done
# The ambient host may expose a real user systemctl; this block asserts only
# the optional Compose selections, not the separate OpenCode user-service probe.
    [[ "$ODS_EXISTING_INSTALL" == true && "$ENABLE_OPEN_WEBUI" == true ]] || {
    echo 'FAIL: existing-install WebUI selection was lost on rerun' >&2
    exit 1
}

# Library setup enables a user service. An unattended installer rerun may lack
# login-session environment even though the user manager is reachable through
# the installer's ods_systemctl_user helper. Preserve that selected add-back.
(
    : >"$INSTALL_DIR/.env"
    mkdir -p "$fixture/home/.config/systemd/user" "$fixture/mock-bin"
    : >"$fixture/home/.config/systemd/user/opencode-web.service"
    cat >"$fixture/mock-bin/systemctl" <<'MOCK_SYSTEMCTL'
#!/usr/bin/env bash
[[ "$*" == '--user is-enabled --quiet opencode-web.service' ]] || exit 2
[[ -f "$HOME/.config/systemd/user/opencode-web.service" ]] || exit 3
[[ "${XDG_RUNTIME_DIR:-}" == "/run/user/$(id -u)" ]] || exit 4
[[ "${DBUS_SESSION_BUS_ADDRESS:-}" == "unix:path=$XDG_RUNTIME_DIR/bus" ]] || exit 5
MOCK_SYSTEMCTL
    chmod +x "$fixture/mock-bin/systemctl"
    export HOME="$fixture/home" PATH="$fixture/mock-bin:$PATH"
    unset XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS
    if systemctl --user is-enabled --quiet opencode-web.service; then
        echo 'FAIL: headless OpenCode fixture unexpectedly has login-session environment' >&2
        exit 1
    fi
    eval "$defaults"
    [[ "$ENABLE_OPENCODE" == true ]] || {
        echo 'FAIL: enabled Library OpenCode was lost on headless installer rerun' >&2
        exit 1
    }
)

# Explicit --all remains after this block in install-core.sh and overrides it.
defaults_line="$(awk '/^INTERACTIVE=true$/ { print NR; exit }' "$ROOT/install-core.sh")"
all_line="$(awk '/^[[:space:]]*--all\)/ { print NR; exit }' "$ROOT/install-core.sh")"
[[ "$all_line" -gt "$defaults_line" ]] || { echo 'FAIL: --all precedes defaults' >&2; exit 1; }
echo 'PASS: installed optional Compose selections survive installer reruns'
