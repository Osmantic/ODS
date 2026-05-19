#!/bin/bash
# ============================================================================
# Dream Server Installer — Phase 07: Developer Tools
# ============================================================================
# Part of: installers/phases/
# Purpose: Install Claude Code, Codex CLI, and OpenCode
#
# Expects: DRY_RUN, INSTALL_DIR, LOG_FILE, LLM_MODEL, MAX_CONTEXT,
#           PKG_MANAGER,
#           ai(), ai_ok(), ai_warn(), log()
# Provides: (developer tools installed to ~/.npm-global)
#
# Modder notes:
#   Add new developer tools or change installation methods here.
# ============================================================================

dream_progress 42 "devtools" "Installing developer tools"
if $DRY_RUN; then
    log "[DRY RUN] Would install AI developer tools (Claude Code, Codex CLI, OpenCode)"
    log "[DRY RUN] Would configure OpenCode for local llama-server (user-level systemd service on port 3003)"
else
    ai "Installing AI developer tools..."

    # Ensure Node.js/npm is available (needed for Claude Code and Codex)
    if ! command -v npm &> /dev/null; then
        # In non-interactive mode, fail fast if sudo requires a password — otherwise
        # the sudo prompt hangs and the trailing error-mask silently skips Node.js,
        # leaving downstream Claude Code / Codex CLI installs to be skipped without
        # any visible failure. See pattern in 05-docker.sh:61-65.
        if [[ "${INTERACTIVE:-true}" != "true" ]] && ! sudo -n true 2>/dev/null; then
            error "Cannot install Node.js: sudo password required but running in --non-interactive mode. Re-run interactively or configure NOPASSWD sudo."
        fi
        ai "Installing Node.js..."
        case "$PKG_MANAGER" in
            apt)
                tmpfile=$(mktemp /tmp/nodesource-setup.XXXXXX.sh)
                if curl -fsSL --max-time 300 https://deb.nodesource.com/setup_22.x -o "$tmpfile" 2>/dev/null; then
                    sudo -E bash "$tmpfile" 2>&1 | tee -a "$LOG_FILE" || ai_warn "Failed to run NodeSource apt setup script (non-fatal — Claude Code/Codex CLI will be skipped)"
                fi
                rm -f "$tmpfile"
                sudo apt-get install -y nodejs 2>&1 | tee -a "$LOG_FILE" || ai_warn "Failed to install nodejs via apt-get (non-fatal — Claude Code/Codex CLI will be skipped)"
                ;;
            dnf)
                sudo dnf module install -y nodejs:22 2>&1 | tee -a "$LOG_FILE" || \
                    sudo dnf install -y nodejs 2>&1 | tee -a "$LOG_FILE" || ai_warn "Failed to install nodejs via dnf (non-fatal — Claude Code/Codex CLI will be skipped)"
                ;;
            pacman)
                sudo pacman -S --noconfirm --needed nodejs npm 2>&1 | tee -a "$LOG_FILE" || ai_warn "Failed to install nodejs via pacman (non-fatal — Claude Code/Codex CLI will be skipped)"
                ;;
            zypper)
                sudo zypper --non-interactive install nodejs22 2>&1 | tee -a "$LOG_FILE" || \
                    sudo zypper --non-interactive install nodejs 2>&1 | tee -a "$LOG_FILE" || ai_warn "Failed to install nodejs via zypper (non-fatal — Claude Code/Codex CLI will be skipped)"
                ;;
            *)
                ai_warn "Unknown package manager — cannot install Node.js automatically"
                ;;
        esac
    fi

    if command -v npm &> /dev/null; then
        # Set up user-level npm global prefix (no sudo needed)
        NPM_GLOBAL_DIR="$HOME/.npm-global"
        if [[ ! -d "$NPM_GLOBAL_DIR" ]]; then
            mkdir -p "$NPM_GLOBAL_DIR"
            npm config set prefix "$NPM_GLOBAL_DIR" 2>/dev/null || true
        fi
        # Ensure user-level bin is on PATH for this session
        export PATH="$NPM_GLOBAL_DIR/bin:$PATH"

        # Install Claude Code (Anthropic's CLI for Claude)
        if ! command -v claude &> /dev/null; then
            npm install -g @anthropic-ai/claude-code >> "$LOG_FILE" 2>&1 && \
                ai_ok "Claude Code installed (run 'claude' to start)" || \
                ai_warn "Claude Code install failed — install later with: npm i -g @anthropic-ai/claude-code"
        else
            ai_ok "Claude Code already installed"
        fi

        # Install Codex CLI (OpenAI's terminal agent)
        if ! command -v codex &> /dev/null; then
            npm install -g @openai/codex >> "$LOG_FILE" 2>&1 && \
                ai_ok "Codex CLI installed (run 'codex' to start)" || \
                ai_warn "Codex CLI install failed — install later with: npm i -g @openai/codex"
        else
            ai_ok "Codex CLI already installed"
        fi

        # Ensure ~/.npm-global/bin is on PATH permanently
        if [[ -d "$NPM_GLOBAL_DIR/bin" ]] && ! grep -q 'npm-global' "$HOME/.bashrc" 2>/dev/null; then
            echo 'export PATH="$HOME/.npm-global/bin:$PATH"' >> "$HOME/.bashrc"
            ai "Added ~/.npm-global/bin to PATH in ~/.bashrc"
        fi
    else
        ai_warn "npm not available — skipping Claude Code and Codex CLI install"
        ai "  Install later: npm i -g @anthropic-ai/claude-code @openai/codex"
    fi

    # ── OpenCode (local agentic coding platform) ──
    if ! command -v opencode &> /dev/null && [[ ! -x "$HOME/.opencode/bin/opencode" ]]; then
        ai "Installing OpenCode..."
        tmpfile=$(mktemp /tmp/opencode-install.XXXXXX.sh)
        if curl -fsSL --max-time 300 https://opencode.ai/install -o "$tmpfile" 2>/dev/null && bash "$tmpfile" >> "$LOG_FILE" 2>&1; then
            ai_ok "OpenCode installed (~/.opencode/bin/opencode)"
        else
            ai_warn "OpenCode install failed — install later with: curl -fsSL https://opencode.ai/install | bash"
        fi
        rm -f "$tmpfile"
    else
        ai_ok "OpenCode already installed"
    fi

    # Configure OpenCode to use local llama-server
    if [[ -x "$HOME/.opencode/bin/opencode" ]]; then
        OPENCODE_CONFIG_DIR="$HOME/.config/opencode"
        mkdir -p "$OPENCODE_CONFIG_DIR"
        # Read OLLAMA_PORT and DREAM_MODE from .env generated in phase 06
        if [[ -f "$INSTALL_DIR/.env" ]]; then
            [[ -z "${OLLAMA_PORT:-}" ]] && OLLAMA_PORT=$(grep -m1 '^OLLAMA_PORT=' "$INSTALL_DIR/.env" | cut -d= -f2-)
            # Always re-read DREAM_MODE from .env — Phase 06 may have changed it
            # (e.g. "local" → "lemonade" for AMD) but the shell variable is stale.
            DREAM_MODE=$(grep -m1 '^DREAM_MODE=' "$INSTALL_DIR/.env" | cut -d= -f2-)
            [[ -z "${LITELLM_KEY:-}" ]] && LITELLM_KEY=$(grep -m1 '^LITELLM_KEY=' "$INSTALL_DIR/.env" | cut -d= -f2-)
        fi
        # Route through LiteLLM on AMD/Lemonade, direct to llama-server otherwise
        if [[ "${DREAM_MODE:-local}" == "lemonade" ]]; then
            _opencode_url="http://127.0.0.1:4000/v1"
            _opencode_key="no-key"  # LiteLLM auth removed for local-only installs
        else
            _opencode_url="http://127.0.0.1:${OLLAMA_PORT:-8080}/v1"
            _opencode_key="no-key"
        fi

        # Writes a fresh opencode.json from the template. Used for first-install
        # and as deterministic recovery when the jq rewrite path finds an
        # existing malformed file it cannot parse (issue #332).
        _opencode_write_fresh() {
            cat > "$1" <<OPENCODE_EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "model": "llama-server/${LLM_MODEL}",
  "small_model": "llama-server/${LLM_MODEL}",
  "provider": {
    "llama-server": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "llama-server (local)",
      "options": {
        "baseURL": "${_opencode_url}",
        "apiKey": "${_opencode_key}"
      },
      "models": {
        "${LLM_MODEL}": {
          "name": "${LLM_MODEL}",
          "limit": {
            "context": ${MAX_CONTEXT:-131072},
            "output": 32768
          }
        }
      }
    }
  }
}
OPENCODE_EOF
        }

        if [[ ! -f "$OPENCODE_CONFIG_DIR/opencode.json" ]]; then
            _opencode_write_fresh "$OPENCODE_CONFIG_DIR/opencode.json"
            ai_ok "OpenCode configured for local llama-server (model: ${LLM_MODEL})"
        else
            # Reinstall: update API key and URL in existing config (key may have changed)
            _opencode_updated=false
            if command -v jq >/dev/null 2>&1; then
                _opencode_tmp="$OPENCODE_CONFIG_DIR/opencode.json.tmp.$$"
                if jq --arg url "$_opencode_url" --arg key "$_opencode_key" \
                    '.provider["llama-server"].options.baseURL = $url
                     | .provider["llama-server"].options.apiKey = $key' \
                    "$OPENCODE_CONFIG_DIR/opencode.json" > "$_opencode_tmp" 2>/dev/null; then
                    mv "$_opencode_tmp" "$OPENCODE_CONFIG_DIR/opencode.json"
                    ai_ok "OpenCode config updated (API key and URL refreshed)"
                    _opencode_updated=true
                else
                    rm -f "$_opencode_tmp"
                    ai_warn "OpenCode config jq rewrite failed (existing file unparseable) — regenerating from template"
                fi
            else
                # Fallback without jq: narrow sed that only matches the quoted value,
                # preserving any trailing comma on the line
                _sed_i "s|\"apiKey\": *\"[^\"]*\"|\"apiKey\": \"${_opencode_key}\"|" "$OPENCODE_CONFIG_DIR/opencode.json"
                _sed_i "s|\"baseURL\": *\"[^\"]*\"|\"baseURL\": \"${_opencode_url}\"|" "$OPENCODE_CONFIG_DIR/opencode.json"
                ai_ok "OpenCode config updated (API key and URL refreshed)"
                _opencode_updated=true
            fi
            # Recovery path (issue #332): if the update branch above failed to
            # produce a valid file (jq parse error on pre-existing corruption),
            # regenerate deterministically from the template.
            if [[ "$_opencode_updated" != "true" ]]; then
                _opencode_write_fresh "$OPENCODE_CONFIG_DIR/opencode.json"
                ai_ok "OpenCode config regenerated from template (recovered from corruption)"
            fi
        fi
        # OpenCode reads config.json, not opencode.json — always sync
        cp "$OPENCODE_CONFIG_DIR/opencode.json" "$OPENCODE_CONFIG_DIR/config.json"

        # Install OpenCode Web UI as user-level systemd service (no sudo required)
        if [[ -f "$INSTALL_DIR/opencode/opencode-web.service" ]]; then
            SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
            mkdir -p "$SYSTEMD_USER_DIR"

            svc_tmp="/tmp/opencode-web.service.$$"
            cp "$INSTALL_DIR/opencode/opencode-web.service" "$svc_tmp"
            # Escape sed special chars to prevent injection from path values
            _home_esc=$(printf '%s\n' "$HOME" | sed 's/[&/\]/\\&/g')
            _sed_i "s|__HOME__|${_home_esc}|g" "$svc_tmp"
            cp "$svc_tmp" "$SYSTEMD_USER_DIR/opencode-web.service"
            rm -f "$svc_tmp"

            systemctl --user daemon-reload 2>/dev/null || true
            systemctl --user enable --now opencode-web.service >> "$LOG_FILE" 2>&1 && \
                ai_ok "OpenCode Web UI service installed (user-level, port 3003)" || \
                ai_warn "OpenCode Web UI service failed to start"

            # Enable lingering so service survives logout
            loginctl enable-linger "$(whoami)" 2>/dev/null || \
                sudo -n loginctl enable-linger "$(whoami)" 2>/dev/null || \
                ai_warn "Could not enable linger. OpenCode may stop after logout. Run: loginctl enable-linger $(whoami)"
        fi
    fi
