---
title: Pixel documentation inventory
doc_type: reference
audience: [owner, operator, contributor, security-reviewer, maintainer]
feature_status: mixed
owners: [documentation]
sources_of_truth: [scripts/docs/inventory-baseline.json]
generated_by: scripts/docs/generate-document-inventory.mjs
last_verified_at: generated
---

<!-- GENERATED FILE. Run `node scripts/docs/generate.mjs --write`; do not edit by hand. -->

# Pixel documentation inventory

This inventory makes **178 existing Markdown files** discoverable without moving path-sensitive release evidence, root contracts, component guides, or installed template content. Classification is a reading boundary, not a claim that every older document is current. Start with the [job-oriented documentation hub](../README.md) and [generated status](../status.md); use this catalog when tracing specialist or historical source material.

## Colocated specialist document

| Document | Reading boundary |
|---|---|
| [`.github/pull_request_template.md`](../../.github/pull_request_template.md) | Owning subsystem scope; verify its feature/evidence status before use. |
| [`deploy/agent-comparison/codex-0.147.0-prompt.md`](../../deploy/agent-comparison/codex-0.147.0-prompt.md) | Owning subsystem scope; verify its feature/evidence status before use. |
| [`deploy/work-runner/builder-agent.md`](../../deploy/work-runner/builder-agent.md) | Owning subsystem scope; verify its feature/evidence status before use. |

## Path-stable root contract or entry point

