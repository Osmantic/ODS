# macOS bootstrap model promotion

Fast-start installs initially serve the bootstrap model (currently Qwen 3.5 2B)
while downloading the selected full model. With native Pixel configured, the
background worker delegates promotion to the authenticated host agent. That
transaction owns admission, inference changes, Pixel configuration, downstream
routes and rollback. The worker does not independently restart inference.

Promotion is complete only after the activation receipt names the requested
model and context, Pixel is reconciled, the saved environment still selects a
native local runtime, the status reports the same local model and context, and
no model transaction is pending. Only then may the worker remove the bootstrap
model. A rejected or ambiguous activation keeps existing model files and does
not fall through to the legacy restart or retry the activation request.

## An installation that already has a pending switch

This change prevents future uncoordinated promotion. It is **not a reset or
migration of an existing interrupted switch**. In particular, these records do
not establish a completed Portal transition:

```json
{
  "model_switch": {
    "phase": "held",
    "previous": {"model": "Qwen3.5-2B-Q4_K_M.gguf", "contextLength": 65536},
    "target": null
  },
  "bootstrap": {"status": "complete", "model": "Qwen3.5-9B-Q4_K_M.gguf"}
}
```

The bootstrap status records the worker's result, not the protected
controller's model contract. `target: null` means the host has not recorded a
Pixel apply target; inference settings may already have changed. A held switch
is not evidence that nothing changed.

Preserve `data/pixel-model-transaction.json`, `data/pixel-native`, the protected
receipts, configuration and remaining model files. Do not edit the journal,
clear admission holds, or run a forced reinstall to suppress the warning.
The promotion client reports `model-switch-recovery-required` before sending
any activation request in this state.

Portal's **Recover model switch** verifies the existing transaction. If it
still reports that repair is required, repeated clicks or rerunning the
bootstrap worker do not supply the missing proof. Maintainer investigation
needs the original activation/rollback error from
`~/Library/Logs/ODS/ods-host-agent.log`, the controller's current transaction,
the actual inference identity/context and the configuration evidence. Review
and sanitize logs before sharing; do not publish `.env` or private receipts.

See [model transaction recovery](PORTAL-MODEL-TRANSITIONS.md). The guarded
release option applies only to a transaction proven to have changed nothing;
`target: null` alone does not establish eligibility for that option.

## Automated validation

From the repository root:

```bash
python -m pytest -q ods/tests/test_macos_bootstrap_promote.py ods/tests/test_macos_bootstrap_dispatch.py
bash ods/tests/test-macos-bootstrap-resume.sh
python -m pytest -q ods/extensions/services/dashboard-api/tests/test_model_transaction.py ods/extensions/services/dashboard-api/tests/test_model_recovery_api.py
```

The native bridge workflow runs promotion, HTTP transport, POSIX custody,
shell dispatch and resume fixtures on Ubuntu and macOS. Windows can also run
the client orchestration and real HTTP fixtures: the private file reader is
replaced only in those Windows tests, with POSIX custody tests explicitly
skipped. For shell dispatch on Windows, set `ODS_TEST_BASH` to Git Bash's
executable, rather than the Windows WSL launcher.

These tests do not run Metal inference or Docker Desktop. Physical acceptance
requires a fresh isolated Apple Silicon install: verify automatic 2B-to-full
promotion, matching protected Pixel and inference contracts, no pending
transaction, a real Portal message/tool task, and operation after a Mac
restart. An older successful hardware run is useful evidence but does not
qualify a later integrated commit.
