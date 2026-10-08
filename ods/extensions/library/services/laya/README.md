# Laya for Portal

Enable Laya from ODS Extensions. The selected Portal model gains an optional
`pixel_ods_laya` tool for text classification, ordinal scores and yes/no
probabilities, plus `pixel_ods_laya_batch` for workspace datasets. It uses
decisions to continue your task and answer normally.
There is no separate chat UI, provider key or model-selection step.

Requires the ODS core Portal Laya integration, Docker Compose and the ODS owner's
Node.js runtime. Windows runs the extension through WSL; Linux and macOS use the
same Docker recipe. CPU works on all supported platforms. On NVIDIA hosts,
ODS selects the CUDA overlay; `LAYA_ACCELERATION=auto` uses the GPU only when
enough memory is available. Set it to `cpu` to keep the extension off the GPU,
or `cuda` to require GPU startup instead of allowing CPU fallback. AMD and
Apple hosts currently use CPU.

## Installation and storage

The image pins Laya 0.3.28, its dependency wheels and its Python base image.
The upstream source revision is `a4a8921afebfd852bba0000475cfb6ab737a124c`.
Laya's `reviewed` revision policy selects its reviewed English, multilingual
and typed-decisions checkpoints. First start downloads these models; allow
several minutes and Internet access to the model registry. Subsequent starts
reuse the Docker cache volume. Ordinary decisions run locally.

The container is limited to two CPUs and 8 GiB RAM. It verifies inference with
all three checkpoints before becoming ready. Readiness is evidence that the
models loaded and answered valid test questions, not an accuracy guarantee.

The NVIDIA image uses hash-locked Torch 2.14.0 with CUDA 13.0 wheels and requires
a compatible host NVIDIA driver and Docker GPU integration. Before loading
models, automatic mode keeps 2 GiB of free GPU memory as headroom and limits
Torch's allocator to at most 4 GiB and 25% of the device's total memory. If a
2 GiB allocation budget cannot fit, it uses CPU. This allocator limit does not
cover driver/context memory or reserve memory against other processes.
Only one GPU checkpoint stays resident; it unloads after 60 idle seconds.
Automatic mode retries startup allocation exhaustion once on CPU. Pinned Laya
also supports scoped CPU fallback during inference. Authenticated `/health`
reports actual resident devices and inference fallback counts; a GPU request
alone is not evidence that inference ran there. No main-model settings change.

The dedicated key is under `config/laya/`; Portal's private connection is at
`~/.config/ods/laya-portal.json`. Neither contains a Dashboard or provider key.
Setup preserves the key across restart and re-enable. Only its individual file
is mounted into the container, read-only. The HTTP port binds to localhost;
`LAYA_PORT` defaults to 8017 and can be configured in ODS.

Model downloads live in the Compose-managed `laya-cache` volume. The cache has
separate directories for different owner UIDs, so a recreated WSL user does not
inherit unwritable files. The volume is reusable and contains model files, not
chat history. Disabling the extension preserves this cache. Removing Docker
volumes is a separate destructive operation.

## Behavior

Ask Portal, for example: "Classify tickets.csv by topic, preserve the IDs and
save a CSV report." The batch tool reads an existing CSV/TSV/JSON/JSONL file
through Portal's normal workspace permissions, processes up to 128 rows/64 KiB,
and saves an editable `report.csv` plus original probabilities and source hash
in `decisions.json`. The model can review ambiguous predictions without copying
every row into tool arguments. New reports never overwrite existing files.

Texts already in the conversation use the inline tool. Laya itself cannot read
paths, browse, inspect images or create projects. It does not run on every
message. Long sources must be divided into meaningful sections; truncated
responses are rejected. A service failure returns a tool error and leaves the
Portal model able to continue with its other capabilities. Decisions never
authorize actions.

This fits repeated text classification and scoring, not general chat acceleration.
Compare total task time and label quality for your own workload: loading,
inference and model review can be slower than the chat model alone. Confidence
is an estimate, and saved file hashes do not establish label accuracy. Original
Laya probabilities remain in JSON when the model corrects a CSV label.

Development checks are in `tests/`. `portal_adapter.integration.mjs` requires a
running service and a private `LAYA_TEST_KEY_FILE`; it tests actual inference,
authentication and context handling through the Portal adapter. Platform and
whole-task qualification remain necessary before release.
