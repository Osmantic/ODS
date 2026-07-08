# ODS Composability Execution Board

Date: 2026-03-02  
Scope: Turn ODS into a broadly installable, highly composable OSS platform.

## North Star

By Day 90, external contributors can:

1. Install on supported platforms from a clear support matrix.
2. Add a new backend service via a stable extension manifest.
3. Add dashboard cards/routes without editing core files.
4. Pass CI matrix checks before merge.

## Status Legend

- `TODO` not started
- `IN_PROGRESS` active
- `BLOCKED` waiting dependency
- `DONE` shipped

## Workstream W1: Installer Architecture

Status: `DONE`

Milestone W1-M1 (PR-1): Extract platform detection and dispatcher  
Status: `DONE`  
Owner: Core  
Effort: 2-3 days  
Files:
- [`install.sh`](../install.sh)
- [`get-ods.sh`](../get-ods.sh)
- `installers/dispatch.sh` (new)
- `installers/linux.sh` (new)
- `installers/common.sh` (new)
Acceptance:
- Root installer delegates by platform.
- Linux path parity with current behavior.
- `bash -n` and existing shell tests remain green.
Progress notes:
- `install.sh` converted to entrypoint wrapper.
- `install-core.sh` created as current Linux implementation.
- `installers/common.sh` + `installers/dispatch.sh` added.
- Added capability profile contract (`config/capability-profile.schema.json`) and generator (`scripts/build-capability-profile.sh`), now consumed by `install-core.sh` for tier/backend/compose decisions with fallback behavior.

Milestone W1-M2 (PR-2): Add Windows/macOS stubs with explicit support messaging  
Status: `DONE`  
Owner: Core  
Effort: 1-2 days  
Files:
- `installers/windows.ps1` (new)
- `installers/macos.sh` (new)
- [`README.md`](../README.md)
- [`QUICKSTART.md`](../QUICKSTART.md)
Acceptance:
- No ambiguous “supported” language when unsupported paths are partial.
- Entry scripts route users to correct installer path.
Progress notes:
- `installers/macos.sh` and `installers/windows.ps1` stubs added.
- `install.sh` dispatch now routes to platform targets (or clear unsupported messaging).
- `installers/windows.ps1` now performs prerequisite checks and delegates to WSL installer path.
- `installers/macos.sh` now runs capability-aware preflight/doctor checks and writes a machine-readable report.
- Added hardware class mapping (`config/hardware-classes.json`, `scripts/classify-hardware.sh`) and capability-profile hardware class fields for explicit GPU-class defaults.
- `scripts/ods-doctor.sh` now emits prioritized autofix hints from preflight/runtime findings.
- Added `scripts/simulate-installers.sh` and contract fixture tests under `tests/contracts/` with CI wiring in `.github/workflows/test-linux.yml`.

## Workstream W2: Platform Support Matrix

Status: `DONE`

Milestone W2-M1 (PR-3): Publish support matrix doc + policy  
Status: `DONE`  
Owner: Docs + Core  
Effort: 1 day  
Files:
- `docs/SUPPORT-MATRIX.md` (new)
- [`README.md`](../README.md)
- [`QUICKSTART.md`](../QUICKSTART.md)
Acceptance:
- Matrix defines `Tier A/B/C` support for Linux AMD/NVIDIA, WSL, macOS.
- Every install path links to one canonical matrix.
Progress notes:
- Added `docs/SUPPORT-MATRIX.md`.
- Linked support matrix from `README.md` and `QUICKSTART.md`.

## Workstream W3: Compose Contract Unification

Status: `DONE`

Milestone W3-M1 (PR-4): Define canonical compose contract and mode overlays  
Status: `DONE`  
Owner: Infra  
Effort: 2-4 days  
Files:
- `docker-compose.strix-halo.yml` (historical; removed/renamed)
- `docker-compose.base.yml` (new)
- `docker-compose.nvidia.yml` (new)
- `docker-compose.amd.yml` (new)
- [`scripts/mode-switch.sh`](../scripts/mode-switch.sh)
Acceptance:
- Base+overlay compose strategy documented and used consistently.
- Tests no longer assume one legacy compose filename.
Progress notes:
- Added `docker-compose.base.yml`, `docker-compose.amd.yml`, and `docker-compose.nvidia.yml` scaffold files.
- `install-core.sh` now prefers `-f docker-compose.base.yml -f docker-compose.amd.yml` with legacy fallback.
- `tests/integration-test.sh` updated to validate base+overlay compose flags.
- `scripts/mode-switch.sh` now resolves `strix-halo` to base+overlay (with legacy Strix fallback).
- Added `scripts/resolve-compose-stack.sh` and integrated it in `install-core.sh` to centralize runtime/bootstrap compose matrix resolution.
- Added backend runtime contracts in `config/backends/*.json` and `scripts/load-backend-contract.sh` so health/provider wiring is data-driven.

