# ODS — Developer Targets
# Run `make help` to see available commands.

SHELL_FILES := $(shell find . -name '*.sh' -not -path './node_modules/*' -not -path './.git/*' -not -path './data/*' -not -path './token-spy/*')

.PHONY: help lint test bats smoke simulate fleet-distros fleet-vms verify-bootstrap gate doctor

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

lint: ## Syntax check all shell scripts + Python compile check
	@echo "=== Shell syntax ==="
	@fail=0; for f in $(SHELL_FILES); do bash -n "$$f" || fail=1; done; [ $$fail -eq 0 ]
	@echo "=== Python compile ==="
	@python3 -m py_compile extensions/services/dashboard-api/main.py extensions/services/dashboard-api/agent_monitor.py
	@echo "All lint checks passed."

test: ## Run unit and contract tests
	@echo "=== Install documentation contract ==="
	@bash tests/test-install-docs.sh
	@bash tests/test-hosted-bootstrap-verifier.sh
	@echo ""
	@echo "=== Tier map tests ==="
	@bash tests/test-tier-map.sh
	@bash tests/test-installer-context-parity.sh
	@bash tests/test-hermes-context-floor.sh
	@echo ""
	@echo "=== Installer contracts ==="
	@bash tests/contracts/test-installer-contracts.sh
	@bash tests/contracts/test-preflight-fixtures.sh
	@bash tests/test-preflight-resolver-bug.sh
	@bash tests/test-linux-cloud-mode.sh
	@echo ""
	@echo "=== Linux install preflight ==="
	@bash tests/test-linux-install-preflight.sh
	@bash tests/test-linux-host-agent-stop.sh
	@bash tests/test-preflight-dashboard-port.sh
	@echo ""
	@echo "=== AMD/Lemonade contracts ==="
	@bash tests/contracts/test-amd-lemonade-contracts.sh
	@bash tests/test-litellm-amd-auth-enforced.sh
	@bash tests/contracts/test-windows-strix-tier-map.sh
	@bash tests/contracts/test-windows-llm-model-readiness.sh
	@bash tests/contracts/test-windows-lemonade-swap-wait.sh
	@bash tests/contracts/test-windows-hermes-config-patching.sh
	@echo ""
	@echo "=== Overlay/plist contracts ==="
	@bash tests/contracts/test-overlay-map-coherence.sh
	@bash tests/contracts/test-plist-log-paths.sh
	@echo ""
	@echo "=== safe .env loading (quote/CRLF handling) ==="
	@bash tests/test-safe-env.sh
	@echo ""
	@echo "=== Bind address sweep ==="
	@bash tests/test-bind-address-sweep.sh
	@bash tests/test-n8n-cookie-policy.sh
	@echo ""
	@echo "=== config migration (version check under set -e) ==="
	@bash tests/test-migrate-config.sh
	@echo ""
	@echo "=== ods-cli pipefail tolerance ==="
	@bash tests/test-ods-cli-pipefail-tolerance.sh
	@echo ""
	@echo "=== Bash 3.2 guard contracts ==="
	@bash tests/test-bash32-guards.sh
	@echo ""
	@echo "=== CLI update post-verification ==="
	@bash tests/test-cli-update-verification.sh
	@echo ""
	@echo "=== ods config show secret-mask matrix ==="
	@bash tests/test-ods-config-secret-mask.sh
	@echo ""
	@echo "=== macOS ods config show secret-mask ==="
	@bash tests/test-macos-config-show-secret-mask.sh
	@echo ""
	@echo "=== CLI preset restore user extensions ==="
	@bash tests/test-cli-preset-restore-user-extensions.sh
	@echo ""
	@echo "=== bootstrap OpenClaw compose-guard regression ==="
	@bash tests/test-bootstrap-openclaw-compose-guard.sh
	@echo ""
	@echo "=== ods-update.sh backup command ==="
	@bash tests/test-update-script-backup.sh
	@echo ""
	@echo "=== bootstrap-upgrade spawn closes inherited FDs ==="
	@bash tests/test-bootstrap-upgrade-close-inherited-fds.sh
	@echo ""
	@echo "=== bootstrap-upgrade compose-flags recovery ==="
	@bash tests/test-bootstrap-upgrade-compose-flags-recovery.sh
	@echo ""
	@echo "=== macOS ods-host-agent bootstrap verification ==="
	@bash tests/test-macos-host-agent-verification.sh
	@echo ""
	@echo "=== macOS model download retry contract ==="
	@bash tests/test-macos-download-retries.sh
	@echo ""
	@echo "=== macOS CLI .env quote handling ==="
	@bash tests/test-macos-env-quote-strip.sh
	@echo ""
	@echo "=== macOS uninstall LaunchAgent cleanup ==="
	@bash tests/test-macos-uninstall-launchagents.sh
	@echo ""
	@echo "=== fleet-multi-distro keep-going on docker pull failure ==="
	@bash tests/test-fleet-multi-distro-keep-going.sh
	@echo ""
	@echo "=== fleet host lock behavior ==="
	@bash tests/test-fleet-host-lock.sh
	@echo ""
	@echo "=== issue-to-pr security check ==="
	@bash tests/test-issue-to-pr-security.sh

bats: ## Run BATS unit tests for shell libraries
	@echo "=== BATS unit tests ==="
	@bash tests/run-bats.sh

smoke: ## Run platform smoke tests
	@echo "=== Smoke tests ==="
	@bash tests/smoke/linux-amd.sh
	@bash tests/smoke/linux-nvidia.sh
	@bash tests/smoke/wsl-logic.sh
	@bash tests/smoke/macos-dispatch.sh
	@echo "All smoke tests passed."

simulate: ## Run installer simulation harness
	@bash scripts/simulate-installers.sh

fleet-distros: ## Run disposable Docker multi-distro fleet checks
	@bash tests/fleet-multi-distro.sh

fleet-vms: ## Run disposable Incus VM fleet checks
	@bash tests/fleet-incus-vm.sh

verify-bootstrap: ## Verify hosted bootstrap against EXPECTED_REF (defaults to HEAD)
	@bash scripts/verify-hosted-bootstrap.sh "$${EXPECTED_REF:-$$(git rev-parse HEAD)}" $${ENDPOINTS:-}

doctor: ## Run diagnostic report
	@bash scripts/ods-doctor.sh

gate: lint test bats smoke simulate ## Full pre-release validation (lint + test + bats + smoke + simulate)
	@echo ""
	@echo "Release gate passed."
