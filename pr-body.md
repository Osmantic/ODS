## Summary

A plain `./install.sh` rerun (e.g. an update) on an external-Lemonade install silently destroyed the selection and repointed LiteLLM at a managed container that does not exist.

`install-core.sh` defaults `LEMONADE_EXTERNAL` to `false`, and phase 06 reads it straight from the shell (`LEMONADE_EXTERNAL_VALUE="${LEMONADE_EXTERNAL:-false}"`) — unlike every sibling key, it never looks at the existing `.env`. On a rerun with no flags the whole external block was skipped, so the regenerated `.env` wrote:

- `LEMONADE_EXTERNAL=false`, `LEMONADE_BASE_URL=`, `LEMONADE_CONTAINER_BASE_URL=`, `LEMONADE_MODEL=`
- every `AMD_INFERENCE_*` marker emptied
- `LEMONADE_CONTAINER_API_BASE` falling back to `http://llama-server:8080/api/v1`, so the rendered `lemonade.yaml` routed to a managed container the install does not run → silent 404s after update

## Fix

- `install-core.sh` now tracks `LEMONADE_EXTERNAL_EXPLICIT` (same idiom as `ODS_MODE_EXPLICIT`/`BIND_ADDRESS_EXPLICIT`) and recovers the marker from the existing `.env` via `ods_preserve_lemonade_external` right after mode preservation. An explicit env value — including a deliberate `false` — or a `--use-existing-lemonade`/`--lemonade-url` flag always wins.
- `installers/lib/install-mode.sh` extracts the fail-closed literal-dotenv reader shared by `ODS_MODE` into `_ods_existing_env_literal` and adds `ods_existing_lemonade_external` (accepts only `true`/`false`; rejects duplicates, malformed values, symlinks, group/world-writable or foreign-owned files) plus `ods_preserve_lemonade_external`.
- Phase 06 completes the restore for the three remaining external-only keys by reading `AMD_INFERENCE_PORT`, `AMD_INFERENCE_BACKEND`, and `AMD_INFERENCE_SUPPORTED_BACKENDS` through `_env_get_explicit_first`, matching the existing `LEMONADE_BASE_URL`/`LEMONADE_MODEL` contract.

With the marker restored, the existing `_env_get_explicit_first` machinery repopulates `LEMONADE_BASE_URL`, `LEMONADE_CONTAINER_BASE_URL`, `LEMONADE_MODEL`, `LITELLM_LEMONADE_API_KEY`, and the AMD inference markers unchanged.

## Tests

- `tests/test-installer-mode-preservation.sh` — 7 new behavioral cases: implicit rerun preserves `true`, flag/env choice wins, explicit `false` disables, persisted `false` stays managed, malformed/duplicate/missing markers fail closed. All pass.
- `tests/contracts/test-external-lemonade-contracts.sh` — new wiring contract asserting `LEMONADE_EXTERNAL_EXPLICIT`, the preserve call, and the three `_env_get_explicit_first` reads. All pass.
- `tests/test_installer_mode_dotenv.py` — 12 passed (refactored reader is behavior-identical).
- Also ran: `test-simulate-installers-exit-codes.sh`, `test-bootstrap-upgrade-compose-flags-recovery.sh`, `test-linux-cloud-mode.sh`, `test-resolve-compose-resilient.sh` (36 passed), `test-macos-installer-transitions.sh` — all green. `bash -n` + scoped shellcheck clean (only pre-existing SC2034 class).

## Notes

- macOS/Windows installers are out of scope; external Lemonade is a Linux path.
- `--lemonade-api-key` still requires `--use-existing-lemonade`/`--lemonade-url` to activate external mode; that flag semantics question is handled separately.
