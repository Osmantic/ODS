# Maintainer Runbook

This runbook is for maintainers and downstream fork operators who need to ship,
triage, roll back, or safely review operational changes.

It is intentionally practical. The goal is to make ODS operable by
people who did not write the original installer.

## Maintainer Responsibilities

Maintainers are expected to protect:

- install reliability across supported platforms;
- localhost-first security defaults;
- generated secret handling;
- service manifest and compose consistency;
- dashboard-api auth and host-agent boundaries;
- release validation receipts;
- rebase-friendly extension and fork paths.

When in doubt, prefer a smaller change with stronger validation over a clever
change with broad invisible effects.

## Release Checklist

Before publishing or recommending a release candidate:

1. Confirm the commit under test.
2. Confirm no unrelated local changes are included.
3. Run required CI and focused checks for changed areas.
4. For operational changes, run the release-grade fleet or an explicitly scoped
   release substitute.
5. Confirm capability deferrals are resolved by the capabilities watcher or
   called out as deferred.
6. Confirm lifecycle checks pass: idempotent reinstall, `ods restart`, and
   `ods doctor`.
7. Record skipped optional surfaces, such as owner-card Talk, vision probes, AP
   mode, or disabled hardware lanes.
8. Update release notes with the commit, date, validation layer, and known
   limitations.
9. When `ods/get-ods.sh` changes, verify the rolling hosted bootstrap after the
   change merges to `main`.

## Hosted Bootstrap Verification

The public bootstrap endpoint follows repository `main` through the Osmantic
Worker. Merging `ods/get-ods.sh` updates the hosted source automatically after
edge-cache refresh; no separate bootstrap promotion or Worker deployment is
required.

For any change to `ods/get-ods.sh`:

1. Squash or merge the PR using the repository's agreed strategy.
2. Fetch the target branch and capture the exact final commit. Do not verify a
   pre-squash branch-head SHA.
3. Wait for the five-minute freshness window or purge the installer Worker
   cache.
4. Verify all twelve active Worker aliases from a checkout containing the
   final target-branch commit:

   ```bash
   git fetch origin main
   bash ods/scripts/verify-hosted-bootstrap.sh origin/main
   ```

5. Record each endpoint, `X-ODS-Channel`, `X-ODS-Source-Ref`,
   `X-ODS-Presentation`, and verification result in the PR or release receipt.

Verification is incomplete if any alias does not identify `main`, the
presentation is not `script`, or the body differs from
`origin/main:ods/get-ods.sh`.

Deploy the installer Worker only when routing, headers, validation, caching, or
endpoint configuration changes. For a bad bootstrap merge, revert it on
repository `main`, wait for or purge the cache, and verify again.

## When Release-Grade Fleet Is Required

Use the release-grade gate after changes to:

- installer phases or installer libraries;
- public bootstrap or prerequisite installation;
- Docker Compose stack selection or resolver behavior;
- core service manifests or ports;
- dashboard-api behavior used by install, setup, model, extension, or service
  management flows;
- Hermes, model routing, LiteLLM, Lemonade, or llama-server lifecycle;
- GPU/runtime detection;
- `ods-cli` lifecycle commands;
- dependency/runtime wiring for installed services.

Docs-only and narrow test-only changes normally use focused validation.

## Reading Fleet Results

Use [RELEASE_VALIDATION.md](RELEASE_VALIDATION.md) for the public gate
definition. Operationally:

- `PASS` means the lane completed and validated the expected surface.
- `DEFERRED` means the lane has not failed, but is waiting on a known condition
  such as a full model download.
- `SKIPPED` means the surface is not enabled for that install mode, such as
  owner-card Talk on a non-LAN install.
- `FAIL` means the release gate should stop until the failure is classified.

Classify failures before fixing:

| Failure shape | First question |
|---|---|
| Install exits non-zero | Did the product fail, or did a health wait/time budget expire while services recovered? |
| Compose error | Is the selected file set valid for the mode, hardware, and enabled extensions? |
| Capability failure | Is the full model loaded, or is the host still on the bootstrap model? |
| Talk failure | Is `ods-proxy` enabled and healthy, or is the owner-card surface not part of this install? |
| Dashboard API failure | Is auth configured, service state healthy, and the expected route protected? |
| Lifecycle failure | Did reinstall, restart, or doctor fail independently, or did one transient poison the next probe? |

## Rollback Procedure

If a release or merge breaks operational behavior:

1. Stop new merges into the affected area.
2. Identify the last known-good commit and validation receipt.
3. Reproduce the failure on the smallest matching surface.
4. Decide whether to revert or forward-fix.
5. If reverting, revert only the offending commit or PR.
6. Rerun the focused failing lane.
7. Rerun release-grade validation if installer, compose, lifecycle, or runtime
   behavior was affected.
8. Document the incident in the PR or release notes.

Avoid broad repository resets or unrelated cleanup while handling a release
rollback.

## High-Risk Areas

Treat these areas as high-risk:

- `ods-cli`;
- `install-core.sh`;
- `installers/phases/*`;
- `installers/lib/compose-select.sh`;
- `scripts/resolve-compose-stack.sh`;
- `docker-compose.*.yml`;
- `extensions/services/*/manifest.yaml`;
- dashboard-api auth, host-agent, setup, extension, and model routes;
- generated config writers;
- model download, verification, swap, and bootstrap logic.

Use [HIGH_RISK_CHANGE_MAP.md](HIGH_RISK_CHANGE_MAP.md) to choose validation
before merging.

## Operator Handoff Checklist

When adding, rotating, or handing off maintenance for an upstream area or fork:

- point the operator at this runbook;
- identify the current known-good commit;
- share the latest sanitized validation result;
- list disabled or lab-only fleet lanes;
- list outstanding release-blocking issues;
- list active private patches if this is a fork;
- explain where secrets and runtime data live;
- explain how to restore from backup or reinstall cleanly.

The handoff is not complete until the receiving operator can run validation and
interpret the result without private context.
