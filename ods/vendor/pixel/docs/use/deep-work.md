---
title: Understand Pixel Deep Work
doc_type: concept
audience: [owner, operator, contributor, security-reviewer]
feature_status: development-disabled
owners: [documentation, architecture, security]
sources_of_truth: [RELEASE-MANIFEST.json, DEEP-WORK.md, deploy/work-controller/, schemas/work-goal-v1.schema.json, schemas/work-capability-runtime-v2.schema.json, tests/deep-work-contract.test.mjs]
last_verified_at: 2026-08-27
---

# Understand Pixel Deep Work

Deep Work source defines a separate long-horizon planning, custody, runner, verification, capability, knowledge, fleet, and recovery architecture. The current release contract fixes its runtime to **development-disabled** and admission-only, with no runtime, tool call, network, or external-effect authority.

Do not use source-present commands, schemas, generated units, tests, model qualification, prepared bundles, or synthetic evidence as a claim that Deep Work is active or Supported.

## Inert source lifecycle

The source can create and validate bounded inputs, guided briefs, child proposals, assemblies, goal/controller bundles, launch preparation, dormant stage receipts, service renders, status projections, and exact lifecycle reviews. These objects remain inert until a future release satisfies its promotion contract.

Preparation and stage do not create a ready ledger, schedule, service, worker, lease, model call, network access, external effect, or completion claim. Rendering a unit does not install or activate it.

## Execution architecture described by the contract

A future enabled path would separate:

- the owner request from private broker policy;
- immutable child plans from expiring single-use capability leases;
- disposable runners from independent verifiers;
- durable checkpoints from model self-report;
- capability packs from per-job authorization and consumption;
- local knowledge ciphertext from its external key and deletion ledger;
- local providers from separately enabled remote policies/credentials/egress.

This architecture does not reuse Operations as a generic runner or Frontier as a generic prompt forwarder.

## Pause, resume, and cancel semantics

Source-level pause can stop future controller scheduling at an exact checkpoint; it does not kill a live worker. Resume requires exact paused-state review. Cancel can settle an inactive goal or an unlaunched child through lease revocation; a started child requires terminal cleanup evidence. Cancellation is deliberately not arbitrary process termination.

The browser can expose these controls only under separate private policy and fixed controller configuration, and the current runtime-disabled status still governs what they can accomplish.

## v1 and v2 boundaries

Legacy v1 schemas describe the broader future capability architecture. The current v2 admission contract is deliberately narrower: disabled, network-none, no authority, and no external effects. Documentation and tests must not combine the broad design vocabulary with an enabled-runtime claim.

## Qualification and promotion requirements

Promotion requires exact source/contracts, deterministic failure/recovery/race tests, supported-host/systemd evidence, real contained model/capability behavior, multi-day/endurance evidence where required, security evaluation, clean upgrade/rollback, first-user usability, and independent acceptance. Each receipt must state what it does not prove.

Until the generated [status](../status.md) changes through that release process, treat Deep Work as inspectable development source only.
