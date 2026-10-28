"""vLLM catalog entries: schema, model folder and GPU fit.

Entries in config/model-library.json with ``"runtime": "vllm"`` are served by
the vLLM engine (``.env`` ``LLM_BACKEND=vllm``), not llama.cpp. They carry no
``gguf_*`` fields; each one pins a whole Hugging Face snapshot instead:

* ``hf_repo`` and ``revision`` (the full 40-hex commit);
* ``files``: every file at that commit, ``{path, size, sha256}`` for an LFS
  file and ``{path, size, git_blob_sha1}`` for a small file git stores itself
  (the blob id Hugging Face reports: sha1 of ``"blob <size>\\0"`` + content);
* ``served_model_name`` (the id vLLM serves, ``.env`` ``VLLM_SERVED_MODEL``),
  ``context_length`` (equal to ``--max-model-len``) and ``vllm_args``;
* ``min_gpu_mib`` and ``compute_capability``: the one GPU vLLM runs on needs
  at least this much memory (nvidia-smi ``memory.total``) and one of these
  compute capabilities. When a GPU's free memory is known (a fresh install
  passes nvidia-smi ``memory.free``), it fits only if vLLM's share of its
  total memory is free, plus START_MARGIN_MIB for the CUDA context vLLM
  creates before it checks: vLLM refuses to start with less;
* optional ``gpu_memory_utilization``: the share of that GPU vLLM
  preallocates (``.env`` VLLM_GPU_MEMORY_UTILIZATION, the vllm-server
  service's ``--gpu-memory-utilization``; vLLM's 0.90 when absent). An entry
  for a GPU that is often a workstation's only one (RTX PRO 6000 class) takes
  less, so ComfyUI and voice still fit beside it;
* ``reasoning`` (the thinking default), plus ``tool_call_parser`` and
  ``reasoning_parser``, which name the same parsers as ``vllm_args``;
* ``thinking_template_kwargs``: for each ODS thinking level (off, low,
  medium, high) the ``chat_template_kwargs`` that select it in this model's
  own chat template. Templates disagree (GLM-5.3 reads only
  ``reasoning_effort`` low or high and thinks at "max" for any other value;
  Qwen3.8 turns thinking off with ``enable_thinking`` and fails on an
  unknown ``reasoning_effort``), so a level is never passed through
  literally: model-router applies this map on the vllm route;
* ``selection``: 0 for every llama.cpp memory class, so no llama.cpp install
  ever picks the entry, and a ``vllm`` priority for ``--llm-runtime vllm``.

JSON-valued vLLM options go in ``vllm_args`` as vLLM's dotted flags, so each
argument stays a plain token: ``--speculative-config.method=mtp
--speculative-config.num_speculative_tokens=3`` parses to the same config as
``--speculative-config '{"method":"mtp","num_speculative_tokens":3}'``.

scripts/select-model.py (``--llm-runtime vllm``), scripts/download-hf-manifest.py
and the catalog tests share these rules. Standard library only and no sibling
imports, so a caller can load this file by path, as bin/ods-host-agent.py loads
model_memory.py.
"""

from __future__ import annotations

import math
import re
import shlex
from typing import Any, Iterable, NamedTuple