| Document | Reading boundary |
|---|---|
| [`ACCEPTANCE-CHECKLIST.md`](../../ACCEPTANCE-CHECKLIST.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`ARCHITECTURE.md`](../../ARCHITECTURE.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`CHANGELOG.md`](../../CHANGELOG.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`CLIENT-KIT.md`](../../CLIENT-KIT.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`CLIENT-ONBOARDING.md`](../../CLIENT-ONBOARDING.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`CODEX-WORK-PROVIDER.md`](../../CODEX-WORK-PROVIDER.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`CONTRIBUTING.md`](../../CONTRIBUTING.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`CONTROL-SURFACE.md`](../../CONTROL-SURFACE.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`CUSTOMIZATION.md`](../../CUSTOMIZATION.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`DEEP-WORK.md`](../../DEEP-WORK.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`DEPLOYMENT.md`](../../DEPLOYMENT.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`FRONTIER-LIMB.md`](../../FRONTIER-LIMB.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`FRONTIER-LIVE-QUALIFICATION.md`](../../FRONTIER-LIVE-QUALIFICATION.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`GITHUB-ACTIONS.md`](../../GITHUB-ACTIONS.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`INCIDENT-RESPONSE.md`](../../INCIDENT-RESPONSE.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`KNOWLEDGE-VAULT.md`](../../KNOWLEDGE-VAULT.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`LICENSE.md`](../../LICENSE.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`OPENCLAW-COMPATIBILITY.md`](../../OPENCLAW-COMPATIBILITY.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`OPERATIONS-AUTONOMY.md`](../../OPERATIONS-AUTONOMY.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`OPERATIONS-LIMB.md`](../../OPERATIONS-LIMB.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`OPERATIONS.md`](../../OPERATIONS.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`QUALIFICATION.md`](../../QUALIFICATION.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`README.md`](../../README.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`SEALED-CORPUS-CONTRACT.md`](../../SEALED-CORPUS-CONTRACT.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`SECURITY-ASSURANCE.md`](../../SECURITY-ASSURANCE.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`SECURITY.md`](../../SECURITY.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`SUPPORT.md`](../../SUPPORT.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`THREAT-MODEL.md`](../../THREAT-MODEL.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`UPGRADE.md`](../../UPGRADE.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`UPSTREAM-FAILURE-GUIDE.md`](../../UPSTREAM-FAILURE-GUIDE.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`UPSTREAM-INTAKE.md`](../../UPSTREAM-INTAKE.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |
| [`UPSTREAM-RELEASE-CHECKLIST.md`](../../UPSTREAM-RELEASE-CHECKLIST.md) | Canonical or specialist source; pair with generated status and job-oriented docs. |

## Colocated component guide

| Document | Reading boundary |
|---|---|
| [`deploy/agent-comparison/README.md`](../../deploy/agent-comparison/README.md) | Component-maintainer scope; not a whole-product support claim. |
| [`deploy/mesh/README.md`](../../deploy/mesh/README.md) | Component-maintainer scope; not a whole-product support claim. |
| [`deploy/release-operator/README.md`](../../deploy/release-operator/README.md) | Component-maintainer scope; not a whole-product support claim. |
| [`deploy/work-provider-router/README.md`](../../deploy/work-provider-router/README.md) | Component-maintainer scope; not a whole-product support claim. |
| [`deploy/work-provider/README.md`](../../deploy/work-provider/README.md) | Component-maintainer scope; not a whole-product support claim. |
| [`security-evals/assurance/README.md`](../../security-evals/assurance/README.md) | Component-maintainer scope; not a whole-product support claim. |
| [`security-evals/control-boundary/README.md`](../../security-evals/control-boundary/README.md) | Component-maintainer scope; not a whole-product support claim. |
| [`security-evals/deployment-isolation/README.md`](../../security-evals/deployment-isolation/README.md) | Component-maintainer scope; not a whole-product support claim. |
| [`security-evals/email-prompt-injection/README.md`](../../security-evals/email-prompt-injection/README.md) | Component-maintainer scope; not a whole-product support claim. |
| [`security-evals/frontier-isolation/README.md`](../../security-evals/frontier-isolation/README.md) | Component-maintainer scope; not a whole-product support claim. |
| [`security-evals/frontier-pressure/README.md`](../../security-evals/frontier-pressure/README.md) | Component-maintainer scope; not a whole-product support claim. |
| [`security-evals/limb-kit-isolation/README.md`](../../security-evals/limb-kit-isolation/README.md) | Component-maintainer scope; not a whole-product support claim. |
| [`security-evals/limb-kit-pressure/README.md`](../../security-evals/limb-kit-pressure/README.md) | Component-maintainer scope; not a whole-product support claim. |
| [`security-evals/modular-e2e/README.md`](../../security-evals/modular-e2e/README.md) | Component-maintainer scope; not a whole-product support claim. |
| [`security-evals/operations-live/README.md`](../../security-evals/operations-live/README.md) | Component-maintainer scope; not a whole-product support claim. |
| [`security-evals/operations-pressure/README.md`](../../security-evals/operations-pressure/README.md) | Component-maintainer scope; not a whole-product support claim. |
| [`security-evals/runner-boundary/README.md`](../../security-evals/runner-boundary/README.md) | Component-maintainer scope; not a whole-product support claim. |
| [`security-evals/session-isolation/README.md`](../../security-evals/session-isolation/README.md) | Component-maintainer scope; not a whole-product support claim. |
| [`security-evals/source-pressure/README.md`](../../security-evals/source-pressure/README.md) | Component-maintainer scope; not a whole-product support claim. |

## Maintained product documentation

| Document | Reading boundary |
|---|---|
| [`docs/concepts/architecture-overview.md`](../../docs/concepts/architecture-overview.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/concepts/capability-status-model.md`](../../docs/concepts/capability-status-model.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/concepts/lifecycle-and-state.md`](../../docs/concepts/lifecycle-and-state.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/concepts/status-and-evidence.md`](../../docs/concepts/status-and-evidence.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/concepts/trust-authority-and-evidence.md`](../../docs/concepts/trust-authority-and-evidence.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/concepts/what-is-pixel.md`](../../docs/concepts/what-is-pixel.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/configure/capability-profiles.md`](../../docs/configure/capability-profiles.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/configure/client-overlays.md`](../../docs/configure/client-overlays.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/configure/deployment-profiles.md`](../../docs/configure/deployment-profiles.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/configure/models-and-providers.md`](../../docs/configure/models-and-providers.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/configure/operations-and-frontier.md`](../../docs/configure/operations-and-frontier.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/configure/README.md`](../../docs/configure/README.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/configure/source-limbs.md`](../../docs/configure/source-limbs.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/contributing/codebase-map.md`](../../docs/contributing/codebase-map.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/contributing/development-setup.md`](../../docs/contributing/development-setup.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/contributing/documentation-contract.md`](../../docs/contributing/documentation-contract.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/contributing/generated-files.md`](../../docs/contributing/generated-files.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/contributing/high-risk-change-map.md`](../../docs/contributing/high-risk-change-map.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/contributing/pull-request-guide.md`](../../docs/contributing/pull-request-guide.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/contributing/testing.md`](../../docs/contributing/testing.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/extend/choose-an-extension-point.md`](../../docs/extend/choose-an-extension-point.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/extend/examples/README.md`](../../docs/extend/examples/README.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/extend/frontier-task-packs.md`](../../docs/extend/frontier-task-packs.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/extend/gateway-extensions.md`](../../docs/extend/gateway-extensions.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/extend/local-capability-packs.md`](../../docs/extend/local-capability-packs.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/extend/operations-action-packs.md`](../../docs/extend/operations-action-packs.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/extend/projection-limbs.md`](../../docs/extend/projection-limbs.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/getting-started/client-onboarding.md`](../../docs/getting-started/client-onboarding.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/getting-started/first-deployment.md`](../../docs/getting-started/first-deployment.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/getting-started/post-install-checklist.md`](../../docs/getting-started/post-install-checklist.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/getting-started/quickstart.md`](../../docs/getting-started/quickstart.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/getting-started/requirements.md`](../../docs/getting-started/requirements.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/NATIVE-SEARCH.md`](../../docs/NATIVE-SEARCH.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/operations/backup-and-restore.md`](../../docs/operations/backup-and-restore.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/operations/command-cheat-sheet.md`](../../docs/operations/command-cheat-sheet.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/operations/decommission.md`](../../docs/operations/decommission.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/operations/faq.md`](../../docs/operations/faq.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/operations/gateway-token-rotation.md`](../../docs/operations/gateway-token-rotation.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/operations/incident-response.md`](../../docs/operations/incident-response.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/operations/monitoring-and-logs.md`](../../docs/operations/monitoring-and-logs.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/operations/runbook.md`](../../docs/operations/runbook.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/operations/support-bundle.md`](../../docs/operations/support-bundle.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/operations/troubleshooting.md`](../../docs/operations/troubleshooting.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/operations/update-and-rollback.md`](../../docs/operations/update-and-rollback.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/operations/verify-and-diagnose.md`](../../docs/operations/verify-and-diagnose.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/README.md`](../../docs/README.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/reference/cli.md`](../../docs/reference/cli.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/reference/cli/customization.md`](../../docs/reference/cli/customization.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/reference/cli/deep-work-local-work.md`](../../docs/reference/cli/deep-work-local-work.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/reference/cli/frontier.md`](../../docs/reference/cli/frontier.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/reference/cli/install-configure-maintain.md`](../../docs/reference/cli/install-configure-maintain.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/reference/cli/operations.md`](../../docs/reference/cli/operations.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/reference/cli/qualification-release-migration.md`](../../docs/reference/cli/qualification-release-migration.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/reference/cli/README.md`](../../docs/reference/cli/README.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/reference/cli/sources-external-actions.md`](../../docs/reference/cli/sources-external-actions.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/reference/configuration.md`](../../docs/reference/configuration.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/reference/glossary.md`](../../docs/reference/glossary.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/reference/schemas.md`](../../docs/reference/schemas.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/reference/services-paths-and-ports.md`](../../docs/reference/services-paths-and-ports.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/reference/support-matrix.md`](../../docs/reference/support-matrix.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/releases/evidence-index.md`](../../docs/releases/evidence-index.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/releases/failure-guide.md`](../../docs/releases/failure-guide.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/releases/maintainer-runbook.md`](../../docs/releases/maintainer-runbook.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/releases/qualification.md`](../../docs/releases/qualification.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/releases/README.md`](../../docs/releases/README.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/releases/upstream-intake.md`](../../docs/releases/upstream-intake.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/security/assurance.md`](../../docs/security/assurance.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/security/boundary-reference.md`](../../docs/security/boundary-reference.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/security/evaluations.md`](../../docs/security/evaluations.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/security/README.md`](../../docs/security/README.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/security/sealed-corpus.md`](../../docs/security/sealed-corpus.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/security/threat-model.md`](../../docs/security/threat-model.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/status.md`](../../docs/status.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/use/control-surface.md`](../../docs/use/control-surface.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/use/deep-work.md`](../../docs/use/deep-work.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/use/first-conversation.md`](../../docs/use/first-conversation.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/use/frontier-review.md`](../../docs/use/frontier-review.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/use/operations.md`](../../docs/use/operations.md) | Current indexed guide or generated reference; feature status remains page-specific. |
| [`docs/use/sources-and-proposals.md`](../../docs/use/sources-and-proposals.md) | Current indexed guide or generated reference; feature status remains page-specific. |

## Release or historical evidence

| Document | Reading boundary |
|---|---|
| [`DREAM-FORGE-SOURCE-AUDIT.md`](../../DREAM-FORGE-SOURCE-AUDIT.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.0.0.md`](../../LIVE-AUDIT-4.0.0.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.1.0.md`](../../LIVE-AUDIT-4.1.0.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.2.0.md`](../../LIVE-AUDIT-4.2.0.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.0.md`](../../LIVE-AUDIT-4.3.0.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.1.md`](../../LIVE-AUDIT-4.3.1.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.10.md`](../../LIVE-AUDIT-4.3.10.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.11.md`](../../LIVE-AUDIT-4.3.11.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.12.md`](../../LIVE-AUDIT-4.3.12.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.13.md`](../../LIVE-AUDIT-4.3.13.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.14.md`](../../LIVE-AUDIT-4.3.14.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.15.md`](../../LIVE-AUDIT-4.3.15.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.16.md`](../../LIVE-AUDIT-4.3.16.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.17.md`](../../LIVE-AUDIT-4.3.17.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.18.md`](../../LIVE-AUDIT-4.3.18.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.19.md`](../../LIVE-AUDIT-4.3.19.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.2.md`](../../LIVE-AUDIT-4.3.2.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.20.md`](../../LIVE-AUDIT-4.3.20.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.21.md`](../../LIVE-AUDIT-4.3.21.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.22.md`](../../LIVE-AUDIT-4.3.22.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.23.md`](../../LIVE-AUDIT-4.3.23.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.24.md`](../../LIVE-AUDIT-4.3.24.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.25.md`](../../LIVE-AUDIT-4.3.25.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.26.md`](../../LIVE-AUDIT-4.3.26.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.27.md`](../../LIVE-AUDIT-4.3.27.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.3.md`](../../LIVE-AUDIT-4.3.3.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.4.md`](../../LIVE-AUDIT-4.3.4.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.5.md`](../../LIVE-AUDIT-4.3.5.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.6.md`](../../LIVE-AUDIT-4.3.6.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.7.md`](../../LIVE-AUDIT-4.3.7.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.8.md`](../../LIVE-AUDIT-4.3.8.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT-4.3.9.md`](../../LIVE-AUDIT-4.3.9.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |
| [`LIVE-AUDIT.md`](../../LIVE-AUDIT.md) | Point-in-time evidence only; read exact source identity and current compatibility status. |

## Plan or program record

| Document | Reading boundary |
|---|---|
| [`OPENCLAW-UPSTREAM-QUALIFICATION-PLAN.md`](../../OPENCLAW-UPSTREAM-QUALIFICATION-PLAN.md) | Intent/history; not current runtime or release authority. |
| [`PRODUCTIZATION.md`](../../PRODUCTIZATION.md) | Intent/history; not current runtime or release authority. |
| [`ROADMAP.md`](../../ROADMAP.md) | Intent/history; not current runtime or release authority. |

## Installed workspace/template content

| Document | Reading boundary |
|---|---|
| [`workspace-template/AGENTS.md`](../../workspace-template/AGENTS.md) | Runtime/template input; edit only through its owning template contract. |
| [`workspace-template/HEARTBEAT.md`](../../workspace-template/HEARTBEAT.md) | Runtime/template input; edit only through its owning template contract. |
| [`workspace-template/IDENTITY.md`](../../workspace-template/IDENTITY.md) | Runtime/template input; edit only through its owning template contract. |
| [`workspace-template/MEMORY.md`](../../workspace-template/MEMORY.md) | Runtime/template input; edit only through its owning template contract. |
| [`workspace-template/SOUL.md`](../../workspace-template/SOUL.md) | Runtime/template input; edit only through its owning template contract. |
| [`workspace-template/TOOLS.md`](../../workspace-template/TOOLS.md) | Runtime/template input; edit only through its owning template contract. |
| [`workspace-template/USER.md`](../../workspace-template/USER.md) | Runtime/template input; edit only through its owning template contract. |
| [`workspace-template/WEB-NAVIGATION.md`](../../workspace-template/WEB-NAVIGATION.md) | Runtime/template input; edit only through its owning template contract. |
