---
title: Pixel CLI reference
doc_type: reference
audience: [owner, operator, contributor, maintainer]
feature_status: mixed
owners: [documentation]
sources_of_truth: [pixel]
generated_by: scripts/docs/generate-cli-reference.mjs
last_verified_at: generated
---

<!-- GENERATED FILE. Run `node scripts/docs/generate.mjs --write`; do not edit by hand. -->

# Pixel CLI reference

The root dispatcher exposes **110 user-facing commands**, plus `help`. This index is generated from the help and dispatch blocks together; generation fails if either side contains a stale or invented command.

Descriptions are concise routing summaries, not proof that a command is Supported or safe to run on a particular host. The [command-family index](cli/README.md) adds mutation, confirmation, exit, status, and source semantics. Review the linked guide and the command's own help or preview mode before any privileged, destructive, provider, or external-effect operation.

## [Install, configure, and maintain](cli/install-configure-maintain.md)

- `./pixel bootstrap` — Verify or install host dependencies (use --apply to install)
- `./pixel configure` — Generate .env, gateway environment, and client workspace
- `./pixel services` — Manage reference SearXNG/model services
- `./pixel plan` — Render, validate, and summarize the proposed deployment
- `./pixel apply` — Apply the reviewed plan (requires --confirm)
- `./pixel verify` — Run post-deployment health and policy checks
- `./pixel doctor` — Show rounded local hardware and model-readiness guidance
- `./pixel authorize` — Authorize the configured Google Workspace account
- `./pixel ui` — Start the loopback-only local onboarding and status page
- `./pixel limbs` — Show enabled/disabled modular capability limbs
- `./pixel rollback` — Restore the preceding Pixel release/configuration
- `./pixel backup` — Create an age-encrypted private-state backup and checksum
- `./pixel restore` — Validate, rehearse, or restore an age-encrypted private-state backup
- `./pixel rotate` — Rotate a credential transactionally (gateway is currently supported)
- `./pixel package` — Build a versioned, checksummed handoff archive
- `./pixel test` — Run repository and clean-room workflow tests
- `./pixel pressure` — Repeatedly fuzz and exercise Operations safety/capability paths
- `./pixel help` — Show this help

## [Sources and external actions](cli/sources-external-actions.md)

- `./pixel source-broker` — Install/refresh the isolated Source Broker (requires --confirm)
- `./pixel source-show` — Snapshot and inspect one Calendar proposal outside Pixel
- `./pixel source-approve` — Apply one hash-bound Calendar proposal outside Pixel (requires --confirm)
- `./pixel source-reconcile` — Reconcile an indeterminate Calendar create or update without retrying it
- `./pixel github-action` — Apply or reconcile one exact allowlisted GitHub proposal
- `./pixel action-journal` — Inspect or cancel one still-unsent external action journal

## [Operations](cli/operations.md)

- `./pixel ops-broker` — Install the isolated Operations Broker (requires --confirm)
- `./pixel ops-keygen` — Generate the isolated Operations SSH identity (requires --confirm)
- `./pixel ops-target` — Enroll one verified dedicated runner target (requires --confirm)
- `./pixel ops-target-refresh` — Refresh helpers on one enrolled runner without changing trust
- `./pixel ops-target-actions` — Install private action-pack configuration on one runner (requires --confirm)
- `./pixel ops-show` — Show one immutable Operations plan and current status
- `./pixel ops-approve` — Approve one exact Operations plan hash (requires --confirm)
- `./pixel ops-authority` — Inspect, grant, revoke, and audit bounded authority
- `./pixel ops-policy-migrate` — Convert one private v1 Operations policy to reviewable v2 output
- `./pixel ops-policy-tighten` — Remove reviewed v1 compatibility grants from a v2 policy
- `./pixel ops-action-pack` — Configure one reusable action pack and private target mapping
- `./pixel ops-pause` — Emergency-pause Operations execution (requires --confirm)
- `./pixel ops-resume` — Resume Operations execution (requires --confirm)

## [Frontier](cli/frontier.md)

- `./pixel frontier-broker` — Install the isolated Frontier Broker (requires --confirm)
- `./pixel frontier-show` — Inspect one immutable sanitized Frontier payload and status
- `./pixel frontier-approve` — Approve one exact Frontier payload hash (requires --confirm)
- `./pixel frontier-usage` — Show content-free rolling Frontier usage and routing totals
- `./pixel frontier-live-qualify` — Prepare and explicitly run one fixed synthetic provider check
- `./pixel frontier-budget` — Draft or apply one exact private custom Frontier budget change
- `./pixel frontier-authority` — Inspect, grant, revoke, and audit bounded Frontier authority
- `./pixel frontier-pause` — Emergency-pause Frontier egress (requires --confirm)
- `./pixel frontier-resume` — Resume Frontier egress (requires --confirm)

## [Qualification, release, and migration](cli/qualification-release-migration.md)

