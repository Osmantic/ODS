# Cafe-llama.cpp in ODS: parameter catalog and roadmap

**Status:** research-backed integration plan; this document does not claim that all controls are already wired into ODS.

## Upstream findings

- Repository: [quimmedes/cafe-llama.cpp](https://github.com/quimmedes/cafe-llama.cpp), an experimental feature-rich fork of llama.cpp, MIT licensed.
- The repository's default branch is `master`; the current release observed during this review is [0.75](https://github.com/quimmedes/cafe-llama.cpp/releases/tag/0.75), published 2026-10-08. The source advanced on 2026-10-07.
- GitHub has no enabled issue tracker for this repository. There is no formal, canonical roadmap to quote. The practical roadmap below is inferred from README/source, release contents, and visible feature branches such as `feat/deepseek-v4.1`, `feat/metal-kv-cache-turbo`, and `freetoken-offload`. Branch names are signals of work, not promises that a feature ships in release 0.75.
- The reviewed README documents MoE placement to pinned host RAM, CPU RAM, or SSD; TurboQuant KV types; PLE/N-gram SSD placement; MTP speculative decoding; direct safetensors loading; and backend-specific behavior. The actual binary's `--help` and a running build probe remain the final source of truth.

## Feature roadmap for ODS

| Track | Runtime vocabulary to model | Hardware/model constraints | ODS readiness |
|---|---|---|---|
| Core placement | GPU layers, CPU layers, context, batch/ubatch, threads, device/split mode | VRAM/RAM budget, device count, architecture | Parameter catalog; runtime profile and activation still required |
| KV memory | K and V types independently; unified KV; KV offload; TurboQuant turbo2/3/4 | Turbo KV is GPU-side and requires Flash Attention; turbo2/3 have head-dimension constraints | Catalogued; per-build probing and argument translation required |
| MoE placement | GPU, pinned host RAM, CPU RAM, SSD mmap; first-N-layer variants | Host RAM, CUDA host-memory path, PCIe, storage latency/IOPS; model must be MoE | Catalogued; benchmark per model/hardware |
| PLE / N-gram | load/disable N-gram table, SSD offload | Only applies to checkpoints that contain the internal PLE/N-gram table | Catalogued; model metadata gate required |
| Speculative decoding | MTP, N-gram draft, draft model path, draft GPU layers, K/V draft cache, max/min tokens, acceptance threshold, draft sampling/threads, draft MoE placement | Exact model architecture, draft checkpoint compatibility, free VRAM and CPU/RAM | Catalogued; exact mode must be advertised by the running build |
| Checkpoint loading | GGUF, safetensors, safetensors outtype, native FP8 path | Model family, tensor layout, quantization and backend support | Catalogued; loader support is not universal |
| Kernel/backend | baseline, tensor buffer overrides, build-specific PTQ1-mmV, CUDA/HIP/Vulkan/Metal/CPU and device selection | Compiled backend, GPU architecture, tensor shape, model family | Catalogued; build-specific features require probe |
| Context scaling | RoPE scaling and YaRN fields | Model metadata and training context | Catalogued; do not auto-tune without workload-specific validation |
| Multimodal | projector path/URL, projector offload/device, image/video controls | Model and build support | Catalogued; API capability must match model |
| Memory fitting | auto-fit, fit target, fit context, mmap/mlock | OS, memory pressure, memory estimator accuracy | Catalogued; persist resolved values, not just requested values |
| Provenance | release/tag, asset SHA-256, architecture/backend, build identity | Installed artifact and running process must agree | Required before activation |

## Parameter contract

The registry lives in `extensions/services/dashboard-api/cafe_llama_parameters.py`. It records:
- canonical ODS parameter key and category;
- CLI flag aliases from the source reviewed;
- value shape and documented environment name where the source explicitly defines one;
- readiness (documented, build-dependent, or integration-required);
- notes about model/backend/hardware constraints.

**Do not invent environment variables.** For CLI-only parameters, ODS must build a validated argument vector or a supported runtime config, with shell-safe argument handling; do not concatenate user-provided values into a shell command. Keep K and V cache settings independent internally even if a simple UI later links them.

## Discovery vs execution

The catalog is broad; ICD's default automatic sweep is intentionally small to avoid a combinatorial explosion. The selector should:
1. identify the exact artifact/build and backend;
2. query `--help` (and a machine-readable capability endpoint if added) to discover accepted flags and enumerated values;
3. intersect source catalog, binary capabilities, model architecture, and hardware;
4. produce a bounded set of deterministic candidate configurations;
5. mark candidates `measurement_required=true` and `execution_authorized=false`;
6. only activate through ODS's existing lifecycle path after explicit user selection;
7. record requested and effective/resolved arguments, runtime identity, model hash, workload, hardware, and measurements.

Unknown flags, values, or environment mappings must fail closed. “Catalogued” means the UI and ICD know the parameter, not that the current build supports it.

## Suggested delivery order

1. **Parameter inventory and tests** — registry and capability vocabulary (this change).
2. **Runtime introspection** — obtain and parse the pinned binary's help/version/build metadata; cache by artifact digest.
3. **Argument renderer** — allowlisted typed mapping from ODS configuration to argument vector, with unit tests for quoting, duplicates, invalid ranges, and conflicting settings.
4. **Artifact profile** — pin release asset SHA-256, OS/architecture/backend, and prove that the running process uses that artifact.
5. **Host-agent integration** — opt-in selector; unchanged `llama-server` default; transactional stage → verify identity → real completion → publish route.
6. **Dashboard** — parameter groups and conditional controls driven by runtime/model/hardware capability intersection; distinguish requested from effective values.
7. **Benchmark and rollback** — test on target hardware, persist measurements by configuration/workload, and verify rollback to the previous runtime.
8. **Expansion** — enable Turbo KV, MoE/SSD placement, PLE/N-gram, MTP, safetensors-native, PTQ1-mmV, and backend-specific controls individually when the binary probe and measured test matrix justify them.

## Local Ubuntu work to reconcile

The current chat's historical notes indicate existing local worktrees:
- `~/ods-git` — ODS checkout;
- `~/ods-cafe-pr` — isolated ICD PR worktree based on upstream main; earlier note records commit `9606de0063cb9a263e744b2d56db99edd5a87cfc` and six focused tests passing at that time;
- `~/leones-cafe-staging` — LEONES staging.

Historical notes also indicate the LEONES staging contains `cafe_adapter.py`, host activation overlays, runtime selector, ICD modules, dashboard panel, tests and smoke/evidence scripts. These are prior conversation records, not a fresh scan of the machine. This assistant session has no direct shell/SSH access to the user's Ubuntu host, so it cannot honestly claim to have searched current uncommitted files, binaries, or logs there. The local reconciliation should compare these worktrees and search the installed Cafe binary's `--help` before merging the parameter registry.
