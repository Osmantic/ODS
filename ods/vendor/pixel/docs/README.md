---
title: Pixel documentation
doc_type: reference
audience: [owner, operator, contributor, security-reviewer, maintainer]
feature_status: mixed
owners: [documentation]
sources_of_truth: [README.md, pixel, RELEASE-MANIFEST.json, QUALIFICATION-MATRIX.json, OPENCLAW-COMPATIBILITY.json]
last_verified_at: 2026-08-27
---

# Pixel documentation

Start with the job you need to do. Status words in these pages are evidence boundaries, not marketing labels: source presence, an authored test, or a generated receipt does not by itself prove support, deployment, or live acceptance.

## Start here

| Your job | Start with | Then use |
|---|---|---|
| Understand what this checkout claims | [Current status](status.md) | [Repository README](../README.md) and [qualification contract](../QUALIFICATION.md) |
| Install or evaluate Pixel | [Requirements](getting-started/requirements.md) and [clean-host quickstart](getting-started/quickstart.md) | [First deployment](getting-started/first-deployment.md) and [post-install checklist](getting-started/post-install-checklist.md) |
| Configure or customize Pixel | [Configuration source map](reference/configuration.md) | [Configuration overview](configure/README.md), [extension chooser](extend/choose-an-extension-point.md), and [client onboarding](getting-started/client-onboarding.md) |
| Find a command | [CLI reference](reference/cli.md) | The command's guide, preview, or help output |
| Understand a data contract | [Schema catalog](reference/schemas.md) | The linked schema and its owning component |
| Use Pixel for the first time | [First real conversation](use/first-conversation.md) | [Status and evidence](concepts/status-and-evidence.md) |
| Operate or recover Pixel | [Operations runbook](operations/runbook.md) | [Upgrade and rollback](operations/update-and-rollback.md), [incident response](operations/incident-response.md), and [support packet](operations/support-bundle.md) |
| Review security and privacy | [Security overview](security/README.md) | [Threat model](security/threat-model.md) and [security assurance](security/assurance.md) |
| Change code or docs | [Contributing](../CONTRIBUTING.md) | [Documentation contract](contributing/documentation-contract.md) and [architecture](../ARCHITECTURE.md) |
| Review release or qualification evidence | [Qualification](../QUALIFICATION.md) | [Compatibility record](../OPENCLAW-COMPATIBILITY.md) and the exact versioned live audit linked from [status](status.md) |

## Current foundation

The index, status, CLI, schema, and configuration pages are additive. Existing root guides remain in place while their contracts and inbound links are audited. Versioned live audits, compatibility files, qualification records, and release artifacts have not been moved.

## Owner journey

1. [Check the host and private-input requirements](getting-started/requirements.md).
2. Follow the [clean-host quickstart](getting-started/quickstart.md).
3. Use the [first-deployment decision tree](getting-started/first-deployment.md) for only the enabled limbs.
4. Complete [client onboarding and handoff](getting-started/client-onboarding.md) when another owner/client will operate the deployment.
5. Complete the [post-install checklist](getting-started/post-install-checklist.md).
6. Run a [first real configured-model conversation](use/first-conversation.md).
7. Use [status and evidence](concepts/status-and-evidence.md) to keep repository, installed-host, synthetic, live-backend, recovery, and acceptance claims separate.

## Concepts

- [What is Pixel?](concepts/what-is-pixel.md)
- [Architecture overview](concepts/architecture-overview.md)
- [Trust, authority, and evidence](concepts/trust-authority-and-evidence.md)
- [Lifecycle and state](concepts/lifecycle-and-state.md)
- [Capability status model](concepts/capability-status-model.md)
- [Glossary](reference/glossary.md)

## Configuration

- [Configuration overview](configure/README.md)
- [Deployment profiles](configure/deployment-profiles.md)
- [Capability profiles](configure/capability-profiles.md)
- [Models and providers](configure/models-and-providers.md)
- [Source limbs](configure/source-limbs.md)
- [Operations and Frontier](configure/operations-and-frontier.md)
- [Client overlays](configure/client-overlays.md)
- [Generated configuration source map](reference/configuration.md)

## Generated reference

- [CLI reference](reference/cli.md) and [command-family matrix](reference/cli/README.md)
- [Schema catalog](reference/schemas.md)
- [Configuration source map](reference/configuration.md)
- [Services, paths, and ports](reference/services-paths-and-ports.md)
- [Support and qualification matrix](reference/support-matrix.md)
- [Release evidence index](releases/evidence-index.md)
- [Complete Markdown inventory](reference/document-inventory.md)

## Use Pixel

- [First real conversation](use/first-conversation.md)
- [Control surface](use/control-surface.md)
- [Sources and proposals](use/sources-and-proposals.md)
- [Operations](use/operations.md)
- [Frontier review](use/frontier-review.md)
- [Deep Work status and source boundary](use/deep-work.md)

## Extend Pixel

- [Choose an extension point](extend/choose-an-extension-point.md)
- [Signed projection limbs](extend/projection-limbs.md)
- [Local capability declarations](extend/local-capability-packs.md)
- [Operations action packs](extend/operations-action-packs.md)
- [Frontier task restriction packs](extend/frontier-task-packs.md)
- [Gateway extensions](extend/gateway-extensions.md)
- [Validated extension workflow example](extend/examples/README.md)

## Security and privacy

- [Security overview](security/README.md)
- [Threat model guide](security/threat-model.md)
- [Boundary reference](security/boundary-reference.md)
- [Security assurance](security/assurance.md)
- [Evaluation and qualification boundaries](security/evaluations.md)
- [Sealed-corpus custody](security/sealed-corpus.md)

## Operations

- [Runbook](operations/runbook.md)
- [Command cheat sheet](operations/command-cheat-sheet.md)
- [Verify and diagnose](operations/verify-and-diagnose.md)
- [Backup and restore](operations/backup-and-restore.md)
- [Update and rollback](operations/update-and-rollback.md)
- [Gateway token rotation](operations/gateway-token-rotation.md)
- [Incident response entry point](operations/incident-response.md)
- [Monitoring and logs](operations/monitoring-and-logs.md)
- [Troubleshooting](operations/troubleshooting.md)
- [Safe support packet](operations/support-bundle.md)
- [Owner and operator FAQ](operations/faq.md)
- [Decommission](operations/decommission.md)

## Releases and qualification

- [Release overview](releases/README.md)
- [Maintainer runbook](releases/maintainer-runbook.md)
- [Release qualification](releases/qualification.md)
- [OpenClaw upstream intake](releases/upstream-intake.md)
- [Release failure guide](releases/failure-guide.md)
- [Generated release evidence index](releases/evidence-index.md)

## Contributing and maintenance

- [Codebase map](contributing/codebase-map.md)
- [Development setup](contributing/development-setup.md)
- [Testing](contributing/testing.md)
- [High-risk change map](contributing/high-risk-change-map.md)
- [Generated and private files](contributing/generated-files.md)
- [Pull request guide](contributing/pull-request-guide.md)
- [Documentation contract](contributing/documentation-contract.md)

## Documentation rules

- Generated pages say how to regenerate them and are checked for drift.
- Maintained pages identify their audience, document type, feature status, owners, and source-of-truth paths.
- Current product guides do not copy release versions or provider pins from generated contracts into prose.
- Historical evidence stays historical. Candidate evidence is not rewritten as Supported or live acceptance.
- Private paths, credentials, user content, and live evidence do not belong in examples.

See the [documentation contract](contributing/documentation-contract.md) for the enforced metadata and contribution rules.