fi

# ── Dream Host Agent (extension lifecycle management) ──
# System-mode systemd unit (was --user mode pre-#573). Installs to
# /etc/systemd/system, runs as the installing user with SupplementaryGroups=docker
# so the agent can manage Docker without socket-mounting into a container.
if [[ -f "$INSTALL_DIR/bin/dream-host-agent.py" ]]; then
    AGENT_PYTHON="$(command -v python3)"
    if [[ -n "$AGENT_PYTHON" ]]; then
        if systemctl status >/dev/null 2>&1 || [[ -d /run/systemd/system ]]; then
            # Migrate any pre-existing user-mode unit (idempotent — no-op if absent).
            if [[ -f "$HOME/.config/systemd/user/dream-host-agent.service" ]]; then
                systemctl --user stop dream-host-agent.service 2>/dev/null || true
                systemctl --user disable dream-host-agent.service 2>/dev/null || true
                rm -f "$HOME/.config/systemd/user/dream-host-agent.service"
                systemctl --user daemon-reload 2>/dev/null || true
                ai_ok "Migrated host agent from --user mode to system mode"
            fi

            # System-mode install requires sudo. Fail fast in non-interactive
            # mode if passwordless sudo isn't available (mirrors phase 05).
            if [[ "${INTERACTIVE:-true}" != "true" ]] && ! sudo -n true 2>/dev/null; then
                ai_bad "Host agent install requires sudo and sudo requires a password."
                ai_bad "In non-interactive mode, either:"
                ai "  1. Run with passwordless sudo (NOPASSWD in sudoers)"
                ai "  2. Run the installer interactively (without --non-interactive)"
                error "Cannot install host agent system unit without sudo in non-interactive mode."
            fi

            # Determine the user that should own the running agent. Under
            # `sudo bash install.sh`, $(whoami) returns root — wrong; we want
            # the original invoking user. SUDO_USER is set by sudo and is
            # empty otherwise. INSTALL_USER override wins if explicitly set.
            if [[ -n "${INSTALL_USER:-}" ]]; then
                _agent_user="$INSTALL_USER"
            elif [[ -n "${SUDO_USER:-}" ]]; then
                _agent_user="$SUDO_USER"
            else
                _agent_user="$(whoami)"
            fi

            # Surface (don't block) the case where the agent will run as root.
            # Happens under `sudo su` → `bash install.sh` (SUDO_USER unset, whoami=root).
            # Some appliance/single-user installs may want this; warn so the operator
            # can override with INSTALL_USER if it's unintentional.
            if [[ "$_agent_user" == "root" ]]; then
                ai_warn "Resolved install user is 'root' — host agent will run as root."
                ai_warn "  Set INSTALL_USER=<non-root user> before re-running install if this is unintentional."
            fi

            if [[ -f "$INSTALL_DIR/scripts/systemd/dream-host-agent.service" ]]; then
                svc_tmp="/tmp/dream-host-agent.service.$$"
                cp "$INSTALL_DIR/scripts/systemd/dream-host-agent.service" "$svc_tmp"
                # Substitute placeholders — use sed directly with | delimiter
                # (paths contain / but never |, so | is a safe delimiter).
                # Dual-form for BSD/GNU sed compatibility.
                sed -i "s|__INSTALL_DIR__|${INSTALL_DIR}|g" "$svc_tmp" 2>/dev/null || \
                    sed -i '' "s|__INSTALL_DIR__|${INSTALL_DIR}|g" "$svc_tmp"
                sed -i "s|__HOME__|${HOME}|g" "$svc_tmp" 2>/dev/null || \
                    sed -i '' "s|__HOME__|${HOME}|g" "$svc_tmp"
                sed -i "s|__PYTHON3__|${AGENT_PYTHON}|g" "$svc_tmp" 2>/dev/null || \
                    sed -i '' "s|__PYTHON3__|${AGENT_PYTHON}|g" "$svc_tmp"
                sed -i "s|__INSTALL_USER__|${_agent_user}|g" "$svc_tmp" 2>/dev/null || \
                    sed -i '' "s|__INSTALL_USER__|${_agent_user}|g" "$svc_tmp"
                # Verify placeholders were actually rendered
                if grep -q '__INSTALL_DIR__\|__HOME__\|__PYTHON3__\|__INSTALL_USER__' "$svc_tmp"; then
                    ai_warn "Host agent systemd unit has unrendered placeholders — check $svc_tmp"
                else
                    sudo install -m 644 "$svc_tmp" /etc/systemd/system/dream-host-agent.service
                fi
                rm -f "$svc_tmp"
            fi
            sudo systemctl daemon-reload 2>/dev/null || true
            # Pipe through tee (matching the file's existing sudo+log idiom at L31-45)
            # so the redirect runs in the user's shell rather than under sudo (avoids
            # SC2024). pipefail is set in install-core.sh, so the if branches on the
            # actual systemctl exit status, not tee's.
            if sudo systemctl enable --now dream-host-agent.service 2>&1 | tee -a "$LOG_FILE" >/dev/null; then
                ai_ok "Dream host agent installed (systemd system-mode, user=${_agent_user}, port 7710)"
            else
                ai_warn "Dream host agent service failed to start — run: dream agent start"
            fi
            # Force-restart so the running process matches the binary the installer
            # just rewrote. enable --now is a no-op when the unit was already active,
            # which would leave an old daemon holding a deleted inode and serving
            # stale code after a reinstall. See issue #334. Use is-enabled (not
            # is-active) so a temporarily-down daemon during a fresh install still
            # triggers the restart rather than skipping it.
            if sudo systemctl is-enabled dream-host-agent.service >/dev/null 2>&1; then
                if sudo systemctl restart dream-host-agent.service 2>&1 | tee -a "$LOG_FILE" >/dev/null; then
                    ai_ok "Dream host agent restarted (loaded new binary)"
                else
                    ai_warn "Dream host agent restart failed (non-fatal) — run: sudo systemctl restart dream-host-agent.service"
                fi
            fi
            # loginctl enable-linger no longer needed for host agent (system-mode unit)

        else
            ai_warn "No systemd detected — dream host agent not auto-installed."
            ai_warn "  Start manually: dream agent start"
        fi
    else
        ai_warn "python3 not found — dream host agent not installed"
    fi
