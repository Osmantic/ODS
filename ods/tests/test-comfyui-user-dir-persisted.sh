#!/usr/bin/env bash
# ComfyUI saves workflows and UI settings under $COMFYUI_DIR/user. That path
# was not bind-mounted, so everything a user created lived only in the
# container's writable layer and was destroyed on the next recreate — which a
# routine `ods update` performs (docker compose down + up). This asserts the
# user directory is persisted to the host and that startup.sh links it before
# the workflow templates are copied into it.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="${ODS_COMFYUI_COMPOSE_UNDER_TEST:-$ROOT_DIR/extensions/services/comfyui/compose.nvidia.yaml}"
STARTUP="${ODS_COMFYUI_STARTUP_UNDER_TEST:-$ROOT_DIR/extensions/services/comfyui/startup.sh}"
PHASE="${ODS_PHASE11_UNDER_TEST:-$ROOT_DIR/installers/phases/11-services.sh}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

for f in "$COMPOSE" "$STARTUP" "$PHASE"; do [[ -f "$f" ]] || fail "missing $f"; done

# 1. The user directory must be bind-mounted, and writable (not ro).
mount_line="$(grep -E '^\s*-\s*\./data/comfyui/user:' "$COMPOSE" || true)"
[[ -n "$mount_line" ]] \
    || fail "compose.nvidia.yaml does not bind-mount ./data/comfyui/user; saved workflows stay in the container layer"
grep -qE ':ro(,|$|[[:space:]])' <<<"$mount_line" \
    && fail "the user directory is mounted read-only: $mount_line"
pass "the ComfyUI user directory is bind-mounted read-write"

# 2. startup.sh must link it, and do so before copying workflow templates in.
grep -q 'USER_MOUNT=' "$STARTUP" || fail "startup.sh defines no USER_MOUNT"
link_at="$(grep -n 'ln -s "\$USER_MOUNT"' "$STARTUP" | head -1 | cut -d: -f1)"
copy_at="$(grep -n 'WORKFLOWS_MOUNT"/\*.json' "$STARTUP" | head -1 | cut -d: -f1)"
[[ -n "$link_at" ]] || fail "startup.sh never links \$COMFYUI_DIR/user to the mount"
[[ -n "$copy_at" ]] || fail "could not locate the workflow-template copy in startup.sh"
[[ "$link_at" -lt "$copy_at" ]] \
    || fail "startup.sh copies workflow templates (line $copy_at) before linking the user dir (line $link_at); templates would land in the container layer"
pass "startup.sh links the user directory before seeding workflow templates"

# 3. The installer must pre-create the host directory alongside the others.
grep -qE 'mkdir -p "\$INSTALL_DIR/data/comfyui"/\{[^}]*\buser\b[^}]*\}' "$PHASE" \
    || fail "phase 11 does not create data/comfyui/user, so Docker would auto-create it as root"
pass "the installer pre-creates data/comfyui/user"

# 4. Behavioural: run startup.sh's user-persistence block against a fixture.
block="$(awk '/^if \[ -d "\$USER_MOUNT" \]; then$/{grab=1} grab{print} grab && /^fi$/{exit}' "$STARTUP")"
[[ -n "$block" ]] || fail "could not extract the user-persistence block from startup.sh"

COMFYUI_DIR="$TMP_DIR/comfyui"; USER_MOUNT="$TMP_DIR/user-mount"
mkdir -p "$COMFYUI_DIR/user/default/workflows" "$USER_MOUNT"
printf 'saved-by-user\n' > "$COMFYUI_DIR/user/default/workflows/my-workflow.json"
printf 'ui-settings\n'   > "$COMFYUI_DIR/user/default/comfy.settings.json"
export COMFYUI_DIR USER_MOUNT
bash -c "set -euo pipefail; $block"

[[ -L "$COMFYUI_DIR/user" ]] \
    || fail "startup.sh did not replace \$COMFYUI_DIR/user with a symlink to the mount"
[[ "$(readlink "$COMFYUI_DIR/user")" == "$USER_MOUNT" ]] \
    || fail "\$COMFYUI_DIR/user points at $(readlink "$COMFYUI_DIR/user"), expected $USER_MOUNT"
pass "startup.sh points the ComfyUI user directory at the persisted mount"

[[ -f "$USER_MOUNT/default/workflows/my-workflow.json" ]] \
    || fail "a workflow already in the container was discarded instead of migrated to the host"
[[ "$(cat "$USER_MOUNT/default/workflows/my-workflow.json")" == "saved-by-user" ]] \
    || fail "the migrated workflow's contents changed"
[[ -f "$USER_MOUNT/default/comfy.settings.json" ]] \
    || fail "UI settings were not migrated to the host"
pass "existing container-side workflows and settings are migrated, not discarded"

# 5. Re-running must be idempotent and must not clobber host state.
printf 'host-wins\n' > "$USER_MOUNT/default/workflows/my-workflow.json"
bash -c "set -euo pipefail; $block"
[[ "$(cat "$USER_MOUNT/default/workflows/my-workflow.json")" == "host-wins" ]] \
    || fail "a restart overwrote host-side workflow state"
[[ -L "$COMFYUI_DIR/user" ]] || fail "the symlink did not survive a restart"
pass "restarting keeps the host copy authoritative"
