# ODS 2.6.0 Release Notes

Historical release record for `v2.6.0`. A strict User Green stamp was not
claimed because the long six-cycle model-management matrix was intentionally
waived. This page does not establish the current stable channel or qualify
a later public-beta revision.

## Summary

ODS 2.6.0 rolls up the post-2.5.3 work on remote providers, model
switchboard, verified context selection, GPU reassignment rollback, rootless
Linux installs, Windows and macOS native-runtime stability, dashboard polish,
and release hygiene.

Select the intended channel using [Release channels](RELEASE_CHANNELS.md).
A downstream pinned to an older release needs its own update and compatibility
review before changing versions.

## Highlights

- Remote provider support now includes direct and SSH egress, policy checks,
  tunnel supervision, dashboard status, and peer ODS model operations.
- Model Switchboard gives apps a stable current-model route and propagates the
  selected context across runtimes and applications.
- NVIDIA and Linux AMD/ROCm GPU reassignment flows support larger model swaps
  with verified rollback.
- Linux rootless Docker installs can repair service bind-mount ownership inside
  the rootless namespace.
- Windows native llama-server launches now expose metrics and honor
  `LLAMA_REASONING`; stale Lemonade listener PIDs are handled safely.
- macOS OpenCode config compatibility, Perplexica Lemonade routing, Token Spy
  Postgres cursors, voice readiness, and model-memory estimates were corrected.
- Remote-provider direct egress now pins validated DNS resolutions to close
  DNS-rebinding/SSRF escape paths.

## Upgrade Notes

- `v2.6.0` is the new stable line. Stable hotfixes should target
  `release/2.6.x` first, then merge forward to `main`.
- `release/2.5.x` is superseded and should only receive critical old-stable
  continuity fixes.
- Operators using remote-provider direct routes should re-run route validation
  after upgrade so the new DNS pinning and TLS identity behavior is exercised.
- Rootless Docker operators with container-owned data directories should stop
  ODS, run `ods repair rootless-ownership`, then start ODS if a service reports
  permission errors under `data/`.
- Windows users relying on native llama-server fallback should restart the
  native runtime after upgrade so `--metrics` and `--reasoning-format` are
  present on the process command line.

## Validation Receipt

- Release tag: `v2.6.0`
- Candidate product commit: `07e2a21e3ccab197360009ebd3d66b4e6d4d0af2`
- Base product commit: `c292e00d5b60f6e4e6b331b2867346f9e9748a2c`
- Release-prep PR: `#2232`
- Release-stamp ref: `v2.6.0` tag target
- GitHub Actions at release-prep head:
  `195103787de031973b959de9f313005183cc1afb` green on 2026-07-28
- Focused local validation at candidate commit:
  - Windows parser/resolver
  - llama runtime tunables, metrics, and reasoning contracts
  - env schema and uninstall scoping
  - installer context parity and rootless doctor
  - dashboard API focused regressions: `502 passed, 5 skipped`
  - Perplexica, remote-provider egress, and Token Spy cursor tests:
    `35 passed, 1 skipped`
  - Linux NVIDIA rootless ownership contract: `25 passed`
- Release-prep fleet receipt on 2026-07-28:
  - Historical scope: Linux NVIDIA (x86_64 and aarch64), Linux AMD,
    macOS arm64, Windows NVIDIA, and Windows AMD hardware classes.
  - Private run identifiers, host aliases, and harness paths are omitted.
    The following are recorded historical results, not a new verification.
  - zero-prereq bootstrap: `6/6` lanes passed
  - regressions: `16/16` fixtures passed
  - install: all six selected hosts installed from the public bootstrap at the
    exact candidate commit
  - post-install: verify, cloud-mode contracts, dashboard, Hermes, UI policy,
    capabilities, lifecycle reinstall/restart, and `ods doctor` passed on the
    selected hosts
  - full-model finalize: all deferred Hermes/capability probes were rerun
    after full-model readiness; no install-blocked, still-deferred, or
    download-failed hosts remained
  - generated report: `REPORT.md` recorded Real product bugs: `0`,
    Harness limitations: `0`, Environment notes: `0`
- Explicitly waived or excluded surfaces:
  - six-cycle release model-management matrix was started but intentionally
    stopped as too time-consuming for this release-prep need; cycle 1 passed on
    Linux NVIDIA and Linux AMD, while the remaining generated `143`
    interruption receipts from the operator stop
  - because that matrix was waived, this candidate is not stamped strict User
    Green; publication should describe the waiver rather than claiming Model UI
    Green
  - An additional Linux aarch64 GPU target was excluded after SSH
    host-key verification failed. No result is claimed for that target.

## Known Limits

- Windows native remains an installer/runtime path with targeted validation;
  WSL2 plus Docker Desktop remains the supported Windows appliance path.
- ODS Talk owner-card probes gate only when the owner-card surface and
  `ods-proxy` are enabled for the candidate install.
- Vision probes, AP mode, custom network topologies, and downstream forks need
  their own local validation receipts.

## GitHub Release Body

```markdown
## ODS 2.6.0

ODS 2.6.0 updates the stable line with remote-provider support, Model
Switchboard and context selection, GPU reassignment rollback, rootless Linux
repair, Windows/macOS native-runtime fixes, dashboard polish, and security
hardening.

### Highlights

- Remote provider direct/SSH egress, tunnel supervision, dashboard status, and
  peer model operations.
- Model Switchboard stable routing and verified context propagation across LLM
  applications.
- Verified NVIDIA and Linux AMD/ROCm GPU reassignment with rollback.
- Rootless Docker bind-mount ownership repair for Linux installs.
- Windows native llama-server metrics and reasoning flags, plus safer Lemonade
  stale-PID handling.
- Remote-provider DNS-rebinding/SSRF hardening through validated address
  pinning with preserved TLS identity.

### Validation

- Release tag: `v2.6.0`
- Release-stamp ref: `v2.6.0` tag target
- Product candidate: `07e2a21e3ccab197360009ebd3d66b4e6d4d0af2`
- Base product commit: `c292e00d5b60f6e4e6b331b2867346f9e9748a2c`
- Gate result: `green through six-host full-model finalize; strict User Green
  not claimed because the six-cycle model-management matrix was waived`
- Known skipped/deferred surfaces: `dgx-gpu01 excluded for unverified SSH host
  key; full model-management matrix intentionally stopped after partial pass`

See `ods/CHANGELOG.md` and `ods/docs/RELEASE_NOTES_2.6.0.md` for the full
release notes and validation receipt.
```