- `./pixel promotion-status` — Build a content-free, fail-closed release-readiness index
- `./pixel outcome-task-admit` — Admit one exact private backend-neutral evaluation task
- `./pixel outcome-compare` — Compare exact private Pixel/Codex real-task outcome runs
- `./pixel outcome-campaign` — Require every baseline and declared fault outcome pair
- `./pixel qualify-hosts` — Run the disposable Ubuntu/Debian real-systemd qualification matrix
- `./pixel release-manifest-migrate` — Convert an unversioned release manifest to schema v1
- `./pixel upstream` — Check registries or prepare a verified OpenClaw candidate
- `./pixel release-sign` — Sign one validated release-update envelope (requires --confirm)
- `./pixel release-qualification-sign` — Sign one Candidate envelope for qualification only
- `./pixel release-qualification-inspect` — Verify a Candidate qualification signature without update authority
- `./pixel release-qualification-prepare` — Copy one exact Candidate bundle into a disposable qualification root only (requires --confirm)
- `./pixel release-qualification-rehearse` — Extract and parse one qualification-staged candidate without executing it (requires --confirm)
- `./pixel release-qualification-activation-preview` — Derive an exact qualification activation hash without executing candidate code
- `./pixel release-qualification-activation-claim` — Atomically claim one qualification-root-only activation without executing candidate code (requires --confirm)
- `./pixel release-qualification-host-run` — Atomically acquire one private host-run for an already-claimed qualification activation without executing candidate code (requires --confirm)
- `./pixel release-qualification-execution-claim` — Write the durable qualification execution claim before any candidate process may start (requires --confirm)
- `./pixel release-qualification-execution-result` — Record the immutable content-free result of one claimed qualification execution (requires --confirm)
- `./pixel release-qualification-execution-interruption-preview` — Inertly derive the exact terminal interruption hash for one post-start indeterminate qualification execution
- `./pixel release-qualification-execution-interruption-record` — Write the two private immutable terminal interruption artifacts for one post-start indeterminate qualification execution (requires --confirm plus the exact preview hash)
- `./pixel release-qualification-host-execute` — Run one bounded candidate probe under disposable-host custody and emit a content-free observation (requires --confirm)
- `./pixel update-inspect` — Verify one signed release bundle without executing candidate code
- `./pixel update-prepare` — Copy one eligible verified bundle into private staging (requires --confirm)
- `./pixel update-rehearse` — Safely extract and parse one staged candidate (requires --confirm)
- `./pixel update-activate` — Preview or transactionally activate one exact rehearsed candidate
- `./pixel update-reactivate` — Preview or re-activate one exact successfully rolled-back candidate
- `./pixel update-reactivation-rollback` — Preview or roll back one exact reactivated candidate
- `./pixel update-reactivation-recover` — Diagnose or finalize one interrupted reactivation receipt
- `./pixel update-rollback` — Preview or restore one exact activated update (requires --confirm)
- `./pixel update-recover` — Diagnose or finalize one interrupted update receipt (requires --confirm)
- `./pixel update-cleanup` — Reclaim one completed rolled-back update after preserving audit history
- `./pixel update-archive` — Preserve one terminal failed rollback outside bounded staging (requires --confirm)
- `./pixel update-reactivation-archive` — Preserve one terminal no-live-mutation reactivation failure outside bounded staging (requires --confirm)
- `./pixel migrate-legacy-clean` — Review, rehearse, and finalize a terminal-only clean 3.2.2 to 4.2 migration

## [Customization](cli/customization.md)

- `./pixel limb-kit` — Generate, sign, verify, and manage constrained customization packs
- `./pixel client-kit` — Generate or validate a private golden-core/client-overlay contract
- `./pixel extension-hash` — Compute a custom gateway extension's approved tree digest

## [Deep Work and local work](cli/deep-work-local-work.md)

- `./pixel work-compile` — Compile one bounded Deep Work job into an exact plan and single-use lease
- `./pixel work-model-qualify` — Run the fixed synthetic benchmark against one exact loopback local model
- `./pixel work-model-policy` — Review, bind, or inspect one exact local-model qualification in a private policy
- `./pixel work-model-backend` — Review/render an inert backend launch or inspect the actual private runtime
- `./pixel work-input-pack` — Snapshot explicitly selected local directories into one inert private input bundle
- `./pixel work-goal-draft` — Turn one guided private brief into reviewable bounded child-job proposals
- `./pixel work-goal-assemble` — Bind one reviewed draft to inert goal and controller bundles
- `./pixel work-goal-launch` — Prepare, inspect, or stage one complete reviewed long-horizon goal
- `./pixel work-goal-prepare` — Derive an inert immutable long-horizon goal from exact child jobs
- `./pixel work-goal-controller-prepare` — Validate and assemble one inert supervised goal controller
- `./pixel work-goal-stage` — Confirm and stage dormant child custody without starting any work
- `./pixel work-goal` — Control the disabled durable Deep Work goal ledger and scheduling state
- `./pixel work-context` — Create, find, inspect, or fork private checkpoint-bound work sessions
- `./pixel work-context-guide` — Plain-language reviewed creation, forking, and removal of private work sessions
- `./pixel work-cycle` — Run one supervised, restartable local Deep Work reconciliation cycle
- `./pixel work-continuous` — Run bounded continuous Deep Work while durable progress is occurring
- `./pixel work-capability-pack` — Inspect, install, image-admit, health-probe, revoke, recover, or audit disabled Deep Work tool packs
- `./pixel work-knowledge` — Review and exactly apply disabled local knowledge-vault lifecycle operations
- `./pixel work-knowledge-guide` — Plain-language reviewed local knowledge setup, add, find, remove, rotation, and recovery
- `./pixel work-knowledge-restore` — Deep-audit and safely prepare one offline historical vault for restore
- `./pixel work-accept` — Review or exactly accept one safe Scout/Researcher/Data Lab candidate
- `./pixel work-cancel` — Review and exactly cancel an inactive goal or one with terminal cleanup evidence
- `./pixel work-pause` — Durably pause future supervised goal steps from one private controller config
- `./pixel work-resume` — Review and exactly resume one durably paused supervised goal
- `./pixel work-service` — Render, inspect, explicitly install, activate, or remove one goal service
- `./pixel work-fleet` — Inspect, initialize, or run one durable fair turn across a Deep Work goal fleet
- `./pixel work-fleet-cleanup` — Reconcile and release one crash-held fleet turn after exact cleanup
- `./pixel work-fleet-host-evidence` — Generate expiring live capacity evidence for one fleet
- `./pixel work-fleet-service` — Render, inspect, install, activate, or remove one fleet-wide service
