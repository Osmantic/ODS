# September 2026 public-beta promotion record

Status: preparation in progress; full release qualification is not established.

This record accompanies [promotion PR #6515](https://github.com/Osmantic/ODS/pull/6515).
It describes a move into the development `main` branch, not a stable release,
new version number, or change to `release/2.6.x`. The current stable tag remains
`v2.6.0`; see [Release Channels](RELEASE_CHANNELS.md).

## Candidate and user impact

The initial review compares public-beta
`81fdfc2e3a1536737ae03184e30e5d71ff95ac62` with main
`21f4b3a64dd2a2fac1163f446806091c25b6b814`. Subsequent fixes must be recorded and
the final promoted commit must be bound to its own CI and validation evidence.
Evidence from the initial candidate must not be relabeled as a later-head pass.

The promotion includes the bundled Portal assistant and its Pixel runtime,
dashboard and model-routing changes, expanded native platform handling, and
installer/lifecycle fixes. See [Unreleased](../CHANGELOG.md#unreleased).
Native Windows does not install the Portal host runtime; qualifying WSL
installations use the Linux path. Platform eligibility is not a guarantee of
agent task quality.

Merging affects new users: the hosted Linux/macOS quickstart installers follow
`main`, and the native Windows quickstart downloads `main`. A main merge is
therefore an installer-facing change even without a stable tag. Reproducible
deployments should use a pinned release or audited commit.

## Evidence available

- At the initial candidate, `bash ods/scripts/release-gate.sh` reached
  `[PASS] release gate` on September 23, 2026. It covered source/bundle integrity,
  shell syntax, version and release-claim consistency, generated configuration,
  dependency pins, installer/network contracts, platform smoke simulations,
  and backup/restore rollback contracts. The gate log SHA-256 is
  `c76992bde91320cb1ab19d54a49ef494d21d4607469ae0814b5e927c02c63736`.
- The initial promotion CI exposed Python and shell lint failures that had not
  run on public-beta. Their resolution and final CI are required before this
  candidate can be considered ready for review.
- Prior installed tests span several revisions. They provide useful failure
  evidence but do not establish acceptance of this exact candidate on all six
  target machines.

The source gate uses fixtures and simulations for several platform paths. It
does not prove physical-machine installation, model behavior, or data recovery.

## Known limitations and missing acceptance

The Pixel fleet campaign was capped before any machine completed the full
qualification sequence. It is not being represented as a six-host green run.
Observed task failures included incomplete or malformed coding deliveries,
generated-interface usability problems, unreliable sourced research, and long
task latency. Narrow fixes improve particular failure paths; they do not prove
general agent reliability.

The following remain unqualified for the final promotion candidate:

- The complete [Pixel/Portal acceptance suite](pixel/PORTAL-REGRESSION-ACCEPTANCE.md),
  including follow-up edits, evidence-backed research, error honesty, and
  continuity after restart and update.
- The subsequent general UI and model-switchboard phases. The capped campaign
  did not reach these phases; missing results are not passes.
- Exact-head fresh-install acceptance across the six intended machines, plus
  installed main-to-candidate update, rollback, and reboot continuity with
  retained user data and agent workspaces.

Removing a distracting readiness banner does not change this acceptance state.
Runtime reachability, a successful single conversation, and passing authored
tests each prove less than the complete required user journeys.

## Promotion and recovery requirements

Before merge, record the final source identity, resolve blocking CI and reviewed
installer defects, and obtain the repository's required maintainer approvals.
Apply [Release Validation](RELEASE_VALIDATION.md) and the
[High-Risk Change Map](HIGH_RISK_CHANGE_MAP.md): if a scoped alternative is
accepted, record its exact scope, evidence, and remaining gaps. This document
does not waive those requirements or authorize an administrative bypass.

For an affected installation, retain its previous source identity and backups
of configuration, secrets, persistent volumes, and native agent workspaces
before updating. Follow the [Maintainer Runbook](MAINTAINER_RUNBOOK.md#rollback-procedure)
and platform-specific recovery documentation. Reverting a Git merge alone
does not restore runtime data, downloaded artifacts, or native host state.
Installed rollback remains an acceptance item, not a guarantee supplied by
the source-level rollback fixture.
