# Dream Server — Developer Targets
# Run `make help` to see available commands.

SHELL_FILES := $(shell find . -name '*.sh' -not -path './node_modules/*' -not -path './.git/*' -not -path './data/*' -not -path './token-spy/*')

.PHONY: help lint test bats smoke simulate fleet-distros gate doctor

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
	@echo "=== Tier map tests ==="
	@bash tests/test-tier-map.sh
	@echo ""
	@echo "=== Installer contracts ==="
	@bash tests/contracts/test-installer-contracts.sh
	@bash tests/contracts/test-preflight-fixtures.sh
	@echo ""
	@echo "=== Linux install preflight ==="
	@bash tests/test-linux-install-preflight.sh
	@echo ""
	@echo "=== AMD/Lemonade contracts ==="
	@bash tests/contracts/test-amd-lemonade-contracts.sh
	@bash tests/test-litellm-amd-auth-enforced.sh
	@echo ""
	@echo "=== Overlay/plist contracts ==="
	@bash tests/contracts/test-overlay-map-coherence.sh
	@bash tests/contracts/test-plist-log-paths.sh
	@echo ""
	@echo "=== Bind address sweep ==="
	@bash tests/test-bind-address-sweep.sh
	@echo ""
	@echo "=== dream-cli pipefail tolerance ==="
	@bash tests/test-dream-cli-pipefail-tolerance.sh
	@echo ""
	@echo "=== Bash 3.2 guard contracts ==="
	@bash tests/test-bash32-guards.sh
	@echo ""
	@echo "=== dream config show secret-mask matrix ==="
	@bash tests/test-dream-config-secret-mask.sh
	@echo ""
	@echo "=== bootstrap OpenClaw compose-guard regression ==="
	@bash tests/test-bootstrap-openclaw-compose-guard.sh

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

doctor: ## Run diagnostic report
	@bash scripts/dream-doctor.sh

gate: lint test bats smoke simulate ## Full pre-release validation (lint + test + bats + smoke + simulate)
	@echo ""
	@echo "Release gate passed."
