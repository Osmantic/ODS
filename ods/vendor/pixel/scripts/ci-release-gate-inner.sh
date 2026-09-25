#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
git config --global --add safe.directory "$ROOT"

shellcheck -x pixel scripts/*.sh scripts/lib/*.sh tests/*.sh workspace-template/scripts/*.sh
shellcheck -x security-evals/limb-kit-isolation/*.sh
shellcheck -x -e SC2024 security-evals/frontier-isolation/*.sh
bash tests/run.sh

for package in plugin plugin-ops plugin-frontier; do
  npm ci --prefix "$package" --ignore-scripts
  npm run check --prefix "$package"
  npm audit --prefix "$package" --omit=dev --audit-level=high
done

bash pixel package
version=$(tr -d '[:space:]' < VERSION)
archive="dist/pixel-$version.tar.gz"
sbom="dist/pixel-$version.cdx.json"
provenance="dist/pixel-$version.intoto.jsonl"
update="dist/pixel-$version.update.json"
for member in control/server.py control/doctor.py control/policy.example.json control/ui/index.html control/ui/app.js control/ui/styles.css client-overlay.example.json QUALIFICATION-MATRIX.json schemas/client-overlay-v1.schema.json schemas/qualification-matrix-v1.schema.json schemas/promotion-claims-v1.schema.json schemas/promotion-readiness-v1.schema.json schemas/control-onboarding-v1.schema.json schemas/control-action-v1.schema.json schemas/control-status-v1.schema.json schemas/control-update-status-v1.schema.json schemas/control-recovery-guide-v1.schema.json schemas/control-doctor-v1.schema.json schemas/control-diagnostics-v1.schema.json schemas/control-policy-v1.schema.json schemas/control-frontier-review-v1.schema.json schemas/control-frontier-budget-request-v1.schema.json schemas/control-frontier-budget-proposal-v1.schema.json schemas/frontier-live-authorization-v1.schema.json schemas/frontier-live-receipt-v1.schema.json schemas/work-goal-v1.schema.json schemas/work-goal-checkpoint-v1.schema.json schemas/work-goal-run-bundle-v1.schema.json schemas/work-lease-revocation-v1.schema.json schemas/work-codex-policy-v1.schema.json schemas/work-codex-request-v1.schema.json schemas/work-codex-capsule-v1.schema.json schemas/work-codex-plan-v1.schema.json schemas/work-codex-output-v1.schema.json schemas/work-codex-authorization-v1.schema.json schemas/work-codex-authentication-evidence-v1.schema.json schemas/work-codex-authentication-consumption-v1.schema.json schemas/work-codex-credential-custody-v1.schema.json schemas/work-codex-egress-policy-v1.schema.json schemas/work-codex-execution-claim-v1.schema.json schemas/work-codex-result-v1.schema.json schemas/release-update-v1.schema.json schemas/release-update-stage-v1.schema.json schemas/release-update-rehearsal-v1.schema.json schemas/release-update-activation-v1.schema.json schemas/release-update-activation-result-v1.schema.json schemas/release-update-rollback-v1.schema.json schemas/release-update-rollback-result-v1.schema.json schemas/release-update-recovery-v1.schema.json schemas/release-update-cleanup-v1.schema.json deploy/work-controller/goals.mjs deploy/work-controller/goal-cli.mjs deploy/work-controller/goal-runtime.mjs deploy/work-controller/goal-builder-driver.mjs deploy/work-controller/goal-builder-preparer.mjs deploy/work-controller/goal-builder-resolver.mjs deploy/work-controller/goal-run-bundles.mjs deploy/work-codex-provider/compiler.mjs deploy/work-codex-provider/authentication.mjs deploy/work-codex-provider/credential-custody.mjs deploy/work-codex-provider/container-protocol.mjs deploy/work-codex-provider/container-entrypoint.mjs deploy/work-codex-provider/egress-proxy.mjs deploy/work-codex-provider/isolated-codex-cli.mjs deploy/work-codex-provider/execution.mjs deploy/work-codex-provider/codex-cli-qualification.mjs deploy/work-codex-provider/policy.example.json deploy/work-codex-provider/egress-policy.example.json deploy/work-codex-provider/Dockerfile deploy/work-codex-provider/Dockerfile.egress-proxy tests/work-goals.test.mjs tests/work-goal-cli.test.mjs tests/work-goal-runtime.test.mjs tests/work-goal-builder-driver.test.mjs tests/work-goal-run-bundles.test.mjs tests/work-codex-container-images.test.mjs scripts/qualify-work-codex-images.sh scripts/client-kit.mjs scripts/promotion_readiness.py scripts/frontier_budget.py scripts/frontier-live-qualify.py scripts/frontier-live-qualify.sh scripts/pixel-doctor.py scripts/generate-release-update.mjs scripts/release-update.py scripts/prepare-release-update.sh scripts/rehearse-release-update.sh scripts/activate-release-update.sh scripts/rollback-release-update.sh scripts/recover-release-update.sh scripts/cleanup-release-update.sh CODEX-WORK-PROVIDER.md CLIENT-KIT.md QUALIFICATION.md FRONTIER-LIVE-QUALIFICATION.md FRONTIER-LIMB.md CONTROL-SURFACE.md DEPLOYMENT.md LICENSE.md; do
  tar -tzf "$archive" | grep -Fx "pixel-$version/$member" >/dev/null || { echo "Release archive is missing required product member: $member" >&2; exit 1; }
done
for member in schemas/release-update-archive-v1.schema.json scripts/archive-release-update.sh; do
  tar -tzf "$archive" | grep -Fx "pixel-$version/$member" >/dev/null || { echo "Release archive is missing required failed-rollback archival member: $member" >&2; exit 1; }
done
for member in schemas/release-update-reactivation-archive-v1.schema.json scripts/archive-reactivation-failure.sh; do
  tar -tzf "$archive" | grep -Fx "pixel-$version/$member" >/dev/null || { echo "Release archive is missing required failed-reactivation archival member: $member" >&2; exit 1; }
done
for member in schemas/release-update-reactivation-v1.schema.json schemas/release-update-reactivation-result-v1.schema.json schemas/release-update-reactivation-rollback-v1.schema.json schemas/release-update-reactivation-rollback-result-v1.schema.json schemas/release-update-reactivation-recovery-v1.schema.json scripts/reactivate-release-update.sh scripts/rollback-reactivated-release-update.sh scripts/recover-reactivated-release-update.sh; do
  tar -tzf "$archive" | grep -Fx "pixel-$version/$member" >/dev/null || { echo "Release archive is missing required reactivation member: $member" >&2; exit 1; }
done
for member in scripts/migrate-legacy-clean.py scripts/restore-private-state.sh scripts/restore-receipt.py scripts/verify.sh schemas/legacy-clean-migration-v1.schema.json tests/test_legacy_clean_migration.py tests/test_restore_receipt.py tests/legacy-clean-migration-schema.test.mjs; do
  tar -tzf "$archive" | grep -Fx "pixel-$version/$member" >/dev/null || { echo "Release archive is missing required clean-migration member: $member" >&2; exit 1; }
done
python3 - <<'PY' || { echo "Restore-receipt contract is missing from the migration schema" >&2; exit 1; }
import json, pathlib
schema = json.load(open("schemas/legacy-clean-migration-v1.schema.json", encoding="utf-8"))
if "restoreReceipt" not in schema["$defs"]:
    raise SystemExit(1)
if schema["$defs"]["restoreReceipt"]["properties"]["kind"]["const"] != "pixel-restore-receipt":
    raise SystemExit(1)
if schema["$defs"]["completion"]["required"] != [
    "schemaVersion", "operation", "sourcePixel", "targetPixel", "activeRelease",
    "planSha256", "rehearsalSha256", "backupSha256", "restoreReceiptSha256",
    "runtimeAttestationSha256", "sourceCommit", "sourceTree", "verified",
    "completionSha256", "generatedAt", "privacy", "boundary",
]:
    raise SystemExit(1)
helper = pathlib.Path("scripts/restore-receipt.py").read_text(encoding="utf-8")
for needle in (
    "pixel-restore-receipt-reservation",
    "restore receipt reservation was replaced or altered",
    "reserve",
    "finalize",
    "abort",
):
    if needle not in helper:
        raise SystemExit(1)
restore = pathlib.Path("scripts/restore-private-state.sh").read_text(encoding="utf-8")
if "restore succeeded but receipt finalization failed; do not run migration finalize" not in restore:
    raise SystemExit(1)
if 'restore-receipt.py" reserve' not in restore or 'restore-receipt.py" finalize' not in restore:
    raise SystemExit(1)
PY
for member in control/work-authoring.example.json control/work-launch.example.json control/work-service.example.json schemas/control-chat-v1.schema.json schemas/control-chat-turn-request-v1.schema.json schemas/control-chat-handoff-receipt-v1.schema.json schemas/control-work-authoring-config-v1.schema.json schemas/control-work-authoring-v1.schema.json schemas/control-work-draft-request-v1.schema.json schemas/control-work-draft-reviews-v1.schema.json schemas/control-work-launch-config-v1.schema.json schemas/control-work-service-config-v1.schema.json schemas/control-work-semantic-review-v1.schema.json deploy/work-controller/goal-review-cli.mjs tests/control-deep-work-draft-e2e.test.mjs; do
  tar -tzf "$archive" | grep -Fx "pixel-$version/$member" >/dev/null || { echo "Release archive is missing required guided-authoring member: $member" >&2; exit 1; }
done
for member in scripts/deep-work-service-qualification.mjs tests/deep-work-service-qualification.test.mjs deploy/supported-host/tmp-hygiene-units.mjs tests/supported-host-tmp-hygiene.test.mjs; do
  tar -tzf "$archive" | grep -Fx "pixel-$version/$member" >/dev/null || { echo "Release archive is missing required supported-host supervisor member: $member" >&2; exit 1; }
done
for member in PRODUCTIZATION.md GITHUB-ACTIONS.md RELEASE-IDENTITY.json schemas/release-identity-v1.schema.json scripts/release-identity.mjs tests/release-identity.test.mjs schemas/runtime-attestation-v1.schema.json scripts/runtime-attestation.mjs tests/runtime-attestation.test.mjs schemas/external-action-journal-event-v1.schema.json deploy/action_journal/__init__.py deploy/action_journal/cli.py tests/test_action_journal.py security-evals/action-journal-pressure/fuzz.py deploy/github-broker/broker.py deploy/github-broker/policy.example.json deploy/github-broker/proposal.example.json schemas/github-action-policy-v1.schema.json schemas/github-action-proposal-v1.schema.json schemas/github-action-result-v1.schema.json tests/test_github_broker.py schemas/portal-user-journey-corpus-v1.schema.json schemas/portal-user-trial-journey-v1.schema.json security-evals/portal-user-trial/trial-journeys-v1.json tests/portal-user-trial-journeys.test.mjs schemas/agent-comparison-task-battery-v1.schema.json security-evals/agent-comparison/task-battery-v1.json tests/agent-comparison-battery.test.mjs deploy/agent-comparison/serial-pair.py scripts/scan-history-secrets.py tests/test_scan_history_secrets.py schemas/historical-secret-review-v1.schema.json schemas/historical-secret-review-v2.schema.json security-evals/historical-secret-closure/reviewed-blobs.json security-evals/assurance/reviewed_policy.py schemas/portal-outcome-task-v1.schema.json schemas/portal-outcome-task-admission-v1.schema.json schemas/portal-outcome-model-contract-v1.schema.json schemas/portal-outcome-inference-contract-v1.schema.json schemas/portal-outcome-tool-policy-v1.schema.json schemas/portal-outcome-run-v1.schema.json schemas/portal-outcome-comparison-v1.schema.json schemas/portal-outcome-campaign-plan-v1.schema.json schemas/portal-outcome-campaign-v1.schema.json security-evals/portal-user-journeys/corpus-v1.json tests/portal-user-journey-corpus.test.mjs scripts/portal_outcome_task.py scripts/portal_outcome_materialize_source.mjs scripts/portal_outcome_runner.py scripts/portal_outcome_verifier.py scripts/portal_outcome_orchestrate.py scripts/portal_outcome_livesystem.py scripts/portal_outcome_evaluation.py scripts/portal_outcome_campaign.py tests/test_portal_outcome_task.py tests/test_portal_outcome_runner.py tests/test_portal_outcome_verifier.py tests/test_portal_outcome_orchestrate.py tests/test_portal_outcome_livesystem.py tests/test_portal_outcome_evaluation.py tests/test_portal_outcome_campaign.py scripts/reconcile-source-action.sh; do
  tar -tzf "$archive" | grep -Fx "pixel-$version/$member" >/dev/null || { echo "Release archive is missing required productization member: $member" >&2; exit 1; }
done
for member in deploy/agent-comparison/Dockerfile.dsv4-vllm deploy/agent-comparison/Dockerfile.codex-runner deploy/agent-comparison/README.md deploy/agent-comparison/inference-boundary.mjs deploy/agent-comparison/research-mcp-server.mjs deploy/agent-comparison/codex-research-authority.mjs deploy/agent-comparison/research-fixture-adapter.mjs deploy/agent-comparison/codex-0.147.0-prompt.md deploy/agent-comparison/pair-system.example.json deploy/agent-comparison/pixel-arm.mjs deploy/agent-comparison/pixel-system-cli.mjs deploy/agent-comparison/pixel-system.example.json scripts/agent_comparison_fixture_tool.py scripts/codex_comparison_workspace.py scripts/portal_outcome_materialize_battery.py scripts/portal_outcome_trial_coverage.py scripts/portal_outcome_pixel_orchestrate.py scripts/portal_outcome_pixel_livesystem.py scripts/portal_outcome_pair.py scripts/portal_outcome_pair_system.py scripts/portal_outcome_pair_preflight.py scripts/portal_outcome_battery_campaign.py scripts/qualify_codex_comparison_surface.py scripts/qualify-dsv4-image.sh scripts/qualify-work-runner-image.sh schemas/portal-outcome-research-fixture-v1.schema.json schemas/portal-outcome-trial-coverage-v1.schema.json schemas/portal-outcome-pair-system-v1.schema.json schemas/portal-outcome-pair-system-binding-v1.schema.json schemas/portal-outcome-pair-preflight-v1.schema.json schemas/portal-outcome-pixel-system-v1.schema.json schemas/work-model-qualification-docker-v1.schema.json schemas/work-model-qualification-maintenance-v1.schema.json schemas/work-model-campaign-maintenance-v1.schema.json deploy/work-controller/model-qualification-docker.example.json deploy/work-controller/model-qualification-docker.mjs deploy/work-controller/model-qualification-container.mjs deploy/work-controller/model-qualification-bridge.mjs deploy/work-controller/maintenance-custody.mjs deploy/work-controller/maintenance-recovery-journal.mjs deploy/work-controller/maintenance-recovery-guardian.mjs deploy/work-controller/maintenance-recovery-guardian@.service deploy/work-controller/maintenance-recovery-guardian-supervise.sh deploy/work-controller/maintenance-recovery-guardian-lease.mjs deploy/work-controller/model-qualification-maintenance.example.json deploy/work-controller/model-qualification-maintenance.mjs deploy/work-controller/model-campaign-maintenance.example.json deploy/work-controller/model-campaign-maintenance.mjs deploy/work-model-proxy/inference-policy.mjs tests/agent-comparison-dsv4-runtime.test.mjs tests/agent-comparison-inference-boundary.test.mjs tests/agent-comparison-research-mcp.test.mjs tests/agent-comparison-codex-research-authority.test.mjs tests/agent-comparison-pixel-arm.test.mjs tests/agent-comparison-pixel-system.test.mjs tests/test_agent_comparison_fixture_tool.py tests/test_codex_comparison_workspace.py tests/test_portal_outcome_pair.py tests/test_portal_outcome_pair_system.py tests/test_portal_outcome_pair_preflight.py tests/test_portal_outcome_battery_campaign.py tests/test_portal_outcome_trial_coverage.py tests/work-runner-image-repro.test.mjs tests/work-model-qualification-container.test.mjs tests/work-model-qualification-docker.test.mjs tests/maintenance-custody.test.mjs tests/maintenance-recovery-journal.test.mjs tests/maintenance-recovery-guardian.test.mjs tests/maintenance-recovery-guardian-systemd.test.mjs tests/work-model-qualification-maintenance.test.mjs tests/work-model-campaign-maintenance.test.mjs; do
  tar -tzf "$archive" | grep -Fx "pixel-$version/$member" >/dev/null || { echo "Release archive is missing required DSV4 comparison member: $member" >&2; exit 1; }
done
for member in schemas/portal-outcome-tuning-baseline-freeze-v1.schema.json schemas/portal-outcome-assistant-template-v1.schema.json deploy/agent-comparison/assistant-template.example.json scripts/portal_outcome_product_path.py scripts/portal_outcome_research_evidence.py scripts/portal_outcome_assistant_evidence.py scripts/portal_outcome_assistant_runtime.py scripts/portal_outcome_assistant_system.py deploy/agent-comparison/assistant-model-proxy.mjs tests/test_agent_comparison_rehearsals.py tests/test_portal_outcome_product_path.py tests/test_portal_outcome_assistant_evidence.py tests/test_portal_outcome_assistant_runtime.py tests/test_portal_outcome_assistant_system.py; do
  tar -tzf "$archive" | grep -Fx "pixel-$version/$member" >/dev/null || { echo "Release archive is missing required DSV4 comparison route or rehearsal member: $member" >&2; exit 1; }
done
for member in scripts/portal_outcome_runtime_control.py tests/test_portal_outcome_runtime_control.py; do
  tar -tzf "$archive" | grep -Fx "pixel-$version/$member" >/dev/null || { echo "Release archive is missing required cold/warm runtime-control member: $member" >&2; exit 1; }
done
for member in deploy/work-provider/adapters/local-openai.mjs deploy/work-provider/adapters/openai-chat.mjs deploy/work-provider/executor.mjs deploy/work-provider/grading.mjs deploy/work-provider/harness.mjs deploy/work-provider/local-policy.mjs deploy/work-provider/local-transport.mjs deploy/work-provider/patch-verifier.mjs deploy/work-provider/transport-registry.mjs deploy/work-provider/moonshot-transport.mjs deploy/work-provider/run-ledger.mjs deploy/work-provider/run-store.mjs deploy/work-provider-router/qualification.mjs deploy/work-provider-router/router.mjs schemas/work-provider-run-ledger-v1.schema.json schemas/work-provider-router-decision-v1.schema.json schemas/work-provider-router-qualification-v1.schema.json tests/work-provider-adapters.test.mjs tests/work-provider-executor.test.mjs tests/work-provider-harness.test.mjs tests/work-provider-local-lane.test.mjs tests/work-provider-multiturn.test.mjs tests/work-provider-adversarial.test.mjs tests/work-provider-patch-verifier.test.mjs; do
  tar -tzf "$archive" | grep -Fx "pixel-$version/$member" >/dev/null || { echo "Release archive is missing required multi-provider execution member: $member" >&2; exit 1; }
done
for member in schemas/work-research-revision-review-v1.schema.json schemas/work-research-revision-history-v1.schema.json deploy/work-controller/research-revision-review.mjs tests/work-research-revision-review.test.mjs; do
  tar -tzf "$archive" | grep -Fx "pixel-$version/$member" >/dev/null || { echo "Release archive is missing required Researcher revision-review member: $member" >&2; exit 1; }
done
for member in schemas/work-goal-controller-v1.schema.json schemas/work-goal-declaration-v1.schema.json schemas/work-goal-fleet-v1.schema.json schemas/work-goal-fleet-checkpoint-v1.schema.json schemas/work-semantic-acceptance-v1.schema.json schemas/work-scout-report-proposal-v1.schema.json schemas/work-scout-report-v1.schema.json schemas/work-scout-verification-v1.schema.json schemas/deep-work-endurance-evidence-v1.schema.json schemas/deep-work-multi-day-soak-v1.schema.json schemas/deep-work-multi-day-soak-invocation-v1.schema.json schemas/deep-work-multi-day-soak-evidence-v1.schema.json deploy/work-controller/goal-declaration.example.json deploy/work-controller/goal-prepare-cli.mjs deploy/work-controller/goal-fleet.mjs deploy/work-controller/goal-cycle-cli.mjs deploy/work-controller/goal-continuous-cli.mjs deploy/work-controller/goal-cleanup-cli.mjs deploy/work-controller/goal-cancel-cli.mjs deploy/work-controller/goal-pause-cli.mjs deploy/work-controller/goal-resume-cli.mjs deploy/work-controller/goal-service-cli.mjs deploy/work-controller/goal-service-units.mjs deploy/work-controller/goal-service-lifecycle.mjs deploy/work-controller/goal-service-lifecycle-cli.mjs deploy/work-controller/deep-work-soak-service-units.mjs deploy/work-controller/deep-work-soak-service-lifecycle.mjs deploy/work-controller/deep-work-soak-service-cli.mjs deploy/work-controller/goal-operator-status.mjs deploy/work-controller/operator-status.mjs deploy/work-controller/candidate-wait-loop.mjs deploy/work-controller/goal-candidate-adapter.mjs deploy/work-controller/goal-profile-router.mjs deploy/work-controller/semantic-acceptance.mjs deploy/work-controller/goal-accept-cli.mjs deploy/work-runner/scout-report.mjs scripts/deep-work-endurance-probe.mjs scripts/deep-work-multi-day-soak.mjs tests/work-goal-prepare.test.mjs tests/work-goal-fleet.test.mjs tests/work-candidate-wait-loop.test.mjs tests/work-goal-operator-status.test.mjs tests/work-goal-pause-cli.test.mjs tests/control-deep-work-pause-e2e.test.mjs tests/work-goal-resume-cli.test.mjs tests/work-goal-cancel-cli.test.mjs tests/work-goal-terminal-journey-e2e.test.mjs tests/work-goal-real-crash-e2e.test.mjs tests/work-goal-continuous.test.mjs tests/work-goal-multi-day-soak.test.mjs tests/work-goal-multi-day-soak-service-units.test.mjs tests/work-goal-multi-day-soak-service-lifecycle.test.mjs tests/work-goal-service-units.test.mjs tests/work-goal-service-lifecycle.test.mjs; do
  tar -tzf "$archive" | grep -Fx "pixel-$version/$member" >/dev/null || { echo "Release archive is missing required supervised-controller member: $member" >&2; exit 1; }
done
for member in schemas/work-goal-fleet-controller-v2.schema.json deploy/work-controller/goal-fleet-cycle-cli.mjs deploy/work-controller/goal-fleet-cleanup-cli.mjs deploy/work-controller/goal-fleet-supervised-cycle-cli.mjs deploy/work-controller/goal-fleet-service-units.mjs deploy/work-controller/goal-fleet-service-cli.mjs deploy/work-controller/goal-fleet-service-lifecycle.mjs deploy/work-controller/goal-fleet-service-lifecycle-cli.mjs tests/work-goal-fleet-service-units.test.mjs tests/work-goal-fleet-service-lifecycle.test.mjs; do
  tar -tzf "$archive" | grep -Fx "pixel-$version/$member" >/dev/null || { echo "Release archive is missing required fleet-controller member: $member" >&2; exit 1; }
done
for member in schemas/work-goal-fleet-host-evidence-v2.schema.json schemas/work-goal-fleet-host-probe-v1.schema.json deploy/work-controller/goal-fleet-host-evidence-cli.mjs deploy/work-controller/goal-fleet-host-evidence-ledger.mjs tests/work-goal-fleet-host-evidence.test.mjs; do
  tar -tzf "$archive" | grep -Fx "pixel-$version/$member" >/dev/null || { echo "Release archive is missing required fleet-host-evidence member: $member" >&2; exit 1; }
done
for member in schemas/work-input-selection-v1.schema.json schemas/work-input-pack-v1.schema.json schemas/work-goal-brief-v1.schema.json schemas/work-input-catalog-v1.schema.json schemas/work-goal-draft-v1.schema.json schemas/work-goal-assembly-v1.schema.json schemas/work-goal-launch-preparation-v1.schema.json deploy/work-controller/input-selection.example.json deploy/work-controller/input-selection-data.example.json deploy/work-controller/input-pack-cli.mjs deploy/work-controller/goal-brief.example.json deploy/work-controller/goal-brief-data.example.json deploy/work-controller/input-catalog.example.json deploy/work-controller/goal-draft-cli.mjs deploy/work-controller/goal-assemble-cli.mjs deploy/work-controller/goal-launch-prepare-cli.mjs tests/work-input-pack.test.mjs tests/work-goal-draft.test.mjs tests/work-goal-assemble.test.mjs tests/work-goal-launch-prepare.test.mjs; do
  tar -tzf "$archive" | grep -Fx "pixel-$version/$member" >/dev/null || { echo "Release archive is missing required guided-goal member: $member" >&2; exit 1; }
done
for member in schemas/work-context-capsule-v1.schema.json schemas/work-context-session-input-v1.schema.json deploy/work-controller/context-capsules.mjs deploy/work-controller/context-session-cli.mjs deploy/work-controller/context-session-guide.mjs deploy/work-controller/context-session-input.example.json tests/work-context-capsules.test.mjs tests/work-context-session-cli.test.mjs; do
  tar -tzf "$archive" | grep -Fx "pixel-$version/$member" >/dev/null || { echo "Release archive is missing required private-context member: $member" >&2; exit 1; }
done
member=schemas/work-goal-bundle-v1.schema.json
tar -tzf "$archive" | grep -Fx "pixel-$version/$member" >/dev/null || { echo "Release archive is missing required goal-preparation member: $member" >&2; exit 1; }
for member in schemas/work-goal-controller-environment-v1.schema.json schemas/work-goal-controller-bundle-v1.schema.json deploy/work-controller/goal-controller-environment.example.json deploy/work-controller/goal-controller-capability-environment.example.json deploy/work-controller/goal-controller-knowledge-environment.example.json deploy/work-controller/goal-controller-prepare-cli.mjs deploy/work-controller/goal-knowledge-runtime.mjs deploy/work-controller/knowledge-vault-guide.mjs deploy/work-controller/knowledge-vault-restore-cli.mjs tests/fixtures/work/knowledge-backup-probe.mjs tests/work-goal-controller-prepare.test.mjs tests/work-goal-knowledge-runtime.test.mjs tests/work-knowledge-vault-guide.test.mjs tests/work-knowledge-vault-restore.test.mjs; do
  tar -tzf "$archive" | grep -Fx "pixel-$version/$member" >/dev/null || { echo "Release archive is missing required controller-preparation member: $member" >&2; exit 1; }
done
for member in schemas/work-goal-stage-v1.schema.json deploy/work-controller/goal-stage-cli.mjs tests/work-goal-stage.test.mjs; do
  tar -tzf "$archive" | grep -Fx "pixel-$version/$member" >/dev/null || { echo "Release archive is missing required goal-staging member: $member" >&2; exit 1; }
done
for member in schemas/work-model-backend-status-v1.schema.json schemas/work-model-artifact-manifest-v1.schema.json schemas/work-model-runtime-cache-manifest-v1.schema.json schemas/work-model-artifact-review-v1.schema.json schemas/work-model-artifact-render-receipt-v1.schema.json schemas/work-model-backend-config-v1.schema.json schemas/work-model-backend-review-v1.schema.json schemas/work-model-backend-launch-v1.schema.json schemas/work-model-backend-lifecycle-review-v1.schema.json schemas/work-model-backend-lifecycle-receipt-v1.schema.json schemas/work-model-backend-halt-review-v1.schema.json schemas/work-model-backend-halt-receipt-v1.schema.json schemas/work-model-backend-live-qualification-v1.schema.json deploy/work-controller/model-backend-cli.mjs deploy/work-controller/model-artifact.mjs deploy/work-controller/model-runtime-cache-artifact.mjs deploy/work-controller/model-backend-launch.mjs deploy/work-controller/model-backend-runtime.mjs deploy/work-controller/model-backend-lifecycle.mjs deploy/work-controller/model-backend-halt.mjs deploy/work-runner/model-backend-coordination.mjs deploy/work-controller/model-backend-llama.example.json deploy/work-controller/model-backend-vllm.example.json tests/work-model-backend-cli.test.mjs tests/work-model-artifact.test.mjs tests/work-model-backend-launch.test.mjs tests/work-model-backend-lifecycle.test.mjs tests/work-model-backend-coordination.test.mjs tests/work-model-backend-lifecycle-live.mjs tests/fixtures/work/model-backend.mjs; do
  tar -tzf "$archive" | grep -Fx "pixel-$version/$member" >/dev/null || { echo "Release archive is missing required contained-model member: $member" >&2; exit 1; }
done
first=$(sha256sum "$archive" "$sbom" "$provenance" "$update")
bash pixel package
second=$(sha256sum "$archive" "$sbom" "$provenance" "$update")
[[ "$first" == "$second" ]] || { echo "Release archive is not reproducible" >&2; exit 1; }
(
  smoke_stage=$(mktemp -d)
  trap 'rm -rf -- "$smoke_stage"' EXIT
  tar -xzf "$archive" -C "$smoke_stage"
  cd "$smoke_stage/pixel-$version"
  ./pixel migrate-legacy-clean --help >/dev/null 2>&1 || { echo "Packaged clean-migration CLI failed its dependency-free help check" >&2; exit 1; }
  python3 scripts/migrate-legacy-clean.py --help >/dev/null 2>&1 || { echo "Packaged clean-migration script failed its dependency-free parse check" >&2; exit 1; }
  python3 -c "import importlib.util; spec=importlib.util.spec_from_file_location('m','scripts/migrate-legacy-clean.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)" || { echo "Packaged clean-migration module import failed without jsonschema" >&2; exit 1; }
  python3 -c "import importlib.util; spec=importlib.util.spec_from_file_location('r','scripts/restore-receipt.py'); r=importlib.util.module_from_spec(spec); spec.loader.exec_module(r)" || { echo "Packaged restore-receipt helper import failed without jsonschema" >&2; exit 1; }
)

(cd dist && sha256sum -c "$(basename "$archive").sha256")
(cd dist && sha256sum -c "$(basename "$sbom").sha256")
(cd dist && sha256sum -c "$(basename "$provenance").sha256")
(cd dist && sha256sum -c "$(basename "$update").sha256")
embedded=$(tar -xOf "$archive" "pixel-$version/SBOM.cdx.json" | sha256sum | awk '{print $1}')
standalone=$(sha256sum "$sbom" | awk '{print $1}')
[[ "$embedded" == "$standalone" ]] || { echo "Embedded and standalone SBOM differ" >&2; exit 1; }

node - "$version" "$sbom" "$provenance" <<'NODE'
const fs = require("node:fs");
const [version, sbomPath, provenancePath] = process.argv.slice(2);
const sbom = JSON.parse(fs.readFileSync(sbomPath));
const provenance = JSON.parse(fs.readFileSync(provenancePath));
if (sbom.bomFormat !== "CycloneDX" || sbom.specVersion !== "1.6") process.exit(1);
if (sbom.metadata?.component?.version !== version) process.exit(1);
if (provenance._type !== "https://in-toto.io/Statement/v1") process.exit(1);
if (provenance.predicateType !== "https://slsa.dev/provenance/v1") process.exit(1);
if (provenance.predicate?.buildDefinition?.externalParameters?.version !== version) process.exit(1);
NODE

trust_root=$(mktemp -d)
trap 'rm -rf -- "$trust_root"' EXIT
ssh-keygen -q -t ed25519 -N '' -f "$trust_root/release-key"
printf 'pixel-release %s\n' "$(cat "$trust_root/release-key.pub")" > "$trust_root/allowed-signers"
chmod 600 "$trust_root/release-key"
chmod 644 "$trust_root/allowed-signers"
release_status=$(node -e 'const m=require("./OPENCLAW-COMPATIBILITY.json"); const v=require("fs").readFileSync("VERSION","utf8").trim(); process.stdout.write(m.combinations.find((item)=>item.pixel===v)?.status??"")')
if [[ "$release_status" == supported ]]; then
  bash pixel release-sign --envelope "$(realpath "$update")" --signing-key "$(realpath "$trust_root/release-key")" --confirm >/dev/null
  inspection=$(bash pixel update-inspect --envelope "$(realpath "$update")" --allowed-signers "$(realpath "$trust_root/allowed-signers")" --identity pixel-release)
  node -e 'const r=JSON.parse(process.argv[1]); if(r.status!=="verified" || r.candidateCodeExecuted!==false || r.activationAuthority!=="external-exact-confirmation-only") process.exit(1)' "$inspection"
elif [[ "$release_status" == candidate ]]; then
  if signing_error=$(bash pixel release-sign --envelope "$(realpath "$update")" --signing-key "$(realpath "$trust_root/release-key")" --confirm 2>&1); then
    echo "Candidate release was signed" >&2
    exit 1
  fi
  grep -F "no matching supported compatibility record" <<<"$signing_error" >/dev/null || { echo "Candidate release failed closed for an unexpected reason" >&2; exit 1; }
  [[ ! -e "$update.sig" ]] || { echo "Candidate release left a signature" >&2; exit 1; }
  qualification_receipt=$(bash pixel release-qualification-sign --envelope "$(realpath "$update")" --signing-key "$(realpath "$trust_root/release-key")" --confirm)
  node -e 'const r=JSON.parse(process.argv[1]); if(r.status!=="qualification-signed" || r.publicationAuthority!==false || r.activationAuthority!==false) process.exit(1)' "$qualification_receipt"
  qualification_inspection=$(bash pixel release-qualification-inspect --envelope "$(realpath "$update")" --allowed-signers "$(realpath "$trust_root/allowed-signers")" --identity pixel-release)
  node -e 'const r=JSON.parse(process.argv[1]); if(r.status!=="qualification-verified" || r.publicationAuthority!==false || r.stagingAuthority!==false || r.activationAuthority!==false || r.candidateCodeExtracted!==false || r.candidateCodeExecuted!==false) process.exit(1)' "$qualification_inspection"
  [[ -s "$update.qualification.sig" ]] || { echo "Candidate qualification signature is absent" >&2; exit 1; }
else
  echo "Release compatibility status is not signable" >&2
  exit 1
fi

[[ -z $(git status --porcelain=v1 --untracked-files=no) ]] || { echo "Release gate changed tracked source" >&2; exit 1; }
echo "Supported-host release gate passed."
