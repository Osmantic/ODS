#!/bin/bash
# Dream Server — ASCII QR Code Generator
# Generates simple QR codes for terminal display without external dependencies

# ═══════════════════════════════════════════════════════════════
# QR CODE DISPLAY
# ═══════════════════════════════════════════════════════════════

# Best-effort LAN IP detection for remote access URLs.
_dream_lan_ip() {
    local lan_ip=""
    if command -v ip >/dev/null 2>&1; then
        lan_ip=$(ip route get 1 2>/dev/null | awk '{print $7; exit}')
    fi

    if [[ -z "$lan_ip" ]] && command -v ifconfig >/dev/null 2>&1; then
        lan_ip=$(ifconfig 2>/dev/null | awk '/inet / && $2 != "127.0.0.1" {print $2; exit}')
    fi

    printf '%s' "$lan_ip"
}

# Print a QR code for the dashboard URL
# Falls back to plain text if qrencode not available
print_dashboard_qr() {
    local display_url="${1:-}"
    if [[ -z "$display_url" ]]; then
        local lan_ip
        lan_ip=$(_dream_lan_ip)
        display_url="http://${lan_ip:-localhost}:3001"
    fi
    
    echo ""
    
    # Try qrencode if available
    if command -v qrencode >/dev/null 2>&1; then
        echo -e "  ${BOLD}Scan to open Dashboard:${NC}"
        echo ""
        qrencode -t ANSIUTF8 -m 2 "$display_url" | sed 's/^/    /'
        echo ""
        echo -e "  ${CYAN}$display_url${NC}"
    else
        # Fallback: Simple ASCII box with URL
        print_url_box "$display_url"
    fi
}

# Print a stylish URL box (fallback when qrencode unavailable)
print_url_box() {
    local url=$1
    local url_len=${#url}
    local box_width=$((url_len + 6))
    
    # Build horizontal line
    local hline=""
    for ((i=0; i<box_width; i++)); do hline+="═"; done
    
    echo -e "  ${CYAN}╔${hline}╗${NC}"
    echo -e "  ${CYAN}║${NC}   ${BOLD}${url}${NC}   ${CYAN}║${NC}"
    echo -e "  ${CYAN}╚${hline}╝${NC}"
}

# ═══════════════════════════════════════════════════════════════
# SUCCESS CARD
# ═══════════════════════════════════════════════════════════════

# Print the final success card with all access info
print_success_card() {
    local tier=$1
    local model=$2
    local dashboard_url=${3:-"http://localhost:3001"}
    local api_url=${4:-"http://localhost:8000/v1"}
    
    # Get LAN IP for remote access URLs
    local lan_ip
    lan_ip=$(_dream_lan_ip)
    
    local remote_dash="http://${lan_ip:-localhost}:3001"
    local remote_api="http://${lan_ip:-localhost}:8000/v1"
    
    echo ""
    echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║${NC}                                                               ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}   ${BOLD}🌙 Dream Server is Ready!${NC}                                  ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}                                                               ${GREEN}║${NC}"
    echo -e "${GREEN}╠═══════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${GREEN}║${NC}                                                               ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}   ${BOLD}Tier:${NC}       $tier                                           ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}   ${BOLD}Model:${NC}      $model                     ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}                                                               ${GREEN}║${NC}"
    echo -e "${GREEN}╠═══════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${GREEN}║${NC}                                                               ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}   ${CYAN}Local Access:${NC}                                              ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}     Dashboard:  $dashboard_url                               ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}     API:        $api_url                            ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}                                                               ${GREEN}║${NC}"
    if [[ -n "$lan_ip" ]]; then
        echo -e "${GREEN}║${NC}   ${CYAN}Remote Access (LAN):${NC}                                       ${GREEN}║${NC}"
        echo -e "${GREEN}║${NC}     Dashboard:  $remote_dash                        ${GREEN}║${NC}"
        echo -e "${GREEN}║${NC}     API:        $remote_api                 ${GREEN}║${NC}"
        echo -e "${GREEN}║${NC}                                                               ${GREEN}║${NC}"
    fi
    echo -e "${GREEN}╠═══════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${GREEN}║${NC}                                                               ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}   ${BOLD}Quick Commands:${NC}                                             ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}     View logs:     docker compose logs -f                    ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}     Stop server:   docker compose down                       ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}     Restart:       docker compose restart                    ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}                                                               ${GREEN}║${NC}"
    echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
    
    # Print QR code for mobile access
    if [[ -n "$lan_ip" ]]; then
        print_dashboard_qr "$remote_dash"
    fi
    
    echo ""
    echo -e "${BOLD}Welcome to your Dream. 🌙${NC}"
    echo ""
}

# ═══════════════════════════════════════════════════════════════
# INSTALL SUMMARY
# ═══════════════════════════════════════════════════════════════

# Print installation summary with timing
print_install_summary() {
    local tier=$1
    local model=$2
    local start_time=$3
    local end_time=$4
    
    local duration=$((end_time - start_time))
    local duration_str
    duration_str=$(format_duration $duration)
    
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}Installation Complete${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "  ${BOLD}Tier:${NC}           $tier"
    echo -e "  ${BOLD}Model:${NC}          $model"
    echo -e "  ${BOLD}Install Time:${NC}   $duration_str"
    echo ""
}
