"""Cafe-llama.cpp parameter inventory for ODS.

This is a broad, source-oriented registry, not a claim that every setting is
safe for every build or already wired into ODS. Discover exact CLI support
from the pinned binary's --help and retain provenance for every applied value.
"""
from __future__ import annotations
from typing import Any

CATALOG_SCHEMA_VERSION = "cafe-llama-parameters.v1"
UPSTREAM_REPOSITORY = "https://github.com/quimmedes/cafe-llama.cpp"
UPSTREAM_RELEASE = "0.75"
UPSTREAM_REF = "master"

# readiness: documented = seen in source/README; build-dependent = backend/model
# gated; branch-feature = feature branch rather than guaranteed in release;
# integration-required = needs ODS CLI/lifecycle wiring and runtime proof.
PARAMETERS: dict[str, dict[str, Any]] = {
    # Model, loader and placement
    "model": {"group":"model","flags":["-m","--model"],"value":"path","readiness":"documented","notes":"GGUF or supported safetensors input."},
    "draft_model": {"group":"speculation","flags":["-md","--spec-draft-model"],"value":"path","readiness":"documented","notes":"Separate draft checkpoint; MTP can also be built into supported targets."},
    "gpu_layers": {"group":"placement","flags":["-ngl","--gpu-layers","--n-gpu-layers"],"value":"integer","readiness":"documented","env":"LLAMA_ARG_N_GPU_LAYERS","notes":"Number of model layers offloaded to GPU."},
    "split_mode": {"group":"placement","flags":["-sm","--split-mode"],"value":"none|layer|row|tensor","readiness":"documented","notes":"Multi-GPU placement mode."},
    "tensor_split": {"group":"placement","flags":["-ts","--tensor-split"],"value":"comma-separated floats","readiness":"documented","notes":"Must match available devices."},
    "main_gpu": {"group":"placement","flags":["-mg","--main-gpu"],"value":"device index","readiness":"documented","notes":"Requires a detected device index."},
    "context": {"group":"memory","flags":["-c","--ctx-size"],"value":"tokens","readiness":"documented","env":"LLAMA_ARG_CTX_SIZE","notes":"Bound by model limits and KV memory."},
    "batch": {"group":"throughput","flags":["-b","--batch-size"],"value":"tokens","readiness":"documented","env":"LLAMA_ARG_BATCH","notes":"Logical batch size."},
    "ubatch": {"group":"throughput","flags":["-ub","--ubatch-size"],"value":"tokens","readiness":"documented","env":"LLAMA_ARG_UBATCH","notes":"Physical microbatch; usually <= batch."},
    "parallel_slots": {"group":"serving","flags":["-np","--parallel"],"value":"integer","readiness":"documented","env":"LLAMA_ARG_N_PARALLEL","notes":"Concurrency increases aggregate context/KV memory."},
    "threads": {"group":"cpu","flags":["-t","--threads"],"value":"integer","readiness":"documented","env":"LLAMA_ARG_THREADS","notes":"CPU generation threads."},
    "threads_batch": {"group":"cpu","flags":["-tb","--threads-batch"],"value":"integer","readiness":"documented","env":"LLAMA_ARG_THREADS_BATCH","notes":"CPU prompt/batch threads."},
    "threads_http": {"group":"serving","flags":["--threads-http"],"value":"integer","readiness":"documented","env":"LLAMA_ARG_THREADS_HTTP","notes":"HTTP request worker threads."},
    "flash_attention": {"group":"attention","flags":["-fa","--flash-attn"],"value":"on|off|auto","readiness":"documented","env":"LLAMA_ARG_FLASH_ATTN","notes":"Turbo KV requires compatible flash attention."},
    "cache_type_k": {"group":"kv","flags":["-ctk","--cache-type-k"],"value":"ggml type","readiness":"documented","env":"LLAMA_ARG_CACHE_TYPE_K","notes":"K cache type; backend and kernel dependent."},
    "cache_type_v": {"group":"kv","flags":["-ctv","--cache-type-v"],"value":"ggml type","readiness":"documented","env":"LLAMA_ARG_CACHE_TYPE_V","notes":"V can differ from K; preserve independently."},
    "kv_unified": {"group":"kv","flags":["-kvu","--kv-unified","--no-kv-unified"],"value":"boolean","readiness":"documented","notes":"KV layout/slot behavior depends on server version."},
    "no_kv_offload": {"group":"kv","flags":["-nkvo","--no-kv-offload"],"value":"boolean","readiness":"documented","notes":"Changes KV placement and memory demand."},
    "draft_cache_k": {"group":"speculation","flags":["-ctkd","--spec-draft-type-k"],"value":"ggml type","readiness":"documented","notes":"Draft K cache type."},
    "draft_cache_v": {"group":"speculation","flags":["-ctvd","--spec-draft-type-v"],"value":"ggml type","readiness":"documented","notes":"Draft V cache type."},
    "draft_gpu_layers": {"group":"speculation","flags":["-ngld","--spec-draft-ngl"],"value":"integer","readiness":"documented","notes":"Target and draft GPU placement are independent."},
    "spec_type": {"group":"speculation","flags":["--spec-type"],"value":"comma-separated modes","readiness":"documented","env":"LLAMA_ARG_SPEC_TYPE","notes":"Examples include draft-mtp and ngram-mod; query pinned --help."},
    "spec_draft_n_max": {"group":"speculation","flags":["--spec-draft-n-max"],"value":"integer","readiness":"documented","env":"LLAMA_ARG_SPEC_DRAFT_N_MAX","notes":"Model/workload dependent; measure."},
    "spec_draft_n_min": {"group":"speculation","flags":["--spec-draft-n-min"],"value":"integer","readiness":"documented","notes":"Only with compatible speculation mode."},
    "spec_draft_p_min": {"group":"speculation","flags":["--spec-draft-p-min"],"value":"float","readiness":"documented","notes":"Draft acceptance threshold; measure."},
    "spec_draft_sampling": {"group":"speculation","flags":["--spec-draft-sampling"],"value":"greedy|probabilistic","readiness":"documented","notes":"Must be compatible with target sampling."},
    "draft_threads": {"group":"speculation","flags":["-td","--spec-draft-threads","--threads-draft"],"value":"integer","readiness":"documented","notes":"Separate draft CPU thread budget."},
    # MoE / host memory / storage streaming
    "host_moe": {"group":"moe","flags":["-hmoe","--host-moe"],"value":"boolean","readiness":"documented","notes":"Pinned host RAM/CUDA path; depends on backend and RAM."},
    "n_host_moe": {"group":"moe","flags":["-nhmoe","--n-host-moe"],"value":"layers","readiness":"documented","notes":"Host-MoE for first N layers."},
    "cpu_moe": {"group":"moe","flags":["-cmoe","--cpu-moe"],"value":"boolean","readiness":"documented","notes":"Expert weights in system RAM."},
    "n_cpu_moe": {"group":"moe","flags":["-ncmoe","--n-cpu-moe"],"value":"layers","readiness":"documented","notes":"CPU-MoE for first N layers."},
    "pipeline_parallel": {"group":"moe","flags":["--pipeline-parallel","--no-pipeline-parallel"],"value":"boolean","readiness":"documented","notes":"Overlap scheduler-copied transfers; not universally beneficial."},
    "ssd_streaming": {"group":"ssd","flags":["-ssd","--ssd-streaming","--no-ssd-streaming"],"value":"boolean","readiness":"documented","notes":"Routed expert weights streamed from SSD via mmap."},
    "ssd_n_streaming": {"group":"ssd","flags":["-nssd","--ssd-n-streaming"],"value":"layers","readiness":"documented","notes":"SSD streaming for first N layers."},
    "ssd_cache_experts": {"group":"ssd","flags":["--ssd-streaming-cache-experts"],"value":"integer","readiness":"build-dependent","notes":"Confirm exact range/semantics from binary --help."},
    "ssd_io_threads": {"group":"ssd","flags":["--ssd-io-threads"],"value":"integer","readiness":"documented","env":"LLAMA_ARG_SSD_IO_THREADS","notes":"Tune against storage IOPS and CPU."},
    "ngram_ssd": {"group":"ngram-ple","flags":["--ngram-ssd","--offload-ngram-ssd"],"value":"boolean","readiness":"documented","notes":"Only relevant to checkpoints with internal N-gram/PLE table."},
    "ngram_enabled": {"group":"ngram-ple","flags":["--ngram","--load-ngram","--no-ngram","--disable-ngram","--no-load-ngram"],"value":"boolean","readiness":"documented","notes":"Model-specific PLE/N-gram loading."},
    "draft_host_moe": {"group":"moe","flags":["-hmoed","--spec-draft-host-moe"],"value":"boolean","readiness":"documented","notes":"Host-MoE for draft weights."},
    "draft_cpu_moe": {"group":"moe","flags":["-cmoed","--spec-draft-cpu-moe"],"value":"boolean","readiness":"documented","notes":"CPU-MoE for draft weights."},
    # KV compression / kernel / loader
    "turbo_kv": {"group":"kv","flags":["-ctk turbo2|turbo3|turbo4","-ctv turbo2|turbo3|turbo4"],"value":"turbo2|turbo3|turbo4","readiness":"documented","notes":"TurboQuant GPU-side KV; requires flash attention; turbo2/3 need compatible head dimensions."},
    "tensor_override": {"group":"kernel","flags":["-ot","--override-tensor"],"value":"tensor-pattern=buffer-type","readiness":"documented","notes":"Tensor placement override, not a generic kernel switch."},
    "ptq1_mmv": {"group":"kernel","flags":["build-specific"],"value":"build/model selector","readiness":"build-dependent","notes":"Do not assume generic flag exists; detect exact build and model support."},
    "safetensors_outtype": {"group":"loader","flags":["--safetensors-outtype"],"value":"auto|native|f16|bf16|q8_0|q4_0","readiness":"documented","notes":"Only applies to supported safetensors checkpoints."},
    "safetensors_native": {"group":"loader","flags":["--safetensors-native"],"value":"boolean","readiness":"documented","notes":"Preserves FP8 E4M3 representation where backend supports it."},
    "mmap_mlock": {"group":"loader","flags":["--mmap","--no-mmap","--mlock","--no-mlock"],"value":"boolean","readiness":"documented","notes":"OS and memory/resource limits apply."},
    "fit_memory": {"group":"memory","flags":["--fit","--fit-print","--fit-target","--fit-ctx"],"value":"structured","readiness":"documented","env":"LLAMA_ARG_FIT","notes":"Automatic memory fitting; record resulting effective values for reproducibility."},
    "rope_scaling": {"group":"context","flags":["--rope-scaling","--rope-scale","--rope-freq-base","--rope-freq-scale"],"value":"structured","readiness":"documented","notes":"Model-specific context scaling."},
    "yarn": {"group":"context","flags":["--yarn-orig-ctx","--yarn-ext-factor","--yarn-attn-factor","--yarn-beta-slow","--yarn-beta-fast"],"value":"structured","readiness":"documented","notes":"Model-specific context extension."},
    "numa": {"group":"cpu","flags":["--numa"],"value":"distribute|isolate|numactl","readiness":"documented","env":"LLAMA_ARG_NUMA","notes":"Useful only on applicable NUMA hosts."},
    "multimodal_projector": {"group":"multimodal","flags":["-mm","--mmproj","--mmproj-url","--mmproj-auto","--no-mmproj","--mmproj-offload","--mmproj-device"],"value":"structured","readiness":"documented","notes":"Vision/audio model and build dependent."},
    "image_video": {"group":"multimodal","flags":["--image-min-tokens","--image-max-tokens","--video-fps","--video-timestamp-interval","--video-ffmpeg-dir"],"value":"structured","readiness":"build-dependent","notes":"Confirm exact flags with pinned binary --help."},
    "backend_devices": {"group":"backend","flags":["--device","--list-devices"],"value":"backend/device list","readiness":"documented","notes":"Only compiled ggml backends are available."},
    "runtime_provenance": {"group":"provenance","flags":[],"value":"release+asset SHA256+build id","readiness":"integration-required","notes":"Pin binary, architecture/backend, and verify running build identity."},
}