VLLM_RUNTIME = "vllm"
REASONING_LEVELS = ("off", "low", "medium", "high")
LLAMA_MEMORY_CLASSES = ("discrete", "unified", "cpu")
# The vllm-server compose service passes these itself: --model /model
# --served-model-name ${VLLM_SERVED_MODEL} --gpu-memory-utilization
# ${VLLM_GPU_MEMORY_UTILIZATION} ${VLLM_ARGS}. An entry sets its share with
# ``gpu_memory_utilization``, never in vllm_args, where it would silently
# override the owner's .env value.
COMPOSE_FLAGS = frozenset({"--model", "--served-model-name", "--host", "--port", "--gpu-memory-utilization"})
# The range an entry's gpu_memory_utilization may take.
MIN_MEMORY_SHARE, MAX_MEMORY_SHARE = 0.10, 0.95
# vLLM's own --gpu-memory-utilization default, for an entry that sets none.
DEFAULT_MEMORY_SHARE = 0.90
# vLLM checks free memory after creating its CUDA context, which nvidia-smi
# has not seen yet: a GPU needs this much free beyond vLLM's share.
START_MARGIN_MIB = 1024
# A file downloads under <name><PARTIAL_SUFFIX> until it is verified.
PARTIAL_SUFFIX = ".partial"
GPU_QUERY = "nvidia-smi --query-gpu=uuid,compute_cap,memory.total --format=csv,noheader,nounits"
# The installer's fresh-install query: GPU_QUERY plus each GPU's free memory.
GPU_FREE_QUERY = "nvidia-smi --query-gpu=uuid,compute_cap,memory.total,memory.free --format=csv,noheader,nounits"

_HF_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*")
_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_COMPUTE_CAPABILITY = re.compile(r"[0-9]+\.[0-9]+")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# vllm_args travel through .env VLLM_ARGS and compose's shell-like command
# splitting, so they hold no whitespace, quotes or shell syntax.
_ARG = re.compile(r"[A-Za-z0-9_.:/=,+-]+")
_GPU_UUID = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")
_TEMPLATE_KWARG = re.compile(r"[a-z][a-z0-9_]{0,63}")
_GPU_MEMORY = re.compile(r"([0-9]+)(?:\s*MiB)?")
_FILE_KEYS = ({"path", "size", "sha256"}, {"path", "size", "git_blob_sha1"})
_SELECTION_KEYS = frozenset(LLAMA_MEMORY_CLASSES) | {VLLM_RUNTIME}
_LLAMA_KEYS = frozenset({"llama_server_image", "runtime_profiles", "mmproj_file", "mmproj_url", "mmproj_sha256"})


class Gpu(NamedTuple):
    """One NVIDIA GPU as nvidia-smi reports it."""

    uuid: str
    compute_capability: str
    memory_mib: int
    # nvidia-smi memory.free when the caller passed it; None means unknown.
    free_mib: int | None = None


class Candidate(NamedTuple):
    """One vLLM entry planned onto the GPU it would own."""

    model: dict
    gpu: Gpu
    priority: int
    meets_min_context: bool


def is_vllm_entry(model: Any) -> bool:
    return isinstance(model, dict) and model.get("runtime") == VLLM_RUNTIME


def model_dir_name(model: dict[str, Any]) -> str:
    """The folder under data/models/ (``.env`` VLLM_MODEL_DIR): ``owner--name@<revision[:8]>``."""
    return f"{model['hf_repo'].replace('/', '--')}@{model['revision'][:8]}"


def manifest_bytes(model: dict[str, Any]) -> int:
    return sum(int(item["size"]) for item in model["files"])


def flag_value(args: list[str], flag: str) -> str | None:
    """The value after ``flag`` (or in ``flag=value``), "" without one, None when absent."""
    for index, arg in enumerate(args):
        if arg == flag:
            return args[index + 1] if index + 1 < len(args) else ""
        if arg.startswith(flag + "="):
            return arg.split("=", 1)[1]
    return None


def vllm_args_text(model: dict[str, Any]) -> str:
    """``vllm_args`` as one string for ``.env`` VLLM_ARGS; compose splits it again."""
    return shlex.join(model["vllm_args"])


def selection_priority(model: dict[str, Any]) -> int:
    """The ``selection.vllm`` priority; 0 (or none) means never auto-selected."""
    selection = model.get("selection")
    value = selection.get(VLLM_RUNTIME, 0) if isinstance(selection, dict) else 0
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def path_problem(path: Any) -> str | None:
    """Why ``path`` is unsafe to create under the model folder, or None."""
    if not isinstance(path, str) or not path:
        return "is empty"
    if "\\" in path or "\x00" in path or ":" in path or path.startswith("/"):
        return "must be a relative POSIX path"
    if any(part in {"", ".", ".."} for part in path.split("/")):
        return "may not contain empty, '.' or '..' parts"
    if path.endswith(PARTIAL_SUFFIX):
        return f"may not end in {PARTIAL_SUFFIX}, the download's temporary suffix"
    return None


