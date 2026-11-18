#!/bin/bash
# Shared, ownership-bound launchd bridge management for the installer and CLI.

macos_configure_port_bridge() {
    local enabled="$1" label="$2" plist="$3" log_path="$4" description="$5"
    local listen_host="$6" listen_port="$7" target_port="$8" allowed_peer="$9"
    local install_dir="${10}"
    local bridge_python="${MACOS_BRIDGE_PYTHON:-/usr/bin/python3}"
    local manager="${install_dir}/bin/macos_bridge_service.py"
    if [[ ! -f "$manager" ]]; then
        ai_err "Colima bridge manager is missing: ${manager}"
        return 1
    fi
    local action=stop
    local bridge_args=()
    if [[ "$enabled" == true ]]; then
        action=configure
        bridge_args=("${install_dir}/bin/ods-macos-llm-bridge.py"
            --listen-host "$listen_host" --listen-port "$listen_port"
            --target-port "$target_port" --allow-peer "$allowed_peer")
    fi
    if ! "${ODS_PYTHON_CMD:-python3}" "$manager" "$action" "$install_dir" \
        "$bridge_python" "$label" "$plist" "$log_path" "${bridge_args[@]}"; then
        ai_err "${description} could not complete its owned ${action} operation"
        return 1
    fi
    if [[ "$enabled" == true ]]; then
        ai_ok "${description} listening on ${listen_host}:${listen_port} -> 127.0.0.1:${target_port}"
    fi
}

macos_configure_llm_bridge_from_env() {
    local env_file="$1"
    local install_dir="$2"
    local mode bind_address listen_host allowed_peer listen_port target_port enabled

    mode="$(read_env_value "$env_file" "ODS_MODE")"
    [[ -n "$mode" ]] || mode="local"
    bind_address="127.0.0.1"
    [[ -n "$bind_address" ]] || bind_address="127.0.0.1"
    listen_host="$(read_env_value "$env_file" "ODS_MACOS_HOST_GATEWAY")"
    allowed_peer="$(read_env_value "$env_file" "ODS_MACOS_VM_IP")"
    listen_port="$(read_env_value "$env_file" "OLLAMA_PORT")"
    target_port="$(read_env_value "$env_file" "ODS_NATIVE_LLAMA_PORT")"
    [[ "$listen_port" =~ ^[0-9]+$ ]] || listen_port="8080"
    [[ "$target_port" =~ ^[0-9]+$ ]] || target_port="8080"

    enabled="false"
    if [[ "$mode" != "cloud" ]] \
       && ! macos_bind_uses_direct_gateway "$bind_address" "$listen_host" \
       && [[ -n "$listen_host" && -n "$allowed_peer" ]]; then
        enabled="true"
    fi
    upsert_env_value "$env_file" "ODS_MACOS_LLM_BRIDGE_ENABLED" "$enabled"

    macos_configure_port_bridge "$enabled" "$LLM_BRIDGE_PLIST_LABEL" \
        "$LLM_BRIDGE_PLIST" "$LLM_BRIDGE_LOG" "Colima LLM bridge" \
        "$listen_host" "$listen_port" "$target_port" "$allowed_peer" "$install_dir"
}