Milestone W3-M2 (PR-5): Remove stale command styles from docs/scripts  
Status: `DONE`  
Owner: Docs + Infra  
Effort: 1 day  
Files:
- [PROFILES.md](PROFILES.md)
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- [INTEGRATION-GUIDE.md](INTEGRATION-GUIDE.md)
Acceptance:
- `docker compose` style standardized.
- Compose examples match canonical contract from W3-M1.
Progress notes:
- Updated `docs/PROFILES.md` from `docker-compose` to `docker compose`.
- Updated `docs/TROUBLESHOOTING.md` compose-file guidance to cover NVIDIA and AMD base+overlay paths.

## Workstream W4: Extension Manifest v1

Status: `IN_PROGRESS`

Milestone W4-M1 (PR-6): Create service manifest schema and loader  
Status: `DONE`  
Owner: Core API  
Effort: 3-5 days  
Files:
- `extensions/schema/service-manifest.v1.json` (new)
- `extensions/services/*.yaml` (new examples)
- [`dashboard-api/main.py`](../extensions/services/dashboard-api/main.py)
Acceptance:
- API can load service definitions from manifests.
- Health checks and feature cards reference manifest data, not hardcoded lists.
Progress notes:
- Added `extensions/schema/service-manifest.v1.json`.
- Added example manifests in `extensions/services/` for inference, voice, workflows, vector DB, and image generation services.
- `dashboard-api/main.py` now loads and merges service/feature definitions from manifests with safe fallback defaults.

Milestone W4-M2 (PR-7): Environment schema and validation  
Status: `IN_PROGRESS`  
Owner: Core  
Effort: 2-3 days  
Files:
- `.env.schema.json` (new)
- [`install.sh`](../install.sh)
- [`scripts/migrate-config.sh`](../scripts/migrate-config.sh)
- `.env.example` (generated by installer, not checked in)
Acceptance:
- `.env` validated at install/start time.
- Unknown/missing required vars produce actionable errors.
Progress notes:
- Added `.env.schema.json` with required keys and typed properties.
- Added `scripts/validate-env.sh` for schema-based `.env` validation (missing/unknown/type checks).
- `install-core.sh` now validates generated `.env` against the schema and fails with actionable logging on mismatch.
- `scripts/migrate-config.sh` now exposes `validate` command wired to the same validator.
- Added `scripts/preflight-engine.sh` and integrated capability-aware preflight reporting into `install-core.sh` with machine-readable blocker/warning output.

## Workstream W5: Dashboard Plugin Surface

Status: `DONE`

Milestone W5-M1 (PR-8): Route + navigation registry  
Status: `DONE`  
Owner: Frontend  
Effort: 3-4 days  
Files:
- [`dashboard/src/App.jsx`](../extensions/services/dashboard/src/App.jsx)
- [`dashboard/src/components/Sidebar.jsx`](../extensions/services/dashboard/src/components/Sidebar.jsx)
- `dashboard/src/plugins/registry.js` (new)
- `dashboard/src/plugins/core.js` (new)
Acceptance:
- Core routes/cards registered through a registry.
- Adding a new page requires registry entry, not editing router internals.
Progress notes:
- Added `dashboard/src/plugins/registry.js` and `dashboard/src/plugins/core.js`.
- `dashboard/src/App.jsx` now renders routes from the registry (component + props mapping).
- `dashboard/src/components/Sidebar.jsx` now derives nav items and quick links from the registry.

Milestone W5-M2 (PR-9): Feature cards from backend metadata  
Status: `DONE`  
Owner: Frontend + API  
Effort: 2-3 days  
Files:
- [`dashboard/src/pages/Dashboard.jsx`](../extensions/services/dashboard/src/pages/Dashboard.jsx)
- [`dashboard-api/main.py`](../extensions/services/dashboard-api/main.py)
Acceptance:
- Feature tiles derive from API metadata.
- Ports/URLs are not hardcoded in JSX.
Progress notes:
- `dashboard/src/pages/Dashboard.jsx` now fetches `/api/features` and renders feature cards from backend metadata.
- Feature card links are now resolved from live service metadata (`external_port`) instead of hardcoded port literals in JSX.

## Workstream W6: Workflow Composability

Status: `DONE`

Milestone W6-M1 (PR-10): Unify workflow directory + catalog contract  
Status: `DONE`  
Owner: API + Docs  
Effort: 1-2 days  
Files:
- `config/n8n/catalog.json` (planned; not yet created)
- [`dashboard-api/main.py`](../extensions/services/dashboard-api/main.py)
- [INTEGRATION-GUIDE.md](INTEGRATION-GUIDE.md)
Acceptance:
- One canonical workflow path in code/docs.
- Catalog supports both templates and metadata cleanly.
Progress notes:
- `dashboard-api/main.py` now resolves workflows from canonical `config/n8n` with legacy `workflows/` fallback.
- Workflow catalog loading now validates structure and returns normalized fallback data on malformed input.
- `docs/INTEGRATION-GUIDE.md` updated to reference `config/n8n/*.json` and `config/n8n/catalog.json`.

