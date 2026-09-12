#!/bin/bash
#=============================================================================
# startup.sh — ComfyUI Container Entrypoint
#
# Sets up model symlinks from bind-mounted /models into ComfyUI's expected
# directory structure, links output/input dirs, copies workflow templates,
# and launches the ComfyUI server.
#=============================================================================

set -euo pipefail

COMFYUI_DIR="/opt/comfyui"
MODELS_MOUNT="/models"
OUTPUT_MOUNT="/output"
INPUT_MOUNT="/input"
WORKFLOWS_MOUNT="/workflows"
USER_MOUNT="/user"

#-----------------------------------------------------------------------------
# Create model subdirectories in bind mount (idempotent)
#-----------------------------------------------------------------------------
for subdir in checkpoints text_encoders diffusion_models vae latent_upscale_models loras; do
    mkdir -p "${MODELS_MOUNT}/${subdir}"
done

#-----------------------------------------------------------------------------
# Symlink bind-mounted model dirs → ComfyUI's models/ tree
#-----------------------------------------------------------------------------
MODEL_TARGET="${COMFYUI_DIR}/models"

for subdir in checkpoints text_encoders diffusion_models vae latent_upscale_models loras; do
    target="${MODEL_TARGET}/${subdir}"
    # Remove existing dir/link and replace with symlink
    if [ -L "$target" ]; then
        rm "$target"
    elif [ -d "$target" ]; then
        rm -rf "$target"
    fi
    ln -s "${MODELS_MOUNT}/${subdir}" "$target"
done

#-----------------------------------------------------------------------------
# Symlink output and input directories
#-----------------------------------------------------------------------------
for pair in "output:${OUTPUT_MOUNT}" "input:${INPUT_MOUNT}"; do
    dir_name="${pair%%:*}"
    mount_path="${pair#*:}"
    target="${COMFYUI_DIR}/${dir_name}"
    if [ -L "$target" ]; then
        rm "$target"
    elif [ -d "$target" ]; then
        rm -rf "$target"
    fi
    ln -s "$mount_path" "$target"
done

#-----------------------------------------------------------------------------
# Persist ComfyUI's user directory (saved workflows + UI settings)
#
# ComfyUI writes everything the user creates under user/. Without a bind mount
# that lives only in the container's writable layer and is destroyed on the
# next recreate — which a routine `ods update` performs (compose down + up).
# Mount outside the ComfyUI tree and symlink, exactly as output/ and input/ do.
# Skipped when /user is not mounted, so an older compose file still works.
#-----------------------------------------------------------------------------
if [ -d "$USER_MOUNT" ]; then
    user_target="${COMFYUI_DIR}/user"
    if [ -L "$user_target" ]; then
        rm "$user_target"
    elif [ -d "$user_target" ]; then
        # First start after this mount was introduced: carry over whatever the
        # container already holds rather than discarding it. -n keeps anything
        # already on the host authoritative.
        cp -a -n "$user_target/." "$USER_MOUNT/" 2>/dev/null || true
        rm -rf "$user_target"
    fi
    ln -s "$USER_MOUNT" "$user_target"
fi

#-----------------------------------------------------------------------------
# Copy workflow templates (read-only mount → writable user dir)
#-----------------------------------------------------------------------------
if [ -d "$WORKFLOWS_MOUNT" ] && [ "$(ls -A "$WORKFLOWS_MOUNT" 2>/dev/null)" ]; then
    WORKFLOW_DIR="${COMFYUI_DIR}/user/default/workflows"
    mkdir -p "$WORKFLOW_DIR"
    cp -u "$WORKFLOWS_MOUNT"/*.json "$WORKFLOW_DIR/" 2>/dev/null || true
    echo "[startup] Copied workflow templates to ${WORKFLOW_DIR}"
fi

#-----------------------------------------------------------------------------
# Launch ComfyUI
#-----------------------------------------------------------------------------
echo "[startup] Starting ComfyUI server..."
cd "$COMFYUI_DIR"
PYTHON_CMD="python3"
if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(0)' >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1 && python -c 'import sys; sys.exit(0)' >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

exec "$PYTHON_CMD" main.py --listen 0.0.0.0 --port 8188
