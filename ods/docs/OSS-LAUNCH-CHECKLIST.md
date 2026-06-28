# ODS OSS Launch Checklist

Date: 2026-03-02
Scope: `/home/user/ods` (Strix Halo variant)

## Completed This Session

- [x] Fix FLUX background download shell block in [`install.sh`](../install.sh) (robust env/quoting for `nohup bash -c`).
- [x] Fix Phase C test parser error in [`tests/test-phase-c-p1.sh`](../tests/test-phase-c-p1.sh) (quote-safe regex).
- [x] Add installer capability profile contract and loader wiring:
  - [`config/capability-profile.schema.json`](../config/capability-profile.schema.json)
  - [`scripts/build-capability-profile.sh`](../scripts/build-capability-profile.sh)
  - [CAPABILITY-PROFILE.md](CAPABILITY-PROFILE.md)
- [x] Add capability-aware preflight and machine-readable reporting:
  - [`scripts/preflight-engine.sh`](../scripts/preflight-engine.sh)
  - [PREFLIGHT-ENGINE.md](PREFLIGHT-ENGINE.md)
- [x] Add backend runtime contracts and loader:
  - [`config/backends/`](../config/backends)
  - [`scripts/load-backend-contract.sh`](../scripts/load-backend-contract.sh)
  - [BACKEND-CONTRACT.md](BACKEND-CONTRACT.md)
- [x] Upgrade Windows/macOS installer stubs to MVP flows:
  - [`installers/windows.ps1`](../installers/windows.ps1) (WSL delegation)
  - [`installers/macos.sh`](../installers/macos.sh) (doctor/preflight)
- [x] Add ODS Doctor diagnostics report:
  - [`scripts/ods-doctor.sh`](../scripts/ods-doctor.sh)
  - [ODS-DOCTOR.md](ODS-DOCTOR.md)
- [x] Add one-command installer simulation harness:
  - [`scripts/simulate-installers.sh`](../scripts/simulate-installers.sh)
  - Outputs: `artifacts/installer-sim/summary.json`, `artifacts/installer-sim/SUMMARY.md`
- [x] Add launch-claim truth table:
  - [PLATFORM-TRUTH-TABLE.md](PLATFORM-TRUTH-TABLE.md)

## P0: Must Fix Before OSS Launch

1. **Unify compose expectations across tests/scripts/docs** ✅ Completed (2026-03-02)
- Why: This repo uses `docker-compose.base.yml` + GPU overlays, but some tests/scripts had stale fallbacks.
- Evidence:
  - [`tests/integration-test.sh:92`](../tests/integration-test.sh)
  - [`tests/test-bootstrap-mode.sh:27`](../tests/test-bootstrap-mode.sh)
  - [`scripts/upgrade-model.sh:202`](../scripts/upgrade-model.sh)
- Owner: Core Maintainer
- Effort: M (0.5-1.5 days)
- Exit criteria: CI/test scripts pass against Strix compose or support both compose files.

2. **Add and validate `.env.example` for reproducible installs** ✅ Completed (2026-03-02)
- Why: Tests expect it; migration script references it; file is currently missing.
- Evidence:
  - [`tests/integration-test.sh:297`](../tests/integration-test.sh)
  - [`scripts/migrate-config.sh:116`](../scripts/migrate-config.sh)
- Owner: Core Maintainer
- Effort: S (1-3 hours)
- Exit criteria: `.env.example` committed and referenced variables validated by tests.

3. **Fix stale/missing doc links and path references** ✅ Completed (2026-03-03)
- Why: README/Quickstart had stale workflow references.
- Owner: Docs Maintainer
- Effort: S (1-2 hours)
- Exit criteria: no broken local links in top-level docs.

4. **Add license file in this publishable repo root** ✅ Completed (2026-03-02)
- Why: README advertises Apache 2.0, but `/home/user/ods` has no `LICENSE`.
- Owner: Maintainer/Legal
- Effort: S (<1 hour)
- Exit criteria: `LICENSE` present and matches stated license.

5. **Run launch smoke tests on a machine with Docker available**
- Why: current environment has no Docker CLI/daemon, so runtime readiness is unverified.
- Evidence:
  - `scripts/ods-preflight.sh` reports Docker not running.
  - `scripts/ods-test.sh --quick` fails early (`docker not installed`).
- Owner: Release Engineer
- Effort: S-M (2-4 hours)
- Exit criteria: preflight + quick test pass on target host.

## P1: Strongly Recommended Before/Right After Launch

1. **Split NVIDIA vs Strix docs or add clear command matrix**
- Why: mixed instructions (legacy llama-server and current `llama-server:8080`) create operator confusion.
- Evidence:
  - [`README.md`](../README.md), [`FAQ.md`](../FAQ.md), [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- Owner: Docs Maintainer
- Effort: M (0.5-1 day)

2. **Modernize old `docker-compose` command style in docs**
- Why: docs mix `docker-compose` and `docker compose`; standardizing reduces support friction.
- Evidence:
  - [PROFILES.md](PROFILES.md)
- Owner: Docs Maintainer
- Effort: S (1-2 hours)

3. **Refactor tests to mode-aware compose selection**
- Why: tests are currently tuned for legacy `docker-compose.yml` layouts.
- Evidence:
  - [`tests/integration-test.sh`](../tests/integration-test.sh)
  - [`tests/test-bootstrap-mode.sh`](../tests/test-bootstrap-mode.sh)
- Owner: QA/Infra
- Effort: M-L (1-2 days)

4. **Add CI workflow for shell lint + test script syntax**
- Why: catches regressions like quoting/parser breaks pre-merge.
- Owner: QA/Infra
- Effort: M (0.5-1 day)

## Suggested Launch Gate

Ship only after all P0 items are complete and the following command set is green on target hardware:

```bash
./scripts/ods-preflight.sh
./scripts/ods-doctor.sh
./scripts/ods-test.sh --quick
bash tests/test-phase-c-p1.sh
```
