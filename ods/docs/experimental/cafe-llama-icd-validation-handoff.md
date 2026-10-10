# Cafe ICD PR: validation and Ubuntu handoff

This checklist is specific to PR [Osmantic/ODS #7500](https://github.com/Osmantic/ODS/pull/7500). It separates source-level validation from proof of a running Cafe runtime.

## Current source changes

- The PR adds the declarative inference-configuration schema, Cafe parameter inventory, broad capability vocabulary, bounded discovery dimensions, and tests that keep estimated candidates distinct from measured evidence.
- Cafe configuration validation now gates populated discovery dimensions against the probed runtime capability list when one is supplied.
- Turbo KV requires `flash_attention=true`; omission is not treated as evidence that the prerequisite is enabled.
- A pytest-discoverable regression file covers unsupported Flash Attention, Turbo KV prerequisites, and strict boolean validation.
- The parameter catalog is not an argument renderer and does not activate Cafe. Runtime activation, artifact identity, real completion and rollback remain outside this declarative ICD slice.

## GitHub status observed on 2026-10-10

- PR #7500 is open against `main`; GitHub reports it mergeable, but that is not test evidence.
- The latest PR-triggered workflow runs were returned as `action_required`, with no individual commit statuses reported. Do not describe CI as passing until the repository's required workflows have actually run and their results are green.
- The GitHub connector could not post a PR comment or replace the PR description because the API returned HTTP 403 (resource not accessible by integration).

## Focused validation to run in Ubuntu

From the ODS source root on the PR branch, first inspect status and diff. Do not run commands from the installed `~/ods` directory and do not change its `.env` or running services.

```bash
git status --short
git diff --check
pytest -q tests/test-inference-configuration-discovery.py tests/test_cafe_llama_capability_gates.py
```

If the repository's test harness requires the dashboard API directory on `PYTHONPATH`, use the same environment/path setup as the existing focused ICD tests. Record the exact command and output; do not infer a pass from collection alone.

Then run the focused model-selection regression tests already used by this checkout. Finally inspect the PR's GitHub Actions page and make sure the required workflows are actually approved/executed and green.

## Host runtime work that still needs local evidence

The separate LEONES staging implementation must be reconciled before claiming Cafe activation support. In particular:

1. Review the uncommitted LEONES overlays, `adapters.py`, and `reconciler.py`; preserve all unrelated local changes.
2. Fix the duplicate `cleanup()`/trap in `build_and_smoke_cafe_runtime.sh`. On failure, diagnostic output must refer to a container that was actually created and to the retained temporary directory.
3. Keep the server smoke test bounded but avoid making an exact generated sentence the only runtime-health signal. Report health, model discovery, non-empty completion, and optional exact-prompt assertion separately.
4. Exercise explicit opt-in, runtime/build identity and artifact provenance, a real completion, route publication only after verification, and rollback after a deliberately injected failure.
5. Confirm the default `llama-server` activation path is unchanged.

No throughput or hardware-support claim should be made until a real target-host benchmark captures model/artifact identity, configuration ID, workload, hardware, and measured results.
