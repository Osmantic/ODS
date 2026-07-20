#!/usr/bin/env bash
# ============================================================================
# ODS Host Agent Firewall Sync
# ============================================================================
# Purpose: (re-)allow ods-network containers to reach the host agent's port,
#   re-deriving the CURRENT ods-network subnet fresh on every call — never a
#   cached/hardcoded value. Companion to the install-time rule added by
#   installers/phases/11-services.sh's _phase11_allow_container_host_firewall —
#   that one only runs once, at install/upgrade; this one runs on every
#   host-agent (re)start.
#
# Invoked as: ExecStartPre=+<INSTALL_DIR>/scripts/sync-host-agent-firewall.sh
#   from ods-host-agent.service. The `+` prefix runs it as root regardless of
#   the unit's User=, so no sudo/NOPASSWD entry is needed.
#
# Coverage: this fires on every agent process start — boot, manual restart,
#   Restart=on-failure — AND on Docker re-IPAM while the agent is running:
#   the agent's bind-address watchdog (bin/ods-host-agent.py,
#   _watch_for_bind_address_change) detects the gateway move and, under
#   systemd, exits nonzero specifically so Restart=on-failure re-runs this
#   script against the new subnet before the agent rebinds. Old rules for
#   previous subnets are left in place (add-only); they are harmless surplus.
#
# Contract: NEVER fails the caller — a firewall helper failing must never
#   block host-agent startup. Missing docker/ufw/firewalld, no active
#   firewall, or an undetectable subnet are all logged (to stderr, captured
#   by the systemd journal) and this exits 0 regardless.
# ============================================================================

set -uo pipefail  # deliberately no -e: every branch below handles its own
                   # failure explicitly and this must always reach `exit 0`

NETWORK_NAME="${1:-ods-network}"
RULE_LABEL="${2:-ods-host-agent}"
INSTALL_DIR="${ODS_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

log() { echo "sync-host-agent-firewall: $*" >&2; }

PORT="$(grep -m1 '^ODS_AGENT_PORT=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2-)"
PORT="${PORT:-7710}"
if ! [[ "$PORT" =~ ^[0-9]+$ ]]; then
    log "invalid ODS_AGENT_PORT '$PORT'; skipping"
    exit 0
fi

BIND_ADDR="$(grep -m1 '^ODS_AGENT_BIND=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2-)"
if [[ -n "$BIND_ADDR" && "$BIND_ADDR" != "0.0.0.0" ]]; then
    log "ODS_AGENT_BIND=$BIND_ADDR is explicitly scoped; skipping automatic rule"
    exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
    log "docker not found; skipping"
    exit 0
fi
if ! command -v systemctl >/dev/null 2>&1; then
    log "systemctl not found; skipping"
    exit 0
fi

mapfile -t SUBNETS < <(docker network inspect "$NETWORK_NAME" \
    --format '{{range .IPAM.Config}}{{println .Subnet}}{{end}}' 2>/dev/null | grep -v ':')

if [[ ${#SUBNETS[@]} -eq 0 ]]; then
    log "could not detect $NETWORK_NAME subnet (network not up yet?); skipping"
    exit 0
fi

for subnet in "${SUBNETS[@]}"; do
    [[ -z "$subnet" ]] && continue

    if command -v ufw >/dev/null 2>&1 && systemctl is-active --quiet ufw 2>/dev/null; then
        if ufw status 2>/dev/null | grep -F "${PORT}/tcp" | grep -F "$subnet" >/dev/null; then
            log "UFW already allows $RULE_LABEL (port $PORT) from $subnet"
        elif ufw allow from "$subnet" to any port "$PORT" proto tcp comment "$RULE_LABEL" >/dev/null 2>&1; then
            log "UFW: allowed $RULE_LABEL (port $PORT) from $subnet"
        else
            log "UFW: failed to add rule for $subnet (non-fatal)"
        fi
    elif command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld 2>/dev/null; then
        fw_rule="rule family=\"ipv4\" source address=\"$subnet\" port protocol=\"tcp\" port=\"$PORT\" accept"
        if firewall-cmd --query-rich-rule="$fw_rule" >/dev/null 2>&1; then
            log "firewalld already allows $RULE_LABEL (port $PORT) from $subnet"
        elif firewall-cmd --permanent --add-rich-rule="$fw_rule" >/dev/null 2>&1 \
          && firewall-cmd --reload >/dev/null 2>&1; then
            log "firewalld: allowed $RULE_LABEL (port $PORT) from $subnet"
        else
            log "firewalld: failed to add rule for $subnet (non-fatal)"
        fi
    else
        log "no active supported firewall detected; nothing to do"
    fi
done

exit 0
