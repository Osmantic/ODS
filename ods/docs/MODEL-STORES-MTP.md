# Additional model stores and MTP

ODS can keep completed GGUF files on an additional disk without moving its default `data/models` directory. The owner registers an existing directory; the host resolves its native path, and the Dashboard API reads the same files through a read-only container mount. Model selection still uses the normal activation transaction and its readiness checks.

## Register a disk

Run from the ODS installation directory, using the Python command available on the host:

```text
python scripts/register-model-store.py --install-dir /path/to/ods --id models-ssd --directory /path/to/models
```

On Windows, use native paths such as `C:/Users/owner/ods` and `D:/Models`. On Linux and macOS, use the disk's mounted absolute path. Docker Desktop must have access to the directory. The command writes `data/model-stores.json` and `.model-stores.compose.json`; it does not move files, switch the active model, or restart services. Recreate the Dashboard API using the installation's Compose stack with the generated overlay. The normal ODS stack resolver includes that overlay on subsequent runs.

Unplugged disks are unavailable until remounted. Ambiguous GGUF basenames across stores are excluded rather than silently selecting a different file. Companion `mmproj` and `MTP-` artifacts are not standalone chat models.

## Qualify MTP for one model and runtime

MTP requires both a compatible checkpoint and runtime. A model's parameter count or the amount of VRAM alone cannot establish that it will run faster. The qualifier checks the GGUF metadata, the publisher's SHA-256, and the selected executable's advertised MTP support:

```text
python scripts/qualify-mtp.py --runtime /path/to/llama-server --model /path/to/model.gguf --expected-sha256 PUBLISHER_SHA256 --hardware-id ACCELERATOR_ID --context 16384 --draft-tokens 2 --output qualified.json
```

The command inspects files and runtime capabilities, then parses the complete baseline and MTP argument lists with `--help` last. It does not load the model or start an inference server. Choose a runtime backend that actually works on the host, such as Metal, CUDA, Vulkan, ROCm, or CPU, and verify the requested context under realistic memory conditions before serving traffic. Activation and verified cold starts repeat the argument preflight before stopping an existing runtime.

For a Lemonade multimodal model, add `--launch-mode lemonade --vision-projector /path/to/mmproj.gguf`. This signs the actual full GPU layer count and projector hash alongside the model, executable, context, and cache settings. A standalone text-only test does not qualify the memory requirements of that deployment.

Runtime and router compatibility must be checked together. Lemonade 10 adds `--no-mmap` when it detects an integrated GPU, even when the chosen accelerator is discrete. Its custom-argument handling recognizes the legacy `--mmap`/`--no-mmap` pair. llama.cpp b10874 supports that pair and native MTP; b10875 removed the legacy flags, so an otherwise MTP-capable newer executable cannot be substituted into this Lemonade 10 path without updating the router contract. The qualifier checks these capabilities instead of assuming that a newer version is compatible. Loading flags are recorded in the qualified profile rather than added globally. See the [Lemonade 10 command builder](https://github.com/lemonade-sdk/lemonade/blob/v10.0.0/src/cpp/server/backends/llamacpp_server.cpp) and [llama.cpp change log](https://github.com/ggml-org/llama.cpp/issues/9289).

An explicit local profile can then be registered for Lemonade:

```text
python scripts/register-model-store.py --install-dir /path/to/ods --id models-ssd --directory /path/to/models --qualified-profile qualified.json --backend vulkan --enable-mtp
```

This makes MTP an option for the qualified model, not a global flag applied to every checkpoint. The profile records its runtime and model hashes, context size, and draft-token limit. Normal model activation is still required.

When the generic VRAM estimate cannot establish fit, the chat selector requires measured evidence for that exact native profile. Register it with `--benchmark-evidence /path/to/evidence.json` after a completed isolated baseline/MTP comparison with the projector loaded. The evidence must match the GPU, memory capacity, model, runtime and launch settings. It does not grant availability on another computer or override the activation transaction's checks.

This availability proves a successful native load and the measured workload, not that every weight resides in dedicated VRAM. Windows and GPU drivers can execute a model using both dedicated memory and shared system RAM, with a substantial performance cost. `activationSupport` therefore remains separate from the generic `fitsVram` result; a verified profile can be available while `fitsVram` remains false. Keep per-process dedicated/shared-memory observations with the benchmark when diagnosing such a deployment. A projector being loaded does not prove that image inference was tested.

Availability currently requires the supported host-managed Windows Lemonade route, the same reported GPU identity/backend, sufficient GPU and system-memory capacity, and the registered artifact/context qualification. Model, executable and projector hashes are rechecked before activation. A changed context cannot reuse an existing memory qualification: activation and cold restart reject the mismatch. Existing default stores and unqualified profiles continue through their existing capacity checks.

## Measure before recommending

For a performance comparison, use the same model, executable, hardware, context, prompts, and cache settings. Avoid a second loaded model competing for accelerator memory. Collect at least three valid baseline samples and three valid MTP samples, each generating at least 128 tokens; record the accepted draft-token count for MTP.

The qualifier accepts `--benchmark-evidence evidence.json`. Evidence must contain the generated profile's exact `signature`, plus `baseline` and `mtp` arrays. Each sample has `valid: true`, `tokens`, and `milliseconds`; MTP samples also need a positive `acceptedDraftTokens`. A median improvement of at least 5% yields an MTP recommendation. Missing, mismatched, or incomplete evidence remains `benchmark-required`; a measured slowdown recommends the baseline.

Successful draft-token acceptance proves that MTP is operating. It does not by itself prove a speedup, better reasoning, or identical performance on another computer.
