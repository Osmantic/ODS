---
title: Pixel pull request guide
doc_type: how-to
audience: [contributor, security-reviewer, maintainer]
feature_status: supported
owners: [documentation, maintainers]
sources_of_truth: [CONTRIBUTING.md, .github/pull_request_template.md, .github/workflows/, QUALIFICATION.md]
last_verified_at: 2026-08-27
---

# Pixel pull request guide

Keep a change narrow enough that reviewers can identify its authority, data, network, filesystem, recovery, upgrade, and documentation effects.

## Before editing

- Base the branch on current `main` and record the exact base commit.
- Select every applicable [high-risk change](high-risk-change-map.md) row.
- Identify source of truth, generated consumers, schemas, focused tests, live gates, rollback, and docs.
- Keep another live task's files and runtime state outside your ownership.

## Required pull request facts

- exact head commit/tree and base;
- concise change boundary and excluded surfaces;
- data/credential/authority/network/filesystem effects;
- mutation, idempotency, claim/replay, failure, recovery, and rollback behavior;
- generated files and why they changed;
- focused tests plus complete gate results;
- live, synthetic, model-off, blocked, partial, and not-applicable evidence labeled separately;
- P0–P3 findings and any owner/expiry/compensating control allowed for lower priorities;
- documentation and support impact.

Never paste private evidence into the pull request.

## Review sequence

1. Verify exact base/head and inspect changed files.
2. Review source and schema authority before authored tests.
3. Run focused adversarial, failure, race, and recovery checks.
4. Run generated-file and documentation drift checks.
5. Run the full gate and packaging on the supported lane.
6. Require independent review for authority, isolation, credential, release, or recovery changes.
7. Immediately before integration, recheck head, checks, conflicts, policy, and evidence freshness.

A green pull request does not authorize deployment, publication, signing, or live-state mutation. Those remain separate owner/release workflows.
