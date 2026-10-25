# Explicit cache precision for registered native models

Registered native profiles retain their existing `q4_0` K/V cache by default.
Omitting `cacheType` and setting `"cacheType": "q4_0"` mean the same thing.
This change does not change the bundled runtime, reasoning flags, existing
MTP profiles, or measured memory qualification.

An owner can author an explicit non-MTP `f16` profile for CPU-only use. This
first implementation supports dense transformer layouts recognized by
`model_preflight.memory_fields`, with complete GGUF memory metadata. It
requires `N_GPU_LAYERS=0`, no vision projector, and fresh RAM admission before
host activation or native start/restart. GPU offload, Metal, hybrid/recurrent
layouts, f16 MTP, and measured f16 qualification remain unsupported.

Author the profile using existing local artifacts (no download or inference):

```powershell
python scripts/qualify-mtp.py --baseline-only --cache-type f16 `
  --runtime 'C:/runtimes/llama-server.exe' --model 'D:/models/model.gguf' `
  --expected-sha256 '<publisher-model-sha256>' --hardware-id '<this-CPU>' `
  --context 4096 --output baseline-f16.json
python scripts/register-model-store.py --install-dir 'C:/ODS' `
  --id cpu-models --directory 'D:/models' --backend vulkan `
  --qualified-profile baseline-f16.json
```

Choose the backend of the actual executable, including `vulkan` for a Vulkan
executable run with zero GPU layers. The installed `.env` must already select
`N_GPU_LAYERS=0` before activating this profile. Registration does not select
a model or change that setting. Recreate the dashboard API with the generated
read-only model-store overlay as usual.

Schema-2 baseline authoring hashes the model and runtime and binds the exact
baseline argv, context, symmetric cache precision, and zero GPU layers. It
parses argv using `--help`; it does not establish generation quality, inference
performance, or measured memory fit. The registrar validates these bindings
again and preserves `cacheType` in the registered row. Schema-1 MTP authoring
and its existing signatures retain their q4 behavior.

Any retained `memoryQualification` record is incompatible with f16. The
runtime refuses it, the dashboard withholds verified native availability,
and baseline registration refuses to overwrite a retained record. Do not
delete a measured record to force admission; retain that store/profile with
its qualified artifacts and q4 configuration. A new baseline profile is an
estimate, not a replacement measured claim.

The admission gate uses the existing architecture estimator at the requested
context and f16 precision. Its CPU budget is the smaller of freshly available
RAM and the shared selector budget `min(max(total RAM * 0.35, 3 GiB), 8 GiB)`.
It gives no credit for memory that stopping the current model might release.
Unknown observations, incomplete layouts, an excessive context, and an
insufficient budget refuse before changing model configuration or stopping
the current native process. Metadata and the original artifact hashes are
still verified. An estimate is not a reservation: other processes may consume
RAM after admission, and the existing launch/rollback handling remains needed.

Tests cover the actual Python resolver, host activation up to its first write,
and the native PowerShell start/restart functions. Service/process effects and
runtime `--help` are replaced by inert test boundaries; Windows RAM observation
is real. No real model generation or installed ODS activation is claimed by
these tests. Live CPU qualification still requires a separately reviewed run
with the chosen model, runtime, reasoning fix, and complete production CLI.