def _whole(value: Any, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _file_problems(files: Any) -> list[str]:
    if not isinstance(files, list) or not files:
        return ["files must list every file of the pinned revision"]
    problems: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(files):
        where = f"files[{index}]"
        if not isinstance(item, dict) or set(item) not in _FILE_KEYS:
            problems.append(f"{where} must be {{path, size, sha256}} or {{path, size, git_blob_sha1}}")
            continue
        unsafe = path_problem(item["path"])
        if unsafe:
            problems.append(f"{where} path {item['path']!r} {unsafe}")
        elif item["path"] in seen:
            problems.append(f"{where} repeats {item['path']}")
        else:
            seen.add(item["path"])
        if not _whole(item["size"]):
            problems.append(f"{where} size must be a whole number of bytes")
        lfs = "sha256" in item
        digest = item["sha256"] if lfs else item["git_blob_sha1"]
        if not isinstance(digest, str) or not (_HEX64 if lfs else _HEX40).fullmatch(digest):
            problems.append(f"{where} {'sha256' if lfs else 'git_blob_sha1'} must be lowercase hex")
    return problems


def _args_problems(model: dict[str, Any]) -> list[str]:
    args = model.get("vllm_args")
    if not isinstance(args, list) or not args or not all(
        isinstance(arg, str) and _ARG.fullmatch(arg) for arg in args
    ):
        return ["vllm_args must be a list of plain strings (letters, digits and _.:/=,+-)"]
    problems = []
    # vLLM's parser reads --gpu_memory_utilization as --gpu-memory-utilization.
    owned = sorted({arg.split("=", 1)[0].replace("_", "-") for arg in args if arg.startswith("--")} & COMPOSE_FLAGS)
    if owned:
        problems.append(f"vllm_args may not set {', '.join(owned)}: the vllm-server service sets them")
    for flag, key in (("--max-model-len", "context_length"), ("--tool-call-parser", "tool_call_parser"),
                      ("--reasoning-parser", "reasoning_parser")):
        if flag_value(args, flag) != str(model.get(key)):
            problems.append(f"vllm_args {flag} must equal the entry's {key} ({model.get(key)})")
    if "--enable-auto-tool-choice" not in args:
        problems.append("vllm_args needs --enable-auto-tool-choice so the tool-call parser runs")
    return problems


def _selection_problems(selection: Any) -> list[str]:
    if not isinstance(selection, dict) or not set(selection) <= _SELECTION_KEYS:
        return [f"selection may hold only {', '.join(sorted(_SELECTION_KEYS))}"]
    problems = [
        f"selection.{memory_class} must be 0: a llama.cpp install never picks a vLLM entry"
        for memory_class in LLAMA_MEMORY_CLASSES
        if selection.get(memory_class) != 0 or isinstance(selection.get(memory_class), bool)
    ]
    if not _whole(selection.get(VLLM_RUNTIME)):
        problems.append("selection.vllm must be a whole number (0 never auto-selects)")
    return problems


def _identity_problems(model: dict[str, Any]) -> list[str]:
    problems = []
    for key in ("id", "llm_model_name", "served_model_name"):
        if not isinstance(model.get(key), str) or not _NAME.fullmatch(model[key]):
            problems.append(f"{key} must be letters, digits and ._-")
    if not isinstance(model.get("name"), str) or not model["name"].strip():
        problems.append("name must be set")
    llama = sorted(key for key in model if key.startswith("gguf") or key in _LLAMA_KEYS)
    if llama:
        problems.append(f"llama.cpp fields do not apply to a vLLM entry: {', '.join(llama)}")
    if not isinstance(model.get("hf_repo"), str) or not _HF_REPO.fullmatch(model["hf_repo"]):
        problems.append("hf_repo must be <owner>/<name>")
    if not isinstance(model.get("revision"), str) or not _HEX40.fullmatch(model["revision"]):
        problems.append("revision must be the full 40-hex commit")
    return problems


def _serving_problems(model: dict[str, Any]) -> list[str]:
    problems = []
    if not _whole(model.get("context_length"), 1):
        problems.append("context_length must be a positive whole number")
    elif "max_context_length" in model and not _whole(model["max_context_length"], model["context_length"]):
        problems.append("max_context_length (config.json max_position_embeddings) must be at least context_length")
    if not _whole(model.get("min_gpu_mib"), 1):
        problems.append("min_gpu_mib must be a positive whole number of MiB")
    share = model.get("gpu_memory_utilization", MAX_MEMORY_SHARE)
    if (not isinstance(share, (int, float)) or isinstance(share, bool)
            or not MIN_MEMORY_SHARE <= share <= MAX_MEMORY_SHARE):
        problems.append(f"gpu_memory_utilization must be a number from {MIN_MEMORY_SHARE} to {MAX_MEMORY_SHARE}")
    capabilities = model.get("compute_capability")
    if (not isinstance(capabilities, list) or not capabilities
            or len(set(map(str, capabilities))) != len(capabilities)
            or not all(isinstance(item, str) and _COMPUTE_CAPABILITY.fullmatch(item) for item in capabilities)):
        problems.append('compute_capability must list distinct "major.minor" strings')
    if model.get("reasoning") not in REASONING_LEVELS:
        problems.append(f"reasoning must be one of {', '.join(REASONING_LEVELS)}")
    for key in ("tool_call_parser", "reasoning_parser"):
        if not isinstance(model.get(key), str) or not _ARG.fullmatch(model[key]):
            problems.append(f"{key} must name a vLLM parser")
    return problems


def _thinking_problems(value: Any) -> list[str]:
    if not isinstance(value, dict) or set(value) != set(REASONING_LEVELS):
        return [f"thinking_template_kwargs must map each of {', '.join(REASONING_LEVELS)} "
                "to chat_template_kwargs"]
    problems = []
    for level in REASONING_LEVELS:
        kwargs = value[level]
        if not isinstance(kwargs, dict) or not all(
            isinstance(key, str) and _TEMPLATE_KWARG.fullmatch(key)
            and (isinstance(item, bool) or (isinstance(item, str) and _NAME.fullmatch(item)))
            for key, item in kwargs.items()
        ):
            problems.append(f"thinking_template_kwargs.{level} must map template argument names "
                            "to booleans or plain words")
    return problems


def thinking_template_kwargs(model: dict[str, Any], level: str) -> dict[str, Any]:
    """The ``chat_template_kwargs`` that select ODS thinking ``level`` in this model."""
    return dict(model["thinking_template_kwargs"][level])


def _size_problems(model: dict[str, Any]) -> list[str]:
    total = manifest_bytes(model)
    problems = []
    if "size_bytes" in model and model["size_bytes"] != total:
        problems.append(f"size_bytes must be the manifest total, {total}")
    if "size_mb" in model and model["size_mb"] != round(total / 1_000_000):
        problems.append(f"size_mb must be the manifest total in MB, {round(total / 1_000_000)}")
    return problems


def entry_problems(model: Any) -> list[str]:
    """Every way ``model`` breaks the vLLM entry schema (see the module doc)."""
    if not is_vllm_entry(model):
        return ["runtime is not vllm"]
    files = _file_problems(model.get("files"))
    problems = [
        *_identity_problems(model),
        *files,
        *_serving_problems(model),
        *_thinking_problems(model.get("thinking_template_kwargs")),
        *_args_problems(model),
        *_selection_problems(model.get("selection")),
        *([] if files else _size_problems(model)),
    ]
    return [f"{model.get('id') or '<no id>'}: {problem}" for problem in problems]


def require_valid(model: dict[str, Any]) -> dict[str, Any]:
    problems = entry_problems(model)
    if problems:
        raise ValueError("invalid vLLM catalog entry: " + "; ".join(problems))
    return model


def parse_gpu(text: str) -> Gpu:
    """One row of :data:`GPU_QUERY` output, or of :data:`GPU_FREE_QUERY` (``, FREE_MIB``).

    A free field that is not a number ([N/A]) leaves that GPU's free memory
    unknown, as a three-field row does.
    """
    parts = [part.strip() for part in str(text).split(",")]
    if len(parts) in (3, 4):
        uuid, capability, memory = parts[:3]
        size = _GPU_MEMORY.fullmatch(memory)
        free = _GPU_MEMORY.fullmatch(parts[3]) if len(parts) == 4 else None
        if _GPU_UUID.fullmatch(uuid) and _COMPUTE_CAPABILITY.fullmatch(capability) and size:
            return Gpu(uuid, capability, int(size.group(1)), int(free.group(1)) if free else None)
    raise ValueError(f"{text!r} is not UUID,COMPUTE_CAP,MEMORY_MIB[,FREE_MIB] ({GPU_FREE_QUERY})")


def memory_share(model: dict[str, Any]) -> float:
    """The share of its GPU a vLLM entry preallocates: its ``gpu_memory_utilization``, else 0.90."""
    return float(model.get("gpu_memory_utilization") or DEFAULT_MEMORY_SHARE)


def claim_mib(model: dict[str, Any], gpu: Gpu) -> int:
    """The memory vLLM claims on ``gpu`` at start: its share of the GPU's total."""
    return math.ceil(memory_share(model) * gpu.memory_mib)


def free_needed_mib(model: dict[str, Any], gpu: Gpu) -> int:
    """The free memory ``gpu`` needs for vLLM to start: its claim plus its CUDA context."""
    return claim_mib(model, gpu) + START_MARGIN_MIB


def gpu_hardware_fits(model: dict[str, Any], gpu: Gpu) -> bool:
    return gpu.compute_capability in model["compute_capability"] and gpu.memory_mib >= model["min_gpu_mib"]


def gpu_fits(model: dict[str, Any], gpu: Gpu) -> bool:
    """The GPU suits ``model`` and, when its free memory is known, has what vLLM needs to start free."""
    return gpu_hardware_fits(model, gpu) and (gpu.free_mib is None or gpu.free_mib >= free_needed_mib(model, gpu))


def best_gpu(model: dict[str, Any], gpus: Iterable[Gpu]) -> Gpu | None:
    """The largest GPU ``model`` fits on, then the one with the most free memory (the first listed on a tie), or None."""
    fitting = [gpu for gpu in gpus if gpu_fits(model, gpu)]
    if not fitting:
        return None
    return max(fitting, key=lambda gpu: (gpu.memory_mib, gpu.memory_mib if gpu.free_mib is None else gpu.free_mib))


def requirement_text(model: dict[str, Any]) -> str:
    capabilities = " or ".join(model["compute_capability"])
    return f"one GPU with compute capability {capabilities} and at least {model['min_gpu_mib']} MiB"


def rank_entries(catalog: Iterable[dict[str, Any]], gpus: Iterable[Gpu], *,
                 min_context: int = 0, require_min_context: bool = False,
                 installable_only: bool = False) -> list[Candidate]:
    """Every curated vLLM entry that fits one of ``gpus``, best first.

    Order: meets ``min_context``, then the ``selection.vllm`` priority, then
    the larger snapshot. A malformed entry raises ValueError.
    """
    gpus = list(gpus)
    pool = []
    for model in catalog:
        if not is_vllm_entry(model) or str(model.get("source") or "").strip().lower() not in {"", "curated"}:
            continue
        require_valid(model)
        if installable_only and model.get("install_recommendation", True) is False:
            continue
        priority = selection_priority(model)
        gpu = best_gpu(model, gpus)
        meets = not min_context or model["context_length"] >= min_context
        if priority <= 0 or gpu is None or (require_min_context and not meets):
            continue
        pool.append(Candidate(model, gpu, priority, meets))
    pool.sort(key=lambda item: (item.meets_min_context, item.priority, manifest_bytes(item.model)), reverse=True)
    return pool
