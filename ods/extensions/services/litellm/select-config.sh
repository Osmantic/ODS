#!/bin/sh
set -eu

mode="${ODS_MODE:-local}"
switchboard="${ODS_MODEL_SWITCHBOARD:-enabled}"
mode_config="${1:-/app/config.yaml}"
switchboard_config="${2:-/app/switchboard.yaml}"

# Cloud routing is authoritative in cloud mode. The switchboard config targets
# ODS's local model-router and is valid only for modes with a local runtime.
if [ "$mode" != "cloud" ] && [ "$switchboard" = "enabled" ]; then
    printf '%s\n' "$switchboard_config"
else
    printf '%s\n' "$mode_config"
fi
