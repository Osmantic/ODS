# Container performance tuning

The llama-server container accepts two optional operator settings in `.env`:

| Setting | Supported values | Effect |
| --- | --- | --- |
| `LLAMA_ARG_UBATCH` | Integer 1–512 | Physical prompt-processing batch size (`--ubatch-size`). Smaller batches can reduce temporary memory and can also slow prefill. |
| `LLAMA_ARG_FIT_TARGET` | Integer 512–65536 | Target free-memory margin in MiB per GPU used by automatic fitting (`--fit-target`). One value applies to every GPU. This is not a guaranteed reserve during requests. |

Leave both unset to retain the engine defaults. Empty values are not defaults.
These settings do not change the configured model, context length, KV precision,
or request slots. Automatic fitting can change layer placement; verify the actual
placement and per-slot context in native startup logs and `/props` or `/slots`.

The physical batch upper bound is the current default of 512. ODS's existing
batch-dependent sliding-window memory estimate remains conservative for this
setting; this is not a guarantee of total runtime memory headroom.
These settings apply to Docker launches; this change does not add arguments to
the native macOS or native Windows launchers.

Drain active work, record the original settings, and use the supported ODS
lifecycle to recreate the engine. A normal same-model upgrade preserves valid
values on the same container backend. Reselection or a backend change drops the
retained values. Invalid retained values are discarded without discarding the
active model. Lost tuning is not inferred from a model-state receipt.
This retention also applies to the legacy PowerShell installer for local
NVIDIA and CPU containers. Native Windows AMD inference, cloud mode, and
other engines do not inherit these container settings.

These are operator settings, not per-model catalog recommendations. Ordinary
model activation can retain them: revalidate them after every model change.
Remove the exact two settings and recreate the engine to restore its defaults.

Use the [fleet performance study](fleet-performance-study.md) to compare matched
control and candidate runs. Preserve CPU/RAM limits, source and model hashes,
sampling, context, precision, resource admission rules, and logging settings.
Keep wrong answers, timeouts, refusals and missing requests in the evidence.
Qualify real Portal/tool journeys and upgrade/restart behavior before adopting
a setting. A short synthetic speedup is not broad quality or capacity proof.