## Workstream W7: CI and Quality Gates

Status: `DONE`

Milestone W7-M1 (PR-11): Add CI workflows for shell, compose, frontend, API lint/tests  
Status: `DONE`  
Owner: QA/Infra  
Effort: 2-3 days  
Files:
- `.github/workflows/lint-shell.yml` (new)
- `.github/workflows/test-linux.yml` (new)
- `.github/workflows/dashboard.yml` (new)
- [`tests/integration-test.sh`](../tests/integration-test.sh)
- [`tests/test-phase-c-p1.sh`](../tests/test-phase-c-p1.sh)
Acceptance:
- PRs fail on syntax/lint regressions.
- Integration smoke suite runs in CI where possible.
Progress notes:
- Added `.github/workflows/lint-shell.yml` with repository-wide shell syntax checks (`bash -n` on `*.sh`).
- Added `.github/workflows/test-linux.yml` to run `tests/integration-test.sh` and `tests/test-phase-c-p1.sh`.
- Added `.github/workflows/dashboard.yml` for frontend lint/build and dashboard API Python syntax checks.

Milestone W7-M2 (PR-12): Platform matrix smoke tests  
Status: `DONE`  
Owner: QA/Infra  
Effort: 3-5 days  
Files:
- `.github/workflows/matrix-smoke.yml` (new)
- `tests/smoke/` (new)
Acceptance:
- Matrix includes Linux AMD path checks, NVIDIA checks, and WSL logic tests.
- macOS path at least verifies installer dispatch and docs correctness.
Progress notes:
- Added `.github/workflows/matrix-smoke.yml` with Linux and macOS jobs.
- Added `tests/smoke/linux-amd.sh`, `tests/smoke/linux-nvidia.sh`, `tests/smoke/wsl-logic.sh`, and `tests/smoke/macos-dispatch.sh`.
- Local smoke runs pass and validate installer dispatch/support-matrix contracts for AMD/NVIDIA/WSL/macOS.

## Workstream W8: Contributor Experience

Status: `DONE`

Milestone W8-M1 (PR-13): Add extension authoring guide and templates  
Status: `DONE`  
Owner: Docs + Core  
Effort: 2 days  
Files:
- `docs/EXTENSIONS.md` (new)
- `extensions/templates/service-template.yaml` (new)
- `extensions/templates/dashboard-plugin-template.js` (new)
- [`CONTRIBUTING.md`](../CONTRIBUTING.md)
Acceptance:
- “Add a service in 30 minutes” path works end-to-end.
- Guide includes test and compatibility checklist.
Progress notes:
- Added `docs/EXTENSIONS.md` with a concrete 30-minute extension authoring flow.
- Added `extensions/templates/service-template.yaml` and `extensions/templates/dashboard-plugin-template.js`.
- Updated `CONTRIBUTING.md` to point contributors to extension workflow + validation checklist.

## Workstream W9: Release Engineering

Status: `IN_PROGRESS`

Milestone W9-M1 (PR-14): Versioned release manifest + compatibility checks  
Status: `IN_PROGRESS`  
Owner: Release  
Effort: 2-3 days  
Files:
- `manifest.json` (new)
- [`dashboard-api/main.py`](../extensions/services/dashboard-api/main.py)
- [`ods-update.sh`](../ods-update.sh)
Acceptance:
- Update path validates version compatibility and rollback point.
- Dashboard displays current/available release and update readiness.
Progress notes:
- Added `manifest.json` with versioned release and compatibility contracts.
- Added `scripts/check-compatibility.sh` to validate manifest contract paths and support-matrix alignment.
- Integrated compatibility checks into CI via `.github/workflows/test-linux.yml`.
- Added `scripts/ods-doctor.sh` and `docs/ODS-DOCTOR.md` for machine-readable readiness diagnostics (capability + preflight + runtime snapshot).

## 30/60/90 Sequencing

Day 0-30:
- PR-1, PR-2, PR-3, PR-4, PR-5

Day 31-60:
- PR-6, PR-7, PR-8, PR-10

Day 61-90:
- PR-9, PR-11, PR-12, PR-13, PR-14

## Critical Dependencies

1. W1 must complete before W7 matrix tests can be trusted.
2. W4 manifest contract must stabilize before W5 plugin registry.
3. W3 compose contract must stabilize before docs freeze and release hardening.

## Launch Gates

Gate A (Day 30):
- Installer dispatch merged.
- Support matrix published.
- Compose contract direction finalized.

Gate B (Day 60):
- Manifest + env schema in production path.
- Dashboard registry merged.

Gate C (Day 90):
- CI matrix active.
- Extension authoring guide validated by a sample external contribution.
- Release manifest + rollback flow validated.
