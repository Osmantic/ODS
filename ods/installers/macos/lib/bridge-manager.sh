#!/bin/bash
# Shared launchd bridge management for the macOS installer and CLI.

macos_configure_port_bridge() {
    local enabled="$1" label="$2" plist="$3" log_path="$4" description="$5"
    local listen_host="$6" listen_port="$7" target_port="$8" allowed_peer="$9"
    local install_dir="${10}"
    local bridge_python="${MACOS_BRIDGE_PYTHON:-/usr/bin/python3}"

    launchctl bootout "gui/$(id -u)/${label}" >/dev/null 2>&1 || true
    rm -f "$plist" 2>/dev/null || true
    [[ "$enabled" == "true" ]] || return 0

    local bridge_script="${install_dir}/bin/ods-macos-llm-bridge.py"
    if [[ ! -f "$bridge_script" ]]; then
        ai_err "Colima host bridge is missing: ${bridge_script}"
        return 1
    fi
    if [[ -z "$listen_host" || -z "$allowed_peer" ]]; then
        ai_err "Colima private bridge addresses are missing; re-run with Colima --network-address."
        return 1
    fi

    chmod +x "$bridge_script"
    mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/ODS"
    cat > "$plist" <<BRIDGE_PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${bridge_python}</string>
        <string>${bridge_script}</string>
        <string>--listen-host</string>
        <string>${listen_host}</string>
        <string>--listen-port</string>
        <string>${listen_port}</string>
        <string>--target-port</string>
        <string>${target_port}</string>
        <string>--allow-peer</string>
        <string>${allowed_peer}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${install_dir}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>5</integer>
    <key>StandardOutPath</key>
    <string>${log_path}</string>
    <key>StandardErrorPath</key>
    <string>${log_path}</string>
</dict>
</plist>
BRIDGE_PLIST_EOF

    local bootstrap_err bootstrap_rc
    bootstrap_err="$(launchctl bootstrap "gui/$(id -u)" "$plist" 2>&1)" \
        && bootstrap_rc=0 || bootstrap_rc=$?
    if [[ "$bootstrap_rc" -ne 0 ]]; then
        ai_err "${description} LaunchAgent failed (rc=${bootstrap_rc}): ${bootstrap_err}"
        return 1
    fi
    launchctl kickstart -k "gui/$(id -u)/${label}" >/dev/null 2>&1 || true
    for _ in $(seq 1 30); do
        if "$bridge_python" -c \
            'import socket, sys; socket.create_connection((sys.argv[1], int(sys.argv[2])), 1).close()' \
            "$listen_host" "$listen_port" >/dev/null 2>&1; then
            ai_ok "${description} listening on ${listen_host}:${listen_port} -> 127.0.0.1:${target_port}"
            return 0
        fi
        sleep 0.2
    done
    ai_err "${description} did not open ${listen_host}:${listen_port}; inspect ${log_path}"
    return 1
}

macos_configure_llm_bridge_from_env() {
    local env_file="$1"
    local install_dir="$2"
    local mode bind_address listen_host allowed_peer listen_port target_port enabled

    mode="$(read_env_value "$env_file" "ODS_MODE")"
    [[ -n "$mode" ]] || mode="local"
    bind_address="$(read_env_value "$env_file" "BIND_ADDRESS")"
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
