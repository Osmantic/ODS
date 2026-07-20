#!/usr/bin/env bash
# hipfire entrypoint for ODS.
#
# hipfire's server settings (host/port/idle_timeout/default_model) are CONFIG-FILE
# keys, not env vars — the daemon reads ~/.hipfire/config.json and ignores the
# environment. So we translate ODS's env into `hipfire config set` on every start.
# (Same class of trap as Lemonade's cached config.json, which is what kept ODS's
# own llama-server on a Vulkan fallback.)
set -euo pipefail

# Defence in depth against the empty-but-defined HSA bug: ROCm treats a *defined*
# HSA_OVERRIDE_GFX_VERSION as an override request, fails to parse "", and then
# enumerates ZERO devices. compose passes it bare so it should be absent here —
# but if anything reintroduces it empty, unset it rather than go dark.
if [ -z "${HSA_OVERRIDE_GFX_VERSION:-}" ]; then
    unset HSA_OVERRIDE_GFX_VERSION || true
fi

HIPFIRE_PORT_INTERNAL="${HIPFIRE_PORT_INTERNAL:-11435}"

# idle_timeout defaults to 300s, which frees VRAM and forces a cold reload on the
# next request. Behind LiteLLM that is exactly wrong — default to never idling out.
hipfire config set host 0.0.0.0                                    >/dev/null
hipfire config set port "${HIPFIRE_PORT_INTERNAL}"                 >/dev/null
hipfire config set idle_timeout "${HIPFIRE_IDLE_TIMEOUT:-0}"       >/dev/null

if [ -n "${HIPFIRE_MODEL:-}" ]; then
    hipfire config set default_model "${HIPFIRE_MODEL}" >/dev/null
    # Models live on the mounted volume; only pull if it isn't already there.
    if ! hipfire list 2>/dev/null | grep -q -- "${HIPFIRE_MODEL}"; then
        echo "hipfire: ${HIPFIRE_MODEL} not present locally; pulling..."
        hipfire pull "${HIPFIRE_MODEL}"
    fi
fi

# Fail loudly and early if the GPU isn't visible, rather than silently serving
# from a broken state.
hipfire diag || true

exec hipfire serve "0.0.0.0:${HIPFIRE_PORT_INTERNAL}"
