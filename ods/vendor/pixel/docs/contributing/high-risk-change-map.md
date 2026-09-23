---
title: Pixel high-risk change map
doc_type: reference
audience: [contributor, security-reviewer, maintainer]
feature_status: mixed
owners: [documentation, maintainers, security]
sources_of_truth: [CONTRIBUTING.md, tests/, security-evals/, QUALIFICATION.md, SECURITY-ASSURANCE.md]
last_verified_at: 2026-08-27
---

# Pixel high-risk change map

Select every applicable row. “Focused checks” are additions to the full gate, not replacements.

| Change surface | Principal risk | Focused checks/evidence | Documentation/rollback impact |
|---|---|---|---|
| CLI dispatch or parser | Invented/stale command, confirmation bypass, exit-code drift | CLI/docs generation, parser negatives, static/e2e | CLI ref, task guide, recovery/error behavior |
| Onboarding/config/render | Secret projection, unsafe default, generated/authored confusion | configure/schema/control/e2e, source-preservation tests | configuration reference, plan/apply/rollback |
| Control/chat UI | token/origin/replay leak, hidden authority, false state | control server/UI/boundary/pressure, conversation contracts | control guide, status language, privacy limits |
| Source Broker/projection | credential/raw-content leak, injection, incomplete pagination | source broker/audit/pressure, email injection, modular e2e | source-limb and proposal guidance; revoke/rebuild path |
| Calendar/GitHub external action | duplicate or uncertain effect | snapshot/action journal/broker tests, race/reconciliation cases | show/approve/reconcile procedure and incident stop |
| Operations policy/broker/runner | shell/target/lease widening, identity substitution | ops broker/runner/human approval, runner boundary, Operations live/pressure | authority/target docs; pause, revoke, rollback |
| Frontier policy/broker/provider | private egress, billing/auth confusion, replay/cache drift | frontier broker/budget/live qualification/isolation/pressure | disclosure, budget, approval, provider recovery |
| Gateway extension or signed pack | undeclared tools/authority, tree drift, removal residue | limb-kit/client-kit/archive/isolation/pressure plus owning plugin tests | extension tutorial, signature/install/remove rollback |
| Backup/restore/migration | forged archive, path escape, partial replacement, resurrection | backup audit, restore receipt/journal, migration e2e/transactions | validation/rehearsal/replace/rollback and key custody |
| Release/update/signing | wrong exact head/tree, candidate promotion, unsafe archive, interrupted activation | release generation/artifacts/update/interruption/operator, assurance, signed canary | status/evidence index, upgrade/rollback/failure guide |
| Sandbox/container/systemd | network/host mount/privilege escape, wrong owner | deployment isolation, supported-host, image reproducibility, service-unit/live probes | requirements, verification, decommission |
| Deep Work prepare/stage/launch | inert object becomes authority or partial custody | goal draft/assemble/prepare/stage/launch/controller tests | development-disabled status and no-authority statements |
| Deep Work pause/resume/cancel | orphan work, stale checkpoint, generic kill | pause/resume/cancel/terminal journey/crash/service/fleet cleanup tests | eligibility, exact confirmation, recovery state |
| Deep Work capability/knowledge/provider | runtime/network/credential widening or data resurrection | capability v1/v2, image/health/queue, knowledge restore, provider custody/egress tests | status, keys, tombstones, qualification and rollback |
| Docs/status/generator | stale facts or status inflation | docs full check, fixtures for failure, generated drift | source map, owner, redirect/move review |

P0/P1 findings cannot be waived. P2/P3 exceptions require an owner, expiry, and compensating control. Authority, isolation, credential, release, and recovery changes require maintainer review.