# Full feature vocabulary for the roadmap and dashboard. This is not blindly
# expanded as a Cartesian product; ICD should choose bounded sweeps per model.
CAPABILITY_VOCABULARY = {
    "kv_cache": ["f16","f32","q8_0","q5_0","q5_1","q4_0","q4_1","q2_k","q3_k","q4_k","q5_k","q6_k","turbo2","turbo3","turbo4"],
    "moe_placement": ["gpu","host-pinned","cpu-ram","ssd-mmap","layer-limited"],
    "speculation": ["none","draft-mtp","ngram-mod","combined-mtp-ngram"],
    "draft_placement": ["gpu-layers","host-moe","cpu-moe"],
    "memory_fit": ["manual","auto-fit","fit-target","fit-context"],
    "checkpoint_input": ["gguf","safetensors","safetensors-native"],
    "multimodal": ["text","vision-mmproj","video"],
    "device_split": ["none","layer","row","tensor"],
    "kernel_tuning": ["baseline","tensor-buffer-overrides","build-specific-ptq1-mmV"],
}

# Only bounded dimensions should be enumerated automatically by ICD.
DISCOVERY_CAPABILITIES = {
    "kernel": ["baseline"],
    "kv_cache": ["f16","q8_0","turbo2","turbo3","turbo4"],
    "flash_attention": [True, False],
    "offload": ["none","host-moe","cpu-moe","ssd"],
    "speculation": ["none","draft-mtp","ngram-mod"],
}

def list_parameters(*, group: str | None = None) -> dict[str, dict[str, Any]]:
    return {key: dict(value) for key, value in PARAMETERS.items()
            if group is None or value.get("group") == group}

def parameters_by_readiness() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for key, value in PARAMETERS.items():
        result.setdefault(str(value.get("readiness", "unknown")), []).append(key)
    return result
