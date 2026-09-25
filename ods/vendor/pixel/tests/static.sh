#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
# Test fixtures request exact public/private modes; do not inherit a collaborative host umask.
umask 022
# Normalize the scratch tmpdir so the full gate is reproducibly green regardless of the
# caller's TMPDIR: a TMPDIR under a private root (/home, /root, /run/user) would trip the
# ProtectHome service-unit guards, and a sandboxed TMPDIR that cannot host a Unix socket
# would break socket-identity fixtures. Run fixtures from a clean socket-capable scratch
# root outside the private roots.
case "${TMPDIR:-}" in
  ""|/home/*|/root/*|/run/user/*) export TMPDIR=/tmp ;;
esac

grep -Fq 'Copyright © 2026 Osmantic. All rights reserved except as expressly granted below.' LICENSE.md
grep -Fq 'solely as part of, or for developing, building,' LICENSE.md
grep -Fq 'This grant does not expand' LICENSE.md
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
for command in node python3 rg git; do command -v "$command" >/dev/null || { echo "Missing test command: $command" >&2; exit 1; }; done
node -e 'JSON.parse(require("fs").readFileSync("schemas/control-permission-settings-v1.schema.json","utf8"))'
for file in scripts/*.sh scripts/lib/*.sh deploy/ops-runner/*.sh deploy/ops-runner/run-test deploy/release-operator/*.sh deploy/work-controller/*.sh deploy/work-provider/*.sh tests/*.sh tests/fixtures/bin/* workspace-template/scripts/*.sh security-evals/deployment-isolation/*.sh security-evals/email-prompt-injection/*.sh security-evals/frontier-isolation/*.sh security-evals/limb-kit-isolation/*.sh security-evals/operations-live/*.sh security-evals/operations-pressure/*.sh security-evals/runner-boundary/*.sh security-evals/session-isolation/*.sh pixel; do bash -n "$file"; done
for route in work-fleet work-fleet-cleanup work-fleet-host-evidence work-fleet-service; do
  grep -Fq "  ${route}) exec " pixel || { echo "Pixel CLI is missing the ${route} route" >&2; exit 1; }
done
./pixel help | grep -Fq 'work-fleet-service Render, inspect, install, activate, or remove one fleet-wide service'
./pixel help | grep -Fq 'outcome-task-admit Admit one exact private backend-neutral evaluation task'
./pixel help | grep -Fq 'release-qualification-execution-interruption-preview Inertly derive the exact terminal interruption hash for one post-start indeterminate qualification execution'
./pixel help | grep -Fq 'release-qualification-execution-interruption-record Write the two private immutable terminal interruption artifacts for one post-start indeterminate qualification execution'
bash -n scripts/generated/release.env
test "$(bash -c 'source scripts/lib/bootstrap-packages.sh; pixel_packages_for_commands age')" = age
test "$(bash -c 'source scripts/lib/bootstrap-packages.sh; pixel_packages_for_commands flock')" = util-linux
test "$(bash -c 'source scripts/lib/common.sh; fake_openclaw(){ printf "%s\n" "OpenClaw 2026.7.1-2"; }; OPENCLAW_BIN=fake_openclaw; pixel_openclaw_version')" = 2026.7.1-2
mapfile -t age_only < <(bash -c 'source scripts/lib/bootstrap-packages.sh; pixel_packages_for_commands age')
[[ " ${age_only[*]} " != *" docker.io "* ]] || { echo "Installing age unexpectedly requests Docker" >&2; exit 1; }
mapfile -t age_and_docker < <(bash -c 'source scripts/lib/bootstrap-packages.sh; pixel_packages_for_commands age docker')
[[ " ${age_and_docker[*]} " == *" age "* && " ${age_and_docker[*]} " == *" docker.io "* ]]
for file in scripts/*.mjs scripts/lib/*.mjs deploy/work-broker/*.mjs deploy/work-controller/*.mjs deploy/work-runner/*.mjs deploy/work-model-proxy/*.mjs deploy/work-provider/*.mjs deploy/work-provider/adapters/*.mjs deploy/work-provider-router/*.mjs deploy/work-research-broker/*.mjs deploy/work-codex-provider/*.mjs plugin/*.mjs plugin/*.js plugin-ops/*.js plugin-frontier/*.js; do node --check "$file"; done
node --check tests/fixtures/work/fake-llama-responses.mjs
node --check tests/fixtures/work/fake-llama-builder.mjs
node --check tests/fixtures/work/fake-llama-builder-loop.mjs
node --check tests/fixtures/work/fake-llama-data-lab.mjs
node --check deploy/work-provider/provider-smoke-cli-test.mjs
node --check deploy/work-provider/provider-smoke-core.mjs
node --check tests/fixtures/work/fake-mcp-stdio.mjs
node --check tests/fixtures/work/knowledge-backup-probe.mjs
node --check deploy/agent-comparison/inference-boundary.mjs
node --check deploy/agent-comparison/research-mcp-server.mjs
node --check deploy/agent-comparison/codex-research-authority.mjs
node --check deploy/agent-comparison/research-fixture-adapter.mjs
node --check deploy/agent-comparison/assistant-model-proxy.mjs
node --check tests/fixtures/agent-comparison/responses-shape-capture.mjs
node --check tests/fixtures/agent-comparison/synthetic-inference-boundary.mjs
node --check tests/work-scout-live.mjs
node --check tests/work-omp-model-rpc-live.mjs
node --check tests/work-builder-live.mjs
node --check tests/work-researcher-live.mjs
node --check tests/work-data-lab-live.mjs
node --check tests/fixtures/work/fake-llama-researcher.mjs
node --check tests/work-builder-loop-live.mjs
node --check tests/work-capability-retention-live.mjs
node --check scripts/work-capability-retention.mjs
node --check tests/work-capability-image-live.mjs
node --test plugin/oauth-security.test.mjs
node --test tests/web-browse.test.mjs
node --test tests/ops-shell-propose-schema.test.mjs
node --test scripts/upstream.test.mjs
node --test scripts/upstream-diff.test.mjs
node --test scripts/upstream-review.test.mjs
node --test scripts/secure-files.test.mjs tests/secure-plugin-read.test.mjs
node --test tests/json-schema.test.mjs
node --test tests/legacy-clean-migration-schema.test.mjs
node --test tests/deep-work-contract.test.mjs
node --test tests/deep-work-service-qualification.test.mjs
node --test tests/work-broker.test.mjs
node --test tests/work-research-broker.test.mjs
node --test tests/work-research-pressure.test.mjs
node --test tests/work-research-tool.test.mjs
node --test tests/work-research-revision-review.test.mjs
node --test tests/work-provider-registry.test.mjs tests/work-provider-adapters.test.mjs
node --test tests/work-provider-custody.test.mjs
node --test tests/work-provider-run-ledger.test.mjs
node --test tests/work-provider-run-store.test.mjs
node --test tests/work-provider-egress-proxy.test.mjs
node --test tests/work-provider-moonshot-transport.test.mjs
node --test tests/work-provider-transports.test.mjs
node --test tests/work-provider-equivalence.test.mjs
node --test tests/work-provider-moonshot-smoke.test.mjs tests/provider-ingress-smoke.test.mjs
node --test tests/work-provider-container-images.test.mjs
node --test tests/work-provider-router.test.mjs tests/work-provider-router-qualification.test.mjs
node --test tests/work-provider-executor.test.mjs tests/work-provider-local-lane.test.mjs
node --test tests/work-provider-multiturn.test.mjs
node --test tests/work-provider-harness.test.mjs tests/work-provider-adversarial.test.mjs tests/work-provider-patch-verifier.test.mjs
node --check tests/fixtures/work-provider-router.mjs
node -e 'for (const f of ["schemas/work-provider-profile-v1.schema.json","schemas/work-provider-receipt-v1.schema.json","schemas/work-provider-private-policy-v1.schema.json","schemas/work-provider-custody-receipt-v1.schema.json","schemas/work-provider-run-ledger-v1.schema.json","deploy/work-provider/private-policy.example.json",...require("fs").readdirSync("deploy/work-provider/profiles").filter((name)=>name.endsWith(".json")).map((name)=>"deploy/work-provider/profiles/"+name)]) JSON.parse(require("fs").readFileSync(f,"utf8"))'
node -e 'for (const f of ["schemas/work-provider-router-request-v1.schema.json","schemas/work-provider-router-qualification-v1.schema.json","schemas/work-provider-router-policy-v1.schema.json","schemas/work-provider-router-decision-v1.schema.json","deploy/work-provider-router/router-policy.example.json"]) JSON.parse(require("fs").readFileSync(f,"utf8"))'
node -e 'JSON.parse(require("fs").readFileSync("schemas/work-research-revision-history-v1.schema.json","utf8"))'
node -e 'JSON.parse(require("fs").readFileSync("schemas/portal-outcome-trial-coverage-v1.schema.json","utf8"))'
node --test tests/work-safe-tar.test.mjs
node --test tests/work-rpc-framing.test.mjs
node --test tests/work-rpc-client.test.mjs
node --test tests/work-runner-core.test.mjs
node --test tests/work-builder-volume.test.mjs
node --test tests/work-checkpoints.test.mjs
node --test tests/work-goals.test.mjs
node --test tests/work-input-pack.test.mjs
node --test tests/work-goal-draft.test.mjs
node --test tests/work-goal-assemble.test.mjs
node --test tests/work-goal-launch-prepare.test.mjs
node --test tests/work-goal-prepare.test.mjs
node --test tests/work-goal-controller-prepare.test.mjs
node --test tests/work-goal-stage.test.mjs
node --test tests/work-goal-fleet.test.mjs
node --test tests/work-goal-fleet-host-evidence.test.mjs
node --test tests/work-goal-fleet-service-units.test.mjs
node --test tests/work-goal-fleet-service-lifecycle.test.mjs
node --test tests/work-goal-cli.test.mjs
node --test tests/work-goal-runtime.test.mjs
node --test tests/work-goal-real-crash-e2e.test.mjs
node --test tests/work-goal-multi-day-soak.test.mjs
node --test tests/work-goal-multi-day-soak-service-units.test.mjs
node --test tests/work-goal-multi-day-soak-service-lifecycle.test.mjs
node --test tests/work-goal-builder-driver.test.mjs
node --test tests/work-candidate-wait-loop.test.mjs
node --test tests/work-goal-run-bundles.test.mjs
node --test tests/work-goal-service-units.test.mjs
node --test tests/work-goal-service-lifecycle.test.mjs
node --test tests/work-goal-continuous.test.mjs
node --test tests/work-model-qualification.test.mjs
node --test tests/work-model-qualification-runner.test.mjs
node --test tests/work-model-qualification-container.test.mjs
node --test tests/work-model-qualification-docker.test.mjs
node --test tests/maintenance-recovery-journal.test.mjs
node --test tests/maintenance-recovery-guardian.test.mjs
node --test tests/maintenance-recovery-guardian-systemd.test.mjs
node --test tests/maintenance-recovery-guardian-supervisor.test.mjs
node --test tests/maintenance-campaign-recovery-guardian-supervisor.test.mjs
node --test tests/maintenance-recovery-guardian-lease.test.mjs
node --test tests/maintenance-recovery-guardian-systemd-facts.test.mjs
node --test tests/work-model-qualification-maintenance.test.mjs
node --test tests/work-model-campaign-maintenance.test.mjs
node --test tests/maintenance-campaign-child-launcher.test.mjs
node --test tests/maintenance-campaign-child-supervisor.test.mjs
node --test tests/maintenance-secure-files.test.mjs
node --test tests/work-model-runtime-contract.test.mjs
node --test tests/work-omp-runtime.test.mjs
node --test tests/work-model-policy-cli.test.mjs
node --test tests/work-model-backend-cli.test.mjs
node --test tests/work-model-artifact.test.mjs
node --test tests/work-model-backend-launch.test.mjs
node --test tests/work-model-backend-lifecycle.test.mjs
node --test tests/work-model-backend-coordination.test.mjs
node --test tests/work-knowledge-vault-cli.test.mjs
node --test tests/work-knowledge-vault-guide.test.mjs
node --test tests/work-knowledge-vault-restore.test.mjs
node --test tests/work-goal-knowledge-runtime.test.mjs
node --test tests/work-watchdog.test.mjs
node --test tests/work-context-capsules.test.mjs
node --test tests/work-context-session-cli.test.mjs
node -e 'for (const f of ["schemas/work-context-session-input-v1.schema.json", "deploy/work-controller/context-session-input.example.json"]) JSON.parse(require("fs").readFileSync(f, "utf8"))'
node --test tests/work-capability-packs.test.mjs
node --test tests/work-capability-pack-installation.test.mjs
node --test tests/work-capability-v2-contract.test.mjs
node --test tests/work-capability-v2-installation.test.mjs
node --test tests/work-capability-image-admission.test.mjs
node --test tests/work-capability-health.test.mjs
node --test tests/work-capability-runtime.test.mjs
node --test tests/work-capability-controller.test.mjs
node --test tests/work-capability-tool.test.mjs
node --test tests/work-capability-profile-service.test.mjs
node --test tests/work-capability-runtime-v2.test.mjs
node --test tests/work-capability-runtime-v2-queue.test.mjs
node --test tests/work-capability-operational-v2.test.mjs
node --test tests/work-capability-tool-queue-v2.test.mjs
node --test tests/work-knowledge-vault.test.mjs
node --test tests/work-operator-status.test.mjs
node --test tests/work-goal-operator-status.test.mjs
node --test tests/work-goal-pause-cli.test.mjs
node --test tests/control-deep-work-pause-e2e.test.mjs
node --test tests/control-deep-work-draft-e2e.test.mjs
node --test tests/work-goal-resume-cli.test.mjs
node --test tests/work-goal-cancel-cli.test.mjs
node --test tests/work-goal-terminal-journey-e2e.test.mjs
node --test tests/work-codex-compiler.test.mjs
node --test tests/work-codex-pressure.test.mjs
node --test tests/work-codex-authentication.test.mjs
node --test tests/work-codex-credential-custody.test.mjs
node --test tests/work-codex-container-protocol.test.mjs
node --test tests/work-codex-container-entrypoint.test.mjs
node --test tests/work-codex-container-images.test.mjs
node --test tests/work-codex-egress-proxy.test.mjs
node --test tests/work-codex-isolated-cli.test.mjs
node --test tests/work-codex-execution.test.mjs
node --test tests/work-codex-cli-qualification.test.mjs
node --test tests/work-verifier-command.test.mjs
node --test tests/work-docker-verifier.test.mjs
node --test tests/work-docker-builder.test.mjs
node --test tests/work-docker-researcher.test.mjs
node --test tests/work-data-runtime.test.mjs
node --test tests/work-builder-runtime.test.mjs
node --test tests/work-capability-retention.test.mjs
node --test tests/work-data-artifacts.test.mjs
node --test tests/work-docker-data-lab.test.mjs
node --test tests/work-docker-scout.test.mjs
node --test tests/work-docker-boundary.test.mjs
node --test tests/work-omp-model-registry.test.mjs
node --test tests/work-model-proxy.test.mjs
node --test tests/frontier-finalize.test.mjs
node --test tests/client-kit.test.mjs
node --test tests/release-identity.test.mjs
node --test tests/runtime-attestation.test.mjs
node --test tests/portal-user-journey-corpus.test.mjs
node --test tests/portal-user-trial-journeys.test.mjs
node --test tests/agent-comparison-battery.test.mjs
node --test tests/agent-comparison-pixel-arm.test.mjs
node --test tests/agent-comparison-dsv4-runtime.test.mjs
node --test tests/agent-comparison-pixel-system.test.mjs
node --test tests/agent-comparison-inference-boundary.test.mjs
node --test tests/agent-comparison-research-mcp.test.mjs
node --test tests/agent-comparison-codex-research-authority.test.mjs
node -e 'for (const f of ["schemas/portal-outcome-pixel-system-v1.schema.json","schemas/portal-outcome-pair-system-v1.schema.json","schemas/portal-outcome-pair-system-binding-v1.schema.json","schemas/portal-outcome-pair-preflight-v1.schema.json","deploy/agent-comparison/pixel-system.example.json","deploy/agent-comparison/pair-system.example.json"]) JSON.parse(require("fs").readFileSync(f,"utf8"))'
node --test tests/supported-host-tmp-hygiene.test.mjs
node --test tests/control-access-adapter.test.mjs
grep -F 'if (status && !onlyPreparedFilesModified(status))' scripts/upstream.mjs >/dev/null
node -e 'for (const f of ["onboarding.example.json","RELEASE-MANIFEST.json","OPENCLAW-COMPATIBILITY.json","QUALIFICATION-MATRIX.json","scripts/generated/release-constants.json","plugin/package.json","plugin/package-lock.json","plugin/openclaw.plugin.json","plugin-ops/package.json","plugin-ops/package-lock.json","plugin-ops/openclaw.plugin.json","plugin-frontier/package.json","plugin-frontier/package-lock.json","plugin-frontier/openclaw.plugin.json","deploy/ops-broker/policy.example.json","deploy/frontier-broker/policy.example.json","deploy/frontier-broker/policy.chatgpt.example.json","deploy/ops-broker/action-packs.example.json","deploy/ops-runner/actions.example.json","deploy/ops-runner/managed.example.json","deploy/release-operator/config.example.json","security-evals/operations-live/fixtures/actions.json","security-evals/operations-live/fixtures/managed.json","schemas/release-manifest-v1.schema.json","schemas/openclaw-compatibility-v1.schema.json","schemas/qualification-matrix-v1.schema.json","schemas/supported-host-systemd-evidence-v1.schema.json","schemas/promotion-claims-v1.schema.json","schemas/promotion-readiness-v1.schema.json","schemas/portal-outcome-task-v1.schema.json","schemas/portal-outcome-task-admission-v1.schema.json","schemas/portal-outcome-model-contract-v1.schema.json","schemas/portal-outcome-inference-contract-v1.schema.json","schemas/portal-outcome-run-v1.schema.json","schemas/portal-outcome-comparison-v1.schema.json","schemas/portal-outcome-campaign-plan-v1.schema.json","schemas/portal-outcome-campaign-v1.schema.json","schemas/operations-policy-v2.schema.json","schemas/operations-authority-lease.schema.json","schemas/frontier-policy-v1.schema.json","schemas/frontier-request-v1.schema.json","schemas/frontier-policy-v2.schema.json","schemas/frontier-request-v2.schema.json","schemas/frontier-integration-v1.schema.json","schemas/frontier-authority-lease-v1.schema.json","schemas/frontier-live-authorization-v1.schema.json","schemas/frontier-live-receipt-v1.schema.json","schemas/control-onboarding-v1.schema.json","schemas/control-action-v1.schema.json","schemas/limb-pack-v1.schema.json","schemas/local-capability-pack-v1.schema.json","schemas/operations-action-pack-v1.schema.json","schemas/frontier-task-pack-v1.schema.json","profiles/capabilities/minimal.json","profiles/capabilities/chief-of-staff.json","profiles/capabilities/research.json","profiles/capabilities/engineering-operator.json"]) JSON.parse(require("fs").readFileSync(f,"utf8"))'
node -e 'for (const f of ["deploy/work-broker/policy.example.json","deploy/work-codex-provider/policy.example.json","deploy/work-codex-provider/egress-policy.example.json","deploy/work-runner/data-runtime.json","schemas/work-job-v1.schema.json","schemas/work-policy-v1.schema.json","schemas/work-plan-v1.schema.json","schemas/work-capability-lease-v1.schema.json","schemas/work-lease-consumption-v1.schema.json","schemas/work-lease-revocation-v1.schema.json","schemas/work-checkpoint-v1.schema.json","schemas/work-verification-evidence-v1.schema.json","schemas/work-model-capability-receipt-v1.schema.json","schemas/work-watchdog-decision-v1.schema.json","schemas/work-context-capsule-v1.schema.json","schemas/work-capability-pack-v1.schema.json","schemas/work-capability-pack-installation-v1.schema.json","schemas/work-capability-pack-removal-v1.schema.json","schemas/work-capability-image-admission-v1.schema.json","schemas/work-capability-image-revocation-v1.schema.json","schemas/work-capability-health-v1.schema.json","schemas/work-capability-runtime-v1.schema.json","schemas/work-capability-pack-operation-v1.schema.json","schemas/work-capability-grant-v1.schema.json","schemas/work-capability-consumption-v1.schema.json","schemas/work-capability-controller-policy-v1.schema.json","schemas/work-capability-job-authorization-v1.schema.json","schemas/work-capability-tool-request-v1.schema.json","schemas/work-capability-watchdog-event-v1.schema.json","schemas/work-capability-tool-response-v1.schema.json","schemas/work-capability-tool-custody-v1.schema.json","schemas/work-knowledge-ingestion-v1.schema.json","schemas/work-knowledge-source-v1.schema.json","schemas/work-knowledge-query-v1.schema.json","schemas/work-knowledge-retrieval-v1.schema.json","schemas/work-knowledge-deletion-v1.schema.json","schemas/work-knowledge-key-rotation-v1.schema.json","schemas/work-knowledge-reconciliation-v1.schema.json","schemas/work-operator-status-v1.schema.json","schemas/work-codex-policy-v1.schema.json","schemas/work-codex-request-v1.schema.json","schemas/work-codex-capsule-v1.schema.json","schemas/work-codex-plan-v1.schema.json","schemas/work-codex-output-v1.schema.json","schemas/work-codex-authorization-v1.schema.json","schemas/work-codex-authentication-evidence-v1.schema.json","schemas/work-codex-authentication-consumption-v1.schema.json","schemas/work-codex-credential-custody-v1.schema.json","schemas/work-codex-egress-policy-v1.schema.json","schemas/work-codex-execution-claim-v1.schema.json","schemas/work-codex-result-v1.schema.json","schemas/work-research-query-v1.schema.json","schemas/work-research-batch-v1.schema.json","schemas/work-research-retrieval-v1.schema.json","schemas/work-research-report-proposal-v1.schema.json","schemas/work-research-report-v1.schema.json","schemas/work-research-verification-v1.schema.json","schemas/work-research-revision-review-v1.schema.json","schemas/work-research-tool-request-v1.schema.json","schemas/work-research-tool-response-v1.schema.json","schemas/work-data-artifact-manifest-v1.schema.json","schemas/work-data-report-proposal-v1.schema.json","schemas/work-data-report-v1.schema.json","schemas/work-data-verification-v1.schema.json","schemas/work-data-runtime-v1.schema.json","schemas/work-result-v1.schema.json"]) JSON.parse(require("fs").readFileSync(f,"utf8"))'
node -e 'for (const f of ["schemas/control-status-v1.schema.json","schemas/control-doctor-v1.schema.json","schemas/control-diagnostics-v1.schema.json","schemas/control-update-status-v1.schema.json","schemas/control-recovery-guide-v1.schema.json","schemas/control-policy-v1.schema.json","schemas/control-frontier-review-v1.schema.json","schemas/control-frontier-budget-request-v1.schema.json","schemas/control-frontier-budget-proposal-v1.schema.json","schemas/control-chat-v1.schema.json","schemas/control-chat-handoff-receipt-v1.schema.json","schemas/control-chat-turn-request-v1.schema.json","schemas/control-access-adapter-v1.schema.json","schemas/control-approval-inbox-v1.schema.json","schemas/control-work-authoring-config-v1.schema.json","schemas/control-work-authoring-v1.schema.json","schemas/control-work-draft-request-v1.schema.json","schemas/control-work-draft-reviews-v1.schema.json","schemas/control-work-launch-config-v1.schema.json","schemas/control-work-service-config-v1.schema.json","schemas/control-work-semantic-review-v1.schema.json","schemas/release-update-v1.schema.json","schemas/release-update-stage-v1.schema.json","schemas/release-update-rehearsal-v1.schema.json","schemas/release-update-activation-v1.schema.json","schemas/release-update-activation-result-v1.schema.json","schemas/release-update-rollback-v1.schema.json","schemas/release-update-rollback-result-v1.schema.json","schemas/release-update-recovery-v1.schema.json","schemas/release-update-cleanup-v1.schema.json","schemas/release-update-qualification-harness-run-v1.schema.json","schemas/release-update-qualification-execution-tombstone-v1.schema.json","schemas/release-update-qualification-acquisition-v1.schema.json","schemas/release-update-qualification-execution-claim-v1.schema.json","schemas/release-update-qualification-execution-result-v1.schema.json","schemas/release-update-qualification-execution-result-marker-v1.schema.json","schemas/release-update-qualification-execution-observation-v1.schema.json","schemas/release-update-qualification-execution-interruption-v1.schema.json","schemas/release-update-qualification-execution-interruption-marker-v1.schema.json","control/policy.example.json","control/access-adapter.example.json","control/work-authoring.example.json","control/work-launch.example.json","control/work-service.example.json"]) JSON.parse(require("fs").readFileSync(f,"utf8"))'
node -e 'for (const f of ["schemas/release-update-archive-v1.schema.json","schemas/release-update-reactivation-archive-v1.schema.json"]) JSON.parse(require("fs").readFileSync(f,"utf8"))'
node -e 'for (const f of ["schemas/work-goal-v1.schema.json","schemas/work-goal-declaration-v1.schema.json","schemas/work-goal-checkpoint-v1.schema.json","schemas/work-goal-fleet-v1.schema.json","schemas/work-goal-fleet-checkpoint-v1.schema.json","schemas/work-goal-fleet-controller-v1.schema.json","schemas/work-goal-fleet-controller-v2.schema.json","schemas/work-goal-fleet-host-evidence-v1.schema.json","schemas/work-goal-fleet-host-evidence-v2.schema.json","schemas/work-goal-fleet-host-probe-v1.schema.json","schemas/work-goal-run-bundle-v1.schema.json","schemas/work-goal-controller-v1.schema.json","schemas/work-semantic-acceptance-v1.schema.json","schemas/work-scout-report-proposal-v1.schema.json","schemas/work-scout-report-v1.schema.json","schemas/work-scout-verification-v1.schema.json"]) JSON.parse(require("fs").readFileSync(f,"utf8"))'
node -e 'for (const f of ["schemas/work-input-selection-v1.schema.json","schemas/work-input-pack-v1.schema.json","schemas/work-goal-brief-v1.schema.json","schemas/work-input-catalog-v1.schema.json","schemas/work-goal-draft-v1.schema.json","schemas/work-goal-bundle-v1.schema.json","schemas/work-goal-assembly-v1.schema.json","schemas/work-goal-launch-preparation-v1.schema.json","deploy/work-controller/input-selection.example.json","deploy/work-controller/input-selection-data.example.json","deploy/work-controller/goal-brief.example.json","deploy/work-controller/goal-brief-data.example.json","deploy/work-controller/input-catalog.example.json","deploy/work-controller/goal-declaration.example.json"]) JSON.parse(require("fs").readFileSync(f,"utf8"))'
node -e 'for (const f of ["schemas/deep-work-endurance-evidence-v1.schema.json","schemas/deep-work-multi-day-soak-v1.schema.json","schemas/deep-work-multi-day-soak-invocation-v1.schema.json","schemas/deep-work-multi-day-soak-evidence-v1.schema.json"]) JSON.parse(require("fs").readFileSync(f,"utf8"))'
node -e 'for (const f of ["schemas/work-model-policy-review-v1.schema.json","schemas/work-model-operator-status-v1.schema.json","schemas/work-model-backend-status-v1.schema.json","schemas/work-model-artifact-manifest-v1.schema.json","schemas/work-model-runtime-cache-manifest-v1.schema.json","schemas/work-model-backend-config-v1.schema.json","schemas/work-model-backend-review-v1.schema.json","schemas/work-model-backend-launch-v1.schema.json","schemas/work-model-backend-lifecycle-review-v1.schema.json","schemas/work-model-backend-lifecycle-receipt-v1.schema.json","schemas/work-model-backend-halt-review-v1.schema.json","schemas/work-model-backend-halt-receipt-v1.schema.json","schemas/work-model-qualification-docker-v1.schema.json","schemas/work-model-qualification-maintenance-v1.schema.json","schemas/work-model-campaign-maintenance-v1.schema.json","deploy/work-controller/model-backend-llama.example.json","deploy/work-controller/model-backend-vllm.example.json","deploy/work-controller/model-qualification-docker.example.json","deploy/work-controller/model-qualification-maintenance.example.json","deploy/work-controller/model-campaign-maintenance.example.json"]) JSON.parse(require("fs").readFileSync(f,"utf8"))'
node -e 'for (const f of ["schemas/work-goal-controller-environment-v1.schema.json","schemas/work-goal-controller-bundle-v1.schema.json","deploy/work-controller/goal-controller-environment.example.json","deploy/work-controller/goal-controller-capability-environment.example.json","deploy/work-controller/goal-controller-knowledge-environment.example.json"]) JSON.parse(require("fs").readFileSync(f,"utf8"))'
node -e 'JSON.parse(require("fs").readFileSync("schemas/work-goal-stage-v1.schema.json","utf8"))'
node -e 'for (const f of ["deploy/work-runner/builder-runtime.json","schemas/work-builder-runtime-v1.schema.json"]) JSON.parse(require("fs").readFileSync(f,"utf8"))'
node -e 'for (const f of ["security-evals/assurance/builder-capability-corpus-v1.json","schemas/work-capability-retention-evidence-v1.schema.json"]) JSON.parse(require("fs").readFileSync(f,"utf8"))'
node -e 'for (const f of ["security-evals/portal-user-journeys/corpus-v1.json","schemas/portal-user-journey-corpus-v1.schema.json","schemas/release-identity-v1.schema.json","schemas/runtime-attestation-v1.schema.json","schemas/external-action-journal-event-v1.schema.json"]) JSON.parse(require("fs").readFileSync(f,"utf8"))'
node -e 'for (const f of ["schemas/github-action-policy-v1.schema.json","schemas/github-action-proposal-v1.schema.json","schemas/github-action-result-v1.schema.json","deploy/github-broker/policy.example.json","deploy/github-broker/proposal.example.json"]) JSON.parse(require("fs").readFileSync(f,"utf8"))'
node -e 'JSON.parse(require("fs").readFileSync("schemas/work-capability-tool-catalog-v1.schema.json","utf8"))'
node -e 'for (const f of ["schemas/work-capability-pack-v2.schema.json","schemas/work-capability-tool-catalog-v2.schema.json","schemas/work-capability-controller-policy-v2.schema.json","schemas/work-capability-job-authorization-v2.schema.json","schemas/work-capability-tool-request-v2.schema.json","schemas/work-capability-grant-v2.schema.json","schemas/work-capability-lease-v2.schema.json","schemas/work-capability-runtime-v2.schema.json","schemas/work-capability-consumption-v2.schema.json"]) JSON.parse(require("fs").readFileSync(f,"utf8"))'
node -e 'const manifest=JSON.parse(require("fs").readFileSync("RELEASE-MANIFEST.json","utf8")); const boundary="admission-only-no-runtime-no-tool-call-no-network-no-external-effects"; if (!manifest.deepWorkCapability || manifest.deepWorkCapability.runtimeEnabled !== false || manifest.deepWorkCapability.admissionBoundary !== boundary) throw new Error("release manifest deepWorkCapability must remain admission-only and runtime-disabled")'
node -e 'const schema=JSON.parse(require("fs").readFileSync("schemas/work-capability-runtime-v2.schema.json","utf8")); const properties=schema.properties; const authority=properties.authority.const; if (properties.enabled.const !== false || properties.result.properties.externalEffects.const !== false || properties.container.properties.network.const !== "none" || !authority || Object.values(authority).some((value) => value !== false)) throw new Error("v2 capability runtime schema granted execution, effects, network, or authority")'
node --check deploy/work-runner/scout-report.mjs
node --check deploy/work-controller/goal-review-cli.mjs
node -e 'JSON.parse(require("fs").readFileSync("schemas/portal-outcome-tuning-baseline-freeze-v1.schema.json","utf8"))'
node -e 'for (const f of ["schemas/pixel-sealed-corpus-commitment-v1.schema.json","schemas/pixel-sealed-corpus-reveal-v1.schema.json","schemas/pixel-sealed-corpus-freeze-v1.schema.json","schemas/pixel-sealed-corpus-reveal-receipt-v1.schema.json"]) JSON.parse(require("fs").readFileSync(f,"utf8"))'
node scripts/check-release-contract.mjs
python3 scripts/lib/bundle-brokers.py --check
if grep -F 'Activation and update-specific automatic rollback are still under development' UPGRADE.md; then
  echo "UPGRADE.md restored obsolete pre-activation guidance" >&2
  exit 1
fi
grep -F './pixel update-activate --preview' UPGRADE.md >/dev/null
grep -F './pixel update-rollback --preview' UPGRADE.md >/dev/null
grep -F './pixel update-recover --preview' UPGRADE.md >/dev/null
grep -F './pixel update-cleanup --preview' UPGRADE.md >/dev/null
grep -Fx 'export PYTHONDONTWRITEBYTECODE=1' pixel >/dev/null
grep -Fx 'export PYTHONDONTWRITEBYTECODE=1' scripts/lib/common.sh >/dev/null
grep -Fx 'export PYTHONDONTWRITEBYTECODE=1' tests/run.sh >/dev/null
grep -Fx 'export PYTHONDONTWRITEBYTECODE=1' scripts/ci-release-gate-inner.sh >/dev/null
grep -Fx 'export PYTHONDONTWRITEBYTECODE=1' scripts/check-no-secrets.sh >/dev/null
grep -F 'python" -B -m pip check' scripts/verify.sh >/dev/null
grep -F "pixel_release_tree_sha \"\$active_release\"" scripts/verify.sh >/dev/null
python_compile_cache=$(mktemp -d)
trap 'rm -rf -- "$python_compile_cache"' EXIT
PYTHONPYCACHEPREFIX="$python_compile_cache" python3 -m py_compile control/server.py control/doctor.py deploy/web-courier/courier.py deploy/action_journal/__init__.py deploy/action_journal/cli.py deploy/source-broker/broker.py deploy/github-broker/broker.py deploy/ops-broker/broker.py deploy/frontier-broker/broker.py deploy/ops-runner/dispatch.py deploy/ops-runner/receive-artifact.py deploy/ops-runner/action.py deploy/ops-runner/managed.py deploy/release-operator/dispatch.py deploy/release-operator/managed.py deploy/release-operator/client.py deploy/release-operator/pixel_release_grammar.py scripts/agent_comparison_fixture_tool.py scripts/audit-private-backup.py scripts/restore-receipt.py scripts/restore-migration-journal.py scripts/extract-upstream-package.py scripts/frontier_budget.py scripts/frontier-live-qualify.py scripts/pixel-doctor.py scripts/promotion_readiness.py scripts/portal_outcome_evaluation.py scripts/portal_outcome_campaign.py scripts/portal_outcome_pair.py scripts/portal_outcome_pair_preflight.py scripts/portal_outcome_policy_compatibility.py scripts/portal_outcome_battery_campaign.py scripts/portal_outcome_livesystem.py scripts/portal_outcome_product_path.py scripts/portal_outcome_research_evidence.py scripts/portal_outcome_assistant_evidence.py scripts/portal_outcome_assistant_runtime.py scripts/portal_outcome_assistant_system.py scripts/portal_outcome_trial_coverage.py scripts/portal_outcome_pixel_livesystem.py scripts/portal_outcome_pixel_orchestrate.py scripts/release-update.py scripts/qualification-host-execution.py scripts/supported-host-systemd-probe.py scripts/upstream-attestation.py scripts/upstream-runtime-probe.py scripts/snapshot-proposal.py scripts/verify-plugin-archive.py scripts/verify-frontier-codex.py scripts/limb-kit.py deploy/mesh/install.py deploy/mesh/migrate_discord.py deploy/mesh/pixel_mesh_peer.py workspace-template/scripts/downscale.py workspace-template/scripts/research-ledger.py security-evals/assurance/audit_source.py security-evals/assurance/audit_remote_refs.py security-evals/assurance/manifest.py security-evals/action-journal-pressure/fuzz.py security-evals/control-boundary/run-live.py security-evals/email-prompt-injection/render-cases.py security-evals/email-prompt-injection/evaluate.py security-evals/frontier-pressure/fuzz.py security-evals/modular-e2e/render-cases.py security-evals/modular-e2e/evaluate.py security-evals/operations-live/render-cases.py security-evals/operations-live/run-cases.py security-evals/operations-live/evaluate.py security-evals/operations-pressure/fuzz.py security-evals/operations-pressure/race.py security-evals/qualification-pressure/fuzz.py security-evals/source-pressure/fuzz.py
PYTHONPYCACHEPREFIX="$python_compile_cache" python3 -m py_compile scripts/portal_outcome_runtime_control.py
rm -rf -- "$python_compile_cache"
trap - EXIT
python3 -m unittest tests/test_agent_comparison_fixture_tool.py tests/test_assurance.py tests/test_backup_audit.py tests/test_control_server.py tests/test_doctor.py tests/test_frontier_budget.py tests/test_plugin_archive.py tests/test_plugin_publish.py tests/test_promotion_readiness.py tests/test_legacy_clean_migration.py tests/test_restore_receipt.py tests/test_restore_migration_journal_hardening.py tests/test_restore_migration_journal_deployment.py tests/test_restore_migration_journal_install.py tests/test_restore_migration_journal_folded.py tests/test_migration_e2e.py tests/test_portal_outcome_task.py tests/test_portal_outcome_runner.py tests/test_portal_outcome_verifier.py tests/test_portal_outcome_orchestrate.py tests/test_portal_outcome_livesystem.py tests/test_portal_outcome_pixel_livesystem.py tests/test_portal_outcome_pixel_orchestrate.py tests/test_portal_outcome_pair.py tests/test_portal_outcome_pair_system.py tests/test_portal_outcome_pair_preflight.py tests/test_portal_outcome_policy_compatibility.py tests/test_portal_outcome_battery_campaign.py tests/test_portal_outcome_sealed_corpus.py tests/test_portal_outcome_evaluation.py tests/test_portal_outcome_campaign.py tests/test_scan_history_secrets.py tests/test_release_artifacts.py tests/test_release_update.py tests/test_release_update_interruption.py tests/test_qualification_host_execution.py tests/test_snapshot_proposal.py tests/test_source_audit.py tests/test_source_pressure.py tests/test_supported_host_systemd.py tests/test_web_courier.py tests/test_action_journal.py tests/test_source_broker.py tests/test_github_broker.py tests/test_ops_broker.py tests/test_frontier_broker.py tests/test_frontier_live_qualification.py tests/test_verify_frontier_codex.py tests/test_ops_runner.py tests/test_release_operator.py tests/test_pixel_operator.py tests/test_pixel_provisioning_harness.py tests/test_runtime_snapshot_hermetic.py tests/test_email_prompt_injection.py tests/test_human_approval.py tests/test_modular_e2e.py tests/test_operations_live.py tests/test_release_generation.py tests/test_release_provider_runtime.py tests/test_upstream_attestation.py tests/test_upstream_ci.py tests/test_upstream_extraction.py tests/test_upstream_runtime_probe.py tests/test_limb_kit.py
bash tests/legacy-clean-migration-shell.test.sh
bash tests/legacy-clean-migration-transaction.test.sh
bash tests/legacy-clean-migration-swap.test.sh
bash tests/restore-broker-reader-acls.test.sh
bash tests/restore-private-state-receipt-gate.test.sh
bash tests/restore-private-state-version-gate.test.sh
bash tests/migrate-prepare.test.sh
bash tests/bootstrap-mirror-fallback.test.sh
bash tests/browse-canary.test.sh
bash tests/broker-bytes-transaction.test.sh
bash tests/vendor-secrets-install.test.sh
bash tests/qualification-candidate-probe.test.sh
python3 -m unittest tests/test_portal_outcome_runtime_control.py
python3 -m unittest tests/test_agent_comparison_rehearsals.py
python3 -m unittest tests/test_portal_outcome_product_path.py
python3 -m unittest tests/test_portal_outcome_assistant_evidence.py tests/test_portal_outcome_assistant_runtime.py tests/test_portal_outcome_assistant_system.py
python3 -m unittest tests/test_portal_outcome_trial_coverage.py
python3 -m py_compile tests/test_control_ui.py tests/control_chat_handoff_ui_fixture.py security-evals/control-pressure/fuzz.py
python3 -m unittest tests/test_mesh_installer.py tests/test_mesh_discord_migration.py tests/test_mesh_peer.py
python3 scripts/scan-history-secrets.py . >/dev/null
python3 -m unittest tests/test_control_ui.py
python3 security-evals/assurance/audit_source.py >/dev/null
python3 security-evals/action-journal-pressure/fuzz.py >/dev/null
python3 security-evals/control-pressure/fuzz.py >/dev/null
python3 security-evals/frontier-pressure/fuzz.py >/dev/null
python3 security-evals/limb-kit-pressure/fuzz.py >/dev/null
python3 security-evals/qualification-pressure/fuzz.py >/dev/null
bash scripts/check-no-secrets.sh
grep -F 'setfacl -m' scripts/lib/broker-reader-acls.sh >/dev/null
grep -F 'pixel_user_can_access' scripts/lib/broker-reader-acls.sh scripts/install-ops-broker.sh scripts/install-frontier-broker.sh scripts/install-source-broker.sh >/dev/null
# shellcheck disable=SC2016
grep -F 'sudo -u "$user" /bin/bash -c "$expression" pixel-access-probe "$path"' scripts/lib/common.sh >/dev/null
if rg -n 'sudo -u .*test (?:! )?-[rw]' scripts --glob '*.sh'; then echo "Production permission probes still depend on external test -r/-w" >&2; exit 1; fi
grep -F 'pixel_apply_ops_reader_acls' scripts/install-ops-broker.sh >/dev/null
grep -F 'pixel_apply_frontier_reader_acls' scripts/install-frontier-broker.sh >/dev/null
grep -F "systemctl restart \"\$PIXEL_OPS_BROKER_UNIT\"" scripts/install-ops-broker.sh >/dev/null
grep -F 'Gateway owner can read Operations authority state' scripts/install-ops-broker.sh >/dev/null
grep -F ".schemaVersion == 2" scripts/verify.sh >/dev/null
grep -F 'PIXEL_OPS_BROKER_STATE_DIR' scripts/backup-private-state.sh >/dev/null
grep -F 'PIXEL_FRONTIER_BROKER_STATE_DIR' scripts/backup-private-state.sh >/dev/null
grep -F 'PIXEL_CONTROL_POLICY_PATH' scripts/backup-private-state.sh scripts/restore-private-state.sh >/dev/null
grep -F 'PIXEL_DEEP_WORK_BACKUP_ENABLED' scripts/backup-private-state.sh scripts/restore-private-state.sh >/dev/null
grep -F 'knowledge-vault-restore-cli.mjs' scripts/restore-private-state.sh >/dev/null
grep -F 'The external knowledge-vault credential must be outside every captured backup root' scripts/backup-private-state.sh >/dev/null
grep -F 'pixel-work-knowledge-setup-review' deploy/work-controller/knowledge-vault-cli.mjs >/dev/null
grep -F 'pixel-work-knowledge-guide' deploy/work-controller/knowledge-vault-guide.mjs >/dev/null
grep -F 'knowledge-vault-systemd-credential' scripts/supported-host-systemd-probe.py >/dev/null
grep -F 'verify-frontier-codex.py' scripts/install-frontier-broker.sh >/dev/null
grep -F 'refusing to mislabel API billing as subscription usage' scripts/install-frontier-broker.sh >/dev/null
grep -F -- '--authorize-one-synthetic-provider-call' scripts/frontier-live-qualify.py >/dev/null
grep -F 'qualification-authorizations' deploy/frontier-broker/broker.py >/dev/null
grep -F -- '--usage-refresh' scripts/install-frontier-broker.sh >/dev/null
# shellcheck disable=SC2016
grep -F 'expected_url="https://nodejs.org/dist/v${node_version}/node-v${node_version}-linux-x64.tar.xz"' .github/workflows/security.yml >/dev/null
grep -F 'sha256sum -c -' .github/workflows/security.yml >/dev/null
grep -F '[[ ! -e /usr/bin/node && ! -L /usr/bin/node ]]' .github/workflows/security.yml >/dev/null
# shellcheck disable=SC2016
grep -F 'sudo install -o root -g root -m 0755 "$node_root/bin/node" /usr/bin/node' .github/workflows/security.yml >/dev/null
grep -F "[[ \$(stat -c '%u:%g:%a:%h' /usr/bin/node) == '0:0:755:1' ]]" .github/workflows/security.yml >/dev/null
[[ $(grep -Fc 'persist-credentials: false' .github/workflows/security.yml) == 3 ]] || { echo "Every security workflow checkout must discard Git credentials" >&2; exit 1; }
if grep -F 'actions/setup-node@' .github/workflows/security.yml; then echo "Security workflow uses a multi-link hosted Node cache" >&2; exit 1; fi
grep -F 'apt-get install -y age ca-certificates' .github/workflows/security.yml >/dev/null
grep -F 'ExecStartPre=' scripts/configure.mjs | grep -F 'verify-codex.py' >/dev/null
docker_codex_version=$(sed -n 's/^ARG CODEX_VERSION=//p' security-evals/frontier-isolation/Dockerfile)
runner_codex_version=$(sed -n 's/^CODEX_VERSION=.*:-\([^}]*\)}$/\1/p' security-evals/frontier-isolation/run.sh)
[[ "$docker_codex_version" == 0.147.0 && "$runner_codex_version" == "$docker_codex_version" ]] || {
  echo "Frontier isolation Codex version pin drifted" >&2
  exit 1
}
grep -F '/run/pixel-frontier-operator/grant.XXXXXXXXXX.json' scripts/frontier-authority.sh >/dev/null
if grep -F '.grant-input-$$' scripts/frontier-authority.sh; then echo "Frontier grant staging is predictable inside broker-writable state" >&2; exit 1; fi
grep -F 'PIXEL_SOURCE_BROKER_STATE_DIR' scripts/backup-private-state.sh >/dev/null
if grep -F 'node:child_process' plugin/index.js; then echo "Source plugin gained process execution" >&2; exit 1; fi
if grep -F 'node:child_process' plugin-frontier/index.js; then echo "Frontier plugin gained process execution" >&2; exit 1; fi
if rg -n 'shell=True|os\.system|subprocess\.(?:run|Popen)\([^\n]*(?:shell|\bcmd\b)' control/server.py; then echo "Local control gained a generic shell surface" >&2; exit 1; fi
grep -F 'control listener must be exactly 127.0.0.1' control/server.py >/dev/null
grep -F 'credentialsExposed": False' control/server.py >/dev/null
grep -F -- '--drain-direct' scripts/configure.mjs >/dev/null
grep -F '| age --encrypt --recipient' scripts/backup-private-state.sh >/dev/null
grep -F 'audit-private-backup.py' scripts/restore-private-state.sh >/dev/null
grep -F 'ssh-keygen -Y verify' scripts/restore-private-state.sh >/dev/null
# shellcheck disable=SC2016
grep -F 'privileged_path_exists() { sudo test -e "$1" || sudo test -L "$1"; }' scripts/restore-private-state.sh >/dev/null
# shellcheck disable=SC2016
grep -F 'source "$ROOT/scripts/lib/broker-reader-acls.sh"' scripts/restore-private-state.sh scripts/install-ops-broker.sh scripts/install-frontier-broker.sh >/dev/null
grep -F 'pixel_apply_broker_reader_acls' scripts/restore-private-state.sh >/dev/null
acl_line=$(grep -n '^pixel_apply_broker_reader_acls$' scripts/restore-private-state.sh | cut -d: -f1)
finalize_line=$(grep -n 'scripts/restore-migration-journal.py" finalize' scripts/restore-private-state.sh | head -1 | cut -d: -f1)
# shellcheck disable=SC2016
verify_line=$(grep -n 'bash "$ROOT/scripts/verify.sh"' scripts/restore-private-state.sh | head -1 | cut -d: -f1)
if [[ -z "$acl_line" || -z "$finalize_line" || -z "$verify_line" || "$acl_line" -le "$finalize_line" || "$acl_line" -ge "$verify_line" ]]; then
  echo "Broker reader ACLs must be applied after service finalization and before verification" >&2; exit 1
fi
# shellcheck disable=SC2016
grep -F 'privileged_path_exists "$source_path" || pixel_die "Staged restore root is missing"' scripts/restore-private-state.sh >/dev/null
grep -F 'pixel_acquire_deployment_lock exclusive' scripts/apply.sh scripts/rollback.sh scripts/restore-private-state.sh scripts/rotate-gateway-token.sh >/dev/null
# shellcheck disable=SC2016
grep -F 'rm -f -- "$check"' scripts/apply.sh >/dev/null
grep -F 'pixel_acquire_deployment_lock shared' scripts/backup-private-state.sh >/dev/null
grep -F 'openssl rand -hex 32' scripts/rotate-gateway-token.sh >/dev/null
grep -F -- '--require-hashes' scripts/bootstrap.sh scripts/preflight.sh scripts/apply.sh >/dev/null
# shellcheck disable=SC2016
grep -F '"$openclaw_bin" plugins install --pin --force "$package_name@$version"' scripts/bootstrap.sh >/dev/null
# shellcheck disable=SC2016
grep -F -- '--allowed-peer "openclaw@$peer_version=$openclaw_root"' scripts/lib/common.sh >/dev/null
# shellcheck disable=SC2016
grep -F 'PIXEL_PLUGIN_RECEIPT_MODE=record OPENCLAW_BIN=$openclaw_bin' scripts/bootstrap.sh >/dev/null
# shellcheck disable=SC2016
grep -F -- '--build-arg "PIXEL_SANDBOX_UID=$sandbox_uid"' scripts/bootstrap.sh >/dev/null
# shellcheck disable=SC2016
grep -F 'org.osmantic.pixel.sandbox-uid=$sandbox_uid' scripts/bootstrap.sh >/dev/null
grep -F 'ARG PIXEL_SANDBOX_UID=1000' deploy/sandbox/Dockerfile >/dev/null
# shellcheck disable=SC2016
grep -F 'LABEL org.osmantic.pixel.sandbox-uid="$PIXEL_SANDBOX_UID"' deploy/sandbox/Dockerfile >/dev/null
# Assert the literal shell variable is absent.
# shellcheck disable=SC2016
if grep -F 'tar -C / -czf "$archive"' scripts/backup-private-state.sh; then echo "Private backup writes plaintext" >&2; exit 1; fi
if grep -Eq 'usermod.*PIXEL_OPS_(BROKER_GROUP|READER_USER)' scripts/install-ops-broker.sh; then echo "Gateway must not join the Operations authority group" >&2; exit 1; fi
if rg -n 'pixel-runner[[:space:]]+ALL=\(root\)' scripts deploy; then echo "Untrusted workload user regained root sudo authority" >&2; exit 1; fi
grep -F 'pixel-ops-transport ALL=(root)' scripts/configure-ops-target-actions.sh >/dev/null
grep -F 'User pixel-ops-transport' scripts/provision-ops-target.sh >/dev/null
if rg -n -- '--update=none' scripts security-evals; then echo "Unsupported Debian 12 cp option detected" >&2; exit 1; fi
# shellcheck disable=SC2016
grep -F 'cp -a -n -- "$ROOT/.generated/workspace/." "$PIXEL_WORKSPACE/"' scripts/apply.sh >/dev/null
# shellcheck disable=SC2016
grep -F '(cd dist && sha256sum -c "$(basename "$archive").sha256")' scripts/ci-release-gate-inner.sh >/dev/null
# shellcheck disable=SC2016
grep -F '(cd dist && sha256sum -c "$(basename "$sbom").sha256")' scripts/ci-release-gate-inner.sh >/dev/null
# shellcheck disable=SC2016
grep -F '(cd dist && sha256sum -c "$(basename "$provenance").sha256")' scripts/ci-release-gate-inner.sh >/dev/null
# shellcheck disable=SC2016
grep -F '(cd dist && sha256sum -c "$(basename "$update").sha256")' scripts/ci-release-gate-inner.sh >/dev/null
# shellcheck disable=SC2016
grep -F '[[ $(uname -s) == Linux ]] || pixel_die "Release packaging requires Linux so archive ownership and executable modes are deterministic"' scripts/package-release.sh >/dev/null
grep -F 'Archive the existing release signature before rebuilding this version' scripts/package-release.sh >/dev/null
grep -F 'pixel_acquire_deployment_lock exclusive' scripts/prepare-release-update.sh >/dev/null
grep -F 'candidateCodeExtracted": False' scripts/release-update.py >/dev/null
grep -F 'pixel_acquire_deployment_lock exclusive' scripts/rehearse-release-update.sh >/dev/null
grep -F 'candidateCodeExecuted": False' scripts/release-update.py >/dev/null
grep -F 'activeDeploymentChanged": False' scripts/release-update.py >/dev/null
grep -F 'pixel_acquire_deployment_lock exclusive' scripts/activate-release-update.sh >/dev/null
grep -F 'activation-claim' scripts/activate-release-update.sh >/dev/null
grep -F 'activation-result' scripts/activate-release-update.sh >/dev/null
grep -F 'pixel_acquire_deployment_lock exclusive' scripts/rollback-release-update.sh >/dev/null
grep -F 'rollback-claim' scripts/rollback-release-update.sh >/dev/null
grep -F 'rollback-result' scripts/rollback-release-update.sh >/dev/null
grep -F 'pixel_acquire_deployment_lock exclusive' scripts/recover-release-update.sh >/dev/null
grep -F 'recovery-preview' scripts/recover-release-update.sh >/dev/null
grep -F 'recovery-finalize' scripts/recover-release-update.sh >/dev/null
grep -F 'pixel_acquire_deployment_lock exclusive' scripts/cleanup-release-update.sh >/dev/null
grep -F 'cleanup-preview' scripts/cleanup-release-update.sh >/dev/null
grep -F 'cleanup-hash' scripts/cleanup-release-update.sh >/dev/null
grep -F 'pixel_acquire_deployment_lock exclusive' scripts/archive-release-update.sh >/dev/null
grep -F 'archive-preview' scripts/archive-release-update.sh >/dev/null
grep -F 'archive-hash' scripts/archive-release-update.sh >/dev/null
# shellcheck disable=SC2016
grep -F 'git clone --quiet --no-local "$ROOT" "$staging_root/source"' scripts/run-debian-release-gate.sh >/dev/null
# shellcheck disable=SC2016
grep -F 'mount_root=$(cygpath -w "$mount_root")' scripts/run-debian-release-gate.sh >/dev/null
grep -F 'MSYS_NO_PATHCONV=1 docker run --rm' scripts/run-debian-release-gate.sh >/dev/null
grep -F 'MSYS_NO_PATHCONV=1 docker run --rm' security-evals/frontier-isolation/run.sh >/dev/null
# shellcheck disable=SC2016
grep -F -- '--mount "type=bind,source=$mount_root,target=/input,readonly"' scripts/run-debian-release-gate.sh >/dev/null
grep -F 'age ca-certificates curl git jq openssh-client python3 python3-jsonschema python3-pil ripgrep shellcheck sudo systemd xz-utils' scripts/run-debian-release-gate.sh >/dev/null
grep -F 'chmod -R go-w /src' scripts/run-debian-release-gate.sh >/dev/null
if rg -nU '\r$' --glob '!*.png' --glob '!*.jpg' .; then echo "CRLF line endings detected" >&2; exit 1; fi
if rg -n 'workProvider(Executor|Harness)TestOnly' deploy --glob '*.mjs' --glob '!executor.mjs' --glob '!harness.mjs' --glob '!equivalence-runner.mjs'; then
  echo "Production modules may not import test-only provider seams" >&2
  exit 1
fi
if rg -n 'writeCredentialExclusiveTestOnly' deploy --glob '*.mjs' --glob '!provider-credential-ingress-test.mjs'; then
  echo "Production modules may not import the credential ingress test seam" >&2
  exit 1
fi
if rg -n 'remoteProviderTransportTestOnly' deploy --glob '*.mjs' --glob '!generic-remote-transport.mjs'; then
  echo "Production modules may not import the remote transport test seam" >&2
  exit 1
fi
if rg -n 'moonshotTransportTestOnly' deploy --glob '*.mjs' --glob '!moonshot-transport.mjs'; then
  echo "Production modules may not import the Moonshot transport test seam" >&2
  exit 1
fi
if rg -n 'providerSmokeTestOnly|runProviderSmokeTestOnly' deploy --glob '*.mjs' --glob '!provider-smoke-cli-test.mjs'; then
  echo "Production modules may not import the provider smoke test seam" >&2
  exit 1
fi
if rg -n 'ProviderSmokeCoreError' deploy --glob '*.mjs' --glob '!provider-smoke-core.mjs' --glob '!provider-smoke-cli-test.mjs'; then
  echo "Production modules may not import the internal smoke core error" >&2
  exit 1
fi
if rg -n 'localProviderTransportTestOnly' deploy --glob '*.mjs' --glob '!local-transport.mjs'; then
  echo "Production modules may not import the local transport test seam" >&2
  exit 1
fi
if rg -n 'workProvider(Equivalence|Qualification)TestSeam' deploy --glob '*.mjs' --glob '!equivalence-runner.mjs' --glob '!qualification-runner.mjs'; then
  echo "Production modules may not import qualification orchestration test seams" >&2
  exit 1
fi
if rg -n 'provider-selected-model' deploy/work-provider/profiles; then
  echo "provider-selected-model placeholder remains in a provider profile" >&2
  exit 1
fi
# The v2 queue test-support runner is test-only: production modules must never
# import it, and the public processing path must not accept a caller runtime or
# transition seam.
if rg -n '\.test-support\.mjs' deploy --glob '*.mjs' --glob '!*.test-support.mjs'; then
  echo "Production modules may not import the v2 queue test-support runner" >&2
  exit 1
fi
if rg -n 'dependencyOverrides' deploy/work-controller/capability-tool-queue-v2.mjs; then
  echo "Public v2 queue processing path must not accept dependencyOverrides" >&2
  exit 1
fi
# Clean-migration custody/journal safety: the privileged descriptor-bound helper is the
# single authority for custody setup, reservation, arming, and safe abort. The removed
# unsafe patterns (raw sudo mkdir/chmod of the custody dir, GNU-install overwrite of the
# reserved inode, and a non-root availability claim inside the root-0700 custody) must not
# return.
# shellcheck disable=SC2016
if grep -F 'sudo mkdir -p -- "$MIGRATION_CUSTODY"' scripts/restore-private-state.sh; then
  echo "Privileged custody setup must go through the descriptor-bound helper" >&2; exit 1
fi
# shellcheck disable=SC2016
if grep -F 'sudo chmod 700 "$MIGRATION_CUSTODY"' scripts/restore-private-state.sh; then
  echo "Privileged custody setup must go through the descriptor-bound helper" >&2; exit 1
fi
if grep -F 'sudo install -o root -g root -m 600' scripts/restore-private-state.sh; then
  echo "Journal arming must never GNU-install over the reserved inode" >&2; exit 1
fi
# shellcheck disable=SC2016
if grep -F '[[ ! -e "$migration_journal" && ! -L "$migration_journal" ]]' scripts/restore-private-state.sh; then
  echo "Non-root orchestrator must not claim availability inside root-0700 custody" >&2; exit 1
fi
# shellcheck disable=SC2016
grep -Fq 'scripts/restore-migration-journal.py" reserve' scripts/restore-private-state.sh
# shellcheck disable=SC2016
grep -Fq 'scripts/restore-migration-journal.py" arm' scripts/restore-private-state.sh
# Supplemental static ordering evidence (the authoritative proof is the dynamic SIGKILL
# regression test): the full transaction journal must be armed BEFORE the first live
# destination rename, so a hard kill (SIGKILL/power loss) always leaves an armed journal for
# deterministic resumable rollback. This guards against an accidental reordering of the arm
# call back into the migration branch (after the swap).
arm_line=$(grep -n 'scripts/restore-migration-journal.py" arm' scripts/restore-private-state.sh | head -1 | cut -d: -f1)
first_mutation_line=$(grep -n 'live_mutation_started=1' scripts/restore-private-state.sh | head -1 | cut -d: -f1)
if [[ -z "$arm_line" || -z "$first_mutation_line" || "$arm_line" -ge "$first_mutation_line" ]]; then
  echo "Migration journal must be armed before the first live destination rename" >&2; exit 1
fi
# The shell must not embed a duplicate journal control parser/state machine; all commit/
# rollback/inspect exact-key validation, locking, progress, and finalization live in the
# single privileged helper. The old `sudo python3 - "$control_mode" "$journal" <<'PY'`
# embedded parser must not return.
# shellcheck disable=SC2016 # The grep needle is intentionally literal source text.
if grep -F 'sudo python3 - "$control_mode" "$journal" <<' scripts/restore-private-state.sh; then
  echo "Migration journal control must not be an embedded duplicate parser in the shell" >&2; exit 1
fi
# Finding 9 static guard: on a pre-arm helper abort failure the reservation must be marked
# retained BEFORE cleanup runs, so cleanup's own guarded abort cannot silently retry and
# make the retained diagnostic stale/ambiguous. The retained marker is the explicit
# recovery authority. The abort-failure echo must sit between a retain_reservation=1 line
# and the cleanup call, all within on_exit.
on_exit_start=$(grep -n '^on_exit()' scripts/restore-private-state.sh | head -1 | cut -d: -f1)
abort_fail_echo=$(grep -n 'could not restore the exact service prestate' scripts/restore-private-state.sh | cut -d: -f1)
on_exit_retain=$(awk -v start="$on_exit_start" 'NR>start && /retain_reservation=1/ {print NR; exit}' scripts/restore-private-state.sh)
on_exit_cleanup=$(awk -v start="$on_exit_start" 'NR>start && /^  cleanup$/ {print NR; exit}' scripts/restore-private-state.sh)
if [[ -z "$on_exit_start" || -z "$abort_fail_echo" || -z "$on_exit_retain" || -z "$on_exit_cleanup" || \
  "$abort_fail_echo" -le "$on_exit_retain" || "$on_exit_retain" -ge "$on_exit_cleanup" ]]; then
  echo "Pre-arm abort failure must set retain_reservation=1 before cleanup" >&2; exit 1
fi
# The privileged reservation must never implicitly reclaim a stale same-contract marker
# (there is no stale reclaim in normal activation) and must not use the old reservationId
# field: reserve is O_EXCL-only and ownership is bound by the reservation token. The docstring
# and the O_EXCL refusal message legitimately mention "reclaim" to state the policy, so the
# guard targets the actual reclaim code path (the former reclaim_fd logic) rather than the word.
if grep -F 'reclaim_fd' scripts/restore-migration-journal.py; then
  echo "Privileged reservation must not implicitly reclaim any pre-existing marker" >&2; exit 1
fi
if grep -F 'reservationId' scripts/restore-migration-journal.py; then
  echo "Privileged reservation must use the cryptographic reservation token, not reservationId" >&2; exit 1
fi
# Rollback recognition uses exact inode/device/type evidence, not tree content hashing.
if grep -F 'tree_sha256' scripts/restore-migration-journal.py; then
  echo "Rollback evidence must not rely on tree content hashing" >&2; exit 1
fi
# No production owner override or test-only override may exist anywhere in the production
# restore path.
if grep -F 'PIXEL_MIGRATION_JOURNAL_OWNER' scripts/restore-private-state.sh; then
  echo "Production owner override was not removed" >&2; exit 1
fi
# The privileged helper must never accept a runtime environment tool override and must
# never execute privileged tools through a caller-controlled PATH or nested sudo: it runs as
# the root euid and invokes only fixed absolute trusted binaries (/usr/bin/systemctl,
# /usr/bin/rm, /usr/bin/mv) directly.
if grep -F 'PIXEL_SYSTEMCTL_BIN' scripts/restore-migration-journal.py; then
  echo "Privileged helper must not read a systemctl environment override" >&2; exit 1
fi
if grep -F 'os.environ' scripts/restore-migration-journal.py; then
  echo "Privileged helper must not accept environment tool overrides" >&2; exit 1
fi
if grep -F 'shutil.which' scripts/restore-migration-journal.py; then
  echo "Privileged helper must not resolve tools through PATH" >&2; exit 1
fi
if grep -F '"sudo"' scripts/restore-migration-journal.py; then
  echo "Privileged helper must not nest sudo/PATH tool execution" >&2; exit 1
fi
# Journal state transitions must be crash-safe atomic replacements, never in-place ftruncate
# (a power loss between truncate and fsync could destroy the only rollback state).
if grep -F 'os.ftruncate' scripts/restore-migration-journal.py; then
  echo "Journal state transitions must be atomic replacements, never in-place ftruncate" >&2; exit 1
fi
# The arm unit predicate must be the exact OR-based validator (a broken 'and not' expression
# accepts every string); arm reuses the same commit/rollback validator.
if grep -F 'not isinstance(unit, str) and not' scripts/restore-migration-journal.py; then
  echo "Arm unit predicate must not accept every string via a broken and-not expression" >&2; exit 1
fi
for file in .node-version scripts/generated/release.env deploy/ops-runner/run-test deploy/ops-runner/receive-artifact.py plugin-frontier/index.js; do
  [[ $(git check-attr eol -- "$file") == "$file: eol: lf" ]] || { echo "$file must be LF on every checkout" >&2; exit 1; }
done
pixel_version=$(tr -d '[:space:]' < VERSION)
manifest_version=$(node -p 'require("./RELEASE-MANIFEST.json").pixel')
[[ "$pixel_version" == "$manifest_version" ]]
echo "Static checks passed."