fi

# ── Dream mDNS Announcer (publishes dream.local on the LAN) ──
# Makes the device discoverable from any phone/laptop on the same network
# without typing an IP. See docs/MDNS.md for details. Linux-only; macOS
# announces hostname.local automatically via Bonjour, the script is a no-op
# there. Windows support TBD.
if [[ -f "$INSTALL_DIR/bin/dream-mdns.py" ]] && [[ "$(uname -s)" == "Linux" ]]; then
    # Install python3-zeroconf via the system package manager. Non-fatal —
    # mDNS is a quality-of-life feature; if zeroconf isn't available the
    # device is still reachable by IP.
    #
    # Two-tier strategy:
    #   1. Try the distro package manager first (best: integrates with apt
    #      upgrades, doesn't require pip / network in offline mode).
    #   2. Fall back to `pip install --user zeroconf` if the package isn't
    #      in the distro's repos (e.g. minimal images, stale apt cache,
    #      Ubuntu universe disabled) — without this, the install logs a
    #      warning and the mDNS announcer never starts even though the
    #      Python module is one pip away.
    _install_zeroconf_via_pkg() {
        case "$PKG_MANAGER" in
            apt)    sudo apt-get install -y python3-zeroconf 2>&1 | tee -a "$LOG_FILE" ;;
            dnf)    sudo dnf install -y python3-zeroconf 2>&1 | tee -a "$LOG_FILE" ;;
            pacman) sudo pacman -S --noconfirm --needed python-zeroconf 2>&1 | tee -a "$LOG_FILE" ;;
            zypper) sudo zypper --non-interactive install python3-zeroconf 2>&1 | tee -a "$LOG_FILE" ;;
            *)      return 99 ;;
        esac
    }
    _install_zeroconf_via_pip() {
        # `--user` writes into ~/.local/lib/python3.x/site-packages so we
        # don't need sudo and don't fight PEP 668 (Debian/Ubuntu mark the
        # system site-packages as externally-managed). The mDNS announcer
        # runs as $USER, not root, so --user is the right install scope.
        if command -v pip3 >/dev/null 2>&1; then
            pip3 install --user --quiet --no-warn-script-location zeroconf 2>&1 | tee -a "$LOG_FILE"
        else
            return 99
        fi
    }
    if ! python3 -c "import zeroconf" 2>/dev/null; then
        ai "Installing python3-zeroconf (for mDNS announcer)..."
        if _install_zeroconf_via_pkg && python3 -c "import zeroconf" 2>/dev/null; then
            ai_ok "Installed python3-zeroconf via $PKG_MANAGER"
        elif python3 -c "import zeroconf" 2>/dev/null; then
            : # Package manager returned non-zero, but the module is importable.
        elif _install_zeroconf_via_pip && python3 -c "import zeroconf" 2>/dev/null; then
            ai_ok "Installed zeroconf via pip --user (system package manager unavailable / failed)"
        else
            ai_warn "Failed to install zeroconf via $PKG_MANAGER AND pip --user — mDNS announcer will not start (non-fatal; device still reachable by IP)"
        fi
    fi

    # Install the systemd unit alongside dream-host-agent. Reuses the same
    # __PLACEHOLDER__ substitution pattern, the same user resolution
    # (INSTALL_USER → SUDO_USER → whoami), and the same sudo discipline.
    if python3 -c "import zeroconf" 2>/dev/null && \
       (systemctl status >/dev/null 2>&1 || [[ -d /run/systemd/system ]]) && \
       [[ -f "$INSTALL_DIR/scripts/systemd/dream-mdns.service" ]]; then
        MDNS_PYTHON="$(command -v python3)"
        # Reuse $_agent_user from the host-agent block above if set; otherwise
        # resolve fresh. Falls back to the same heuristic.
        if [[ -z "${_agent_user:-}" ]]; then
            if [[ -n "${INSTALL_USER:-}" ]]; then
                _agent_user="$INSTALL_USER"
            elif [[ -n "${SUDO_USER:-}" ]]; then
                _agent_user="$SUDO_USER"
            else
                _agent_user="$(whoami)"
            fi
        fi
        svc_tmp="/tmp/dream-mdns.service.$$"
        cp "$INSTALL_DIR/scripts/systemd/dream-mdns.service" "$svc_tmp"
        sed -i "s|__INSTALL_DIR__|${INSTALL_DIR}|g" "$svc_tmp" 2>/dev/null || \
            sed -i '' "s|__INSTALL_DIR__|${INSTALL_DIR}|g" "$svc_tmp"
        sed -i "s|__HOME__|${HOME}|g" "$svc_tmp" 2>/dev/null || \
            sed -i '' "s|__HOME__|${HOME}|g" "$svc_tmp"
        sed -i "s|__PYTHON3__|${MDNS_PYTHON}|g" "$svc_tmp" 2>/dev/null || \
            sed -i '' "s|__PYTHON3__|${MDNS_PYTHON}|g" "$svc_tmp"
        sed -i "s|__INSTALL_USER__|${_agent_user}|g" "$svc_tmp" 2>/dev/null || \
            sed -i '' "s|__INSTALL_USER__|${_agent_user}|g" "$svc_tmp"
        if grep -q '__INSTALL_DIR__\|__HOME__\|__PYTHON3__\|__INSTALL_USER__' "$svc_tmp"; then
            ai_warn "dream-mdns systemd unit has unrendered placeholders — check $svc_tmp"
        else
            sudo install -m 644 "$svc_tmp" /etc/systemd/system/dream-mdns.service
            sudo systemctl daemon-reload 2>/dev/null || true
            if sudo systemctl enable --now dream-mdns.service 2>&1 | tee -a "$LOG_FILE" >/dev/null; then
                _device_name="$(grep -E '^DREAM_DEVICE_NAME=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '"' || true)"
                _device_name="${_device_name:-dream}"
                # Be honest about what mDNS does on its own: it publishes the
                # name. Whether that name LOADS anything in a browser depends
                # on dream-proxy being on :80 and DREAM_PROXY_BIND=0.0.0.0. Those
                # are operator choices made elsewhere (and surfaced in the
                # first-boot wizard); don't claim the URL works yet.
                ai_ok "Dream mDNS announcer installed — '${_device_name}.local' now resolves on the LAN"
                ai "  Enable dream-proxy (DREAM_PROXY_BIND defaults to 0.0.0.0) to make http://${_device_name}.local serve chat. See docs/DREAM-PROXY.md."
            else
                ai_warn "Dream mDNS announcer failed to start (non-fatal — device is still reachable by IP)"
            fi
        fi
        rm -f "$svc_tmp"
    fi
fi
