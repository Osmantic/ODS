"""Qualify native llama-server tuning and speculation for the selected runtime.

Every flag is checked against the selected executable's own --help before a
live model is stopped, because installs keep the llama-server they were given
(b8210 on older installs, b9014 on new ones) and llama.cpp renames flags.

Explicit .env settings fail closed when the runtime cannot honour them. The
settings for the ODS default runtime (--apply-defaults: --parallel from
LLAMA_PARALLEL, --ctx-checkpoints 32, --spec-type ngram-mod, --reasoning from
LLAMA_REASONING, and the shared-slot layout below) are best effort: each one
is added only when the runtime advertises support, and a runtime that cannot
be probed simply starts without them, as it did before they existed.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys


MIB = 1024 ** 2
GIB = 1024 ** 3
# installers/macos/lib/<this file> -> the ODS install (or the repo's ods/).
INSTALL_ROOT = Path(__file__).resolve().parents[3]

OPTIONS = (
    ("--checkpoint-every-n-tokens", -1, 262144),
    ("--ctx-checkpoints", 0, 64),
    ("--cache-ram", 0, 65536),
    ("--sleep-idle-seconds", -1, 86400),
    ("--checkpoint-min-step", 0, 262144),
)

# Draft settings keep one .env name but llama.cpp has spelled the flag two ways.
# b8210 only has the second spelling; b9014 has the first and lists
# --draft-max as removed. The newest spelling is tried first.
DRAFT_N_MAX_FLAGS = ("--spec-draft-n-max", "--draft-max")
DRAFT_TYPE_K_FLAGS = ("--spec-draft-type-k", "--cache-type-k-draft")
DRAFT_TYPE_V_FLAGS = ("--spec-draft-type-v", "--cache-type-v-draft")

# macOS default: keep 32 prompt checkpoints per slot. b8210 keeps 8, so an edit
# more than 8 turns back re-processes the whole prompt; b9014 already defaults
# to 32. On an M4 with Qwen3.5-9B this cut an edit to turn 3 after 12 tool
# turns from 84.3 s to 33.4 s, for at most 32 x ~50 MiB of unified memory.
DEFAULT_CTX_CHECKPOINTS = "32"
# LLAMA_PARALLEL unset keeps one slot, as the launchers always passed.
DEFAULT_PARALLEL = "1"
# llama.cpp --parallel: -1 (auto) or 1 up to LLAMA_MAX_SEQ.
PARALLEL_MAX = 256

# Shared-slot layout: --parallel 2 --kv-unified --ctx-checkpoints 64
# --cache-ram 0, for a hybrid (recurrent) catalog model on a Mac with at most
# 16 GiB, when LLAMA_PARALLEL, LLAMA_ARG_CTX_CHECKPOINTS and LLAMA_ARG_CACHE_RAM
# are all unset.
#
# With one slot, any other client (Talk, Hermes, Open WebUI, a second Pixel
# chat) takes over the slot, and the conversation it replaces survives only in
# llama.cpp's RAM prompt cache: 8 GiB by default, seen at 8017 MiB on a 16 GiB
# Mac mini M4 next to 7.3 GiB of Metal buffers, with swap in use. Two slots in
# one unified KV buffer keep both conversations resident, and the buffer is
# still --ctx-size long, so either slot can use the full context. b9014 saves
# and clears idle slots into the RAM cache on every new task
# (--cache-idle-slots, which needs a RAM cache), so --cache-ram 0 is what keeps
# the second slot resident. Each tool call adds two checkpoints, so 32 lose
# the one at the end of a shared system prompt after about 15 calls; 64 keep
# it for about 30.
#
# Measured on the Mac mini M4 16 GiB, Qwen3.5-9B Q4_K_M, b9014, 64K context,
# a 27-call session then a new chat with the same system prompt: 334.9 s with
# the one-slot default, 262.1 s with 64 checkpoints, 250.4 s with 64 and no
# RAM cache. With another client's request in the middle of the session:
# 361.7 s one-slot default, 453.1 s with 64 and no RAM cache on one slot (the
# session prefix was lost), 274.0 s with this layout.
SHARED_SLOTS = 2
SHARED_SLOT_CHECKPOINTS = 64
SHARED_SLOT_MAX_HOST_BYTES = 16 * GIB
# Flags the layout needs; --cache-idle-slots marks the b9014 behavior above
# (b8210 lacks it and keeps today's defaults).
SHARED_SLOT_CAPABILITIES = ("--kv-unified", "--cache-ram", "--ctx-checkpoints", "--cache-idle-slots")
# Memory budget: two slots x 64 checkpoints, plus the second slot's live and
# speculative state (two more checkpoint-sized copies), must not exceed the
# one-slot default's worst case: 32 checkpoints plus llama.cpp's 8 GiB RAM
# prompt cache. That admits up to about 83 MiB per checkpoint (Qwen3.5-9B:
# 50.25 MiB, so at most 6.4 GiB where the one-slot default could reach 9.6).
LLAMA_DEFAULT_CACHE_RAM_BYTES = 8192 * MIB

# macOS default speculation, the same contract as docker-compose.nvidia.yml:
# LLAMA_ARG_SPEC_TYPE wins, then LLAMA_SPEC_TYPE, then ngram-mod; none opts out.
DEFAULT_SPEC_TYPE = "ngram-mod"
SPEC_DEFAULT_CHOICES = {"ngram-mod", "ngram-simple", "ngram-map-k", "ngram-map-k4v", "ngram-cache", "none"}
# Only runtimes with the dedicated ngram-mod parameters (llama.cpp b8955+,
# which also have the b8842+ speculative checkpoints hybrid models such as
# Qwen3.5 need) get the default. b8210 accepts --spec-type ngram-mod but turns
# speculation off for hybrid models, so it is left alone.
NGRAM_MOD_CAPABILITY = "--spec-ngram-mod-n-match"

# LLAMA_REASONING (off by default). On runtimes with --reasoning (b9014) it is
# passed as --reasoning with llama.cpp's default --reasoning-format, as Docker
# does through LLAMA_ARG_REASONING. b9014 defaults --reasoning to auto, which
# turned Qwen3.5 thinking on for every request without enable_thinking=false,
# and with --reasoning-format none it put the empty "<think>\n\n</think>\n\n"
# block into every reply's content (measured on the Mac mini M4). Runtimes
# without --reasoning (b8210, which never enabled thinking for Qwen3.5) keep
# the caller's --reasoning-format mapping.
REASONING_CHOICES = {"off", "on", "auto"}

HELP_LIMIT = 1024 * 1024


def _unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        value = value[1:-1]
    return value


def requested_arguments(values):
    result = []
    for value, (flag, lower, upper) in zip(values, OPTIONS):
        value = _unquote(value)
        if value == "":
            continue
        if len(value) > 12 or not re.fullmatch(r"-?[0-9]+", value):
            raise ValueError(f"{flag} requires an integer")
        number = int(value)
        zero_forbidden = flag in {"--checkpoint-every-n-tokens", "--sleep-idle-seconds"}
        if not lower <= number <= upper or zero_forbidden and number == 0:
            raise ValueError(f"{flag} is outside the supported range")
        result.extend((flag, str(number)))
    if "--checkpoint-every-n-tokens" in result and "--checkpoint-min-step" in result:
        raise ValueError("Choose either checkpoint interval or minimum spacing, not both")
    return result


def requested_draft_settings(n_max="", type_k="", type_v=""):
    """Validate draft settings as (flag spellings, value) pairs, without running anything."""
    result = []
    n_max = _unquote(n_max)
    if n_max:
        if len(n_max) > 4 or not re.fullmatch(r"[0-9]+", n_max) or not 1 <= int(n_max) <= 256:
            raise ValueError("--spec-draft-n-max requires an integer from 1 to 256")
        result.append((DRAFT_N_MAX_FLAGS, str(int(n_max))))
    for flags, value in ((DRAFT_TYPE_K_FLAGS, type_k), (DRAFT_TYPE_V_FLAGS, type_v)):
        value = _unquote(value)
        if value:
            if not re.fullmatch(r"[a-z0-9_]{1,16}", value):
                raise ValueError(f"{flags[0]} requires a KV cache type such as f16 or q8_0")
            result.append((flags, value))
    return result


def default_spec_type(spec_default):
    value = _unquote(spec_default) or DEFAULT_SPEC_TYPE
    if value not in SPEC_DEFAULT_CHOICES:
        raise ValueError(f"LLAMA_SPEC_TYPE must be one of {', '.join(sorted(SPEC_DEFAULT_CHOICES))}")
    return "" if value == "none" else value


def requested_parallel(value):
    """LLAMA_PARALLEL as a --parallel value; empty when unset."""
    value = _unquote(value)
    if value == "":
        return ""
    if len(value) > 4 or not re.fullmatch(r"-?[0-9]+", value) \
            or not (int(value) == -1 or 1 <= int(value) <= PARALLEL_MAX):
        raise ValueError(f"--parallel requires -1 or an integer from 1 to {PARALLEL_MAX}")
    return str(int(value))


def host_memory_bytes():
    """Physical memory of this Mac (sysctl hw.memsize)."""
    result = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.memsize"], capture_output=True,
                            text=True, timeout=5, check=True)
    value = int(result.stdout.strip())
    if value <= 0:
        raise ValueError("invalid hw.memsize")
    return value


def _model_memory(root):
    """The dashboard's shared memory model, loaded by path as the host agent does."""
    source = root / "extensions/services/dashboard-api/model_memory.py"
    spec = importlib.util.spec_from_file_location("_ods_model_memory", source)
    if spec is None or spec.loader is None:
        raise OSError(f"cannot load {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def model_checkpoint_bytes(model_path, root=None):
    """Bytes per checkpoint of a hybrid (recurrent) catalog model, else 0.

    Dense and sliding-window-only models, and GGUFs the ODS catalog has not
    reviewed, return 0: the shared-slot layout was measured on a hybrid model.
    """
    root = INSTALL_ROOT if root is None else root
    catalog = json.loads((root / "config/model-library.json").read_text(encoding="utf-8"))
    models = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(models, list):
        raise ValueError("invalid model catalog")
    name = Path(model_path).name
    entries = [entry for entry in models if isinstance(entry, dict) and entry.get("gguf_file") == name]
    if len(entries) != 1:
        return 0
    checkpoint_bytes = _model_memory(root).checkpoint_state_bytes(entries[0])
    # A reviewed layout (not None) has a numeric recurrent_state_bytes.
    if checkpoint_bytes is None or not float(entries[0]["recurrent_state_bytes"]) > 0:
        return 0
    return checkpoint_bytes


def shared_slot_budget_fits(host_bytes, checkpoint_bytes):
    """True when the shared-slot layout stays within the one-slot default's worst case."""
    if not 0 < host_bytes <= SHARED_SLOT_MAX_HOST_BYTES or not checkpoint_bytes > 0:
        return False
    shared = (SHARED_SLOTS * SHARED_SLOT_CHECKPOINTS + 2 * (SHARED_SLOTS - 1)) * checkpoint_bytes
    single = int(DEFAULT_CTX_CHECKPOINTS) * checkpoint_bytes + LLAMA_DEFAULT_CACHE_RAM_BYTES
    return shared <= single


def shared_slots_fit(help_text, model):
    """Whether this runtime, Mac and model get the shared-slot layout (best effort)."""
    if not all(supported(help_text, flag) for flag in SHARED_SLOT_CAPABILITIES):
        return False
    try:
        checkpoint_bytes = model_checkpoint_bytes(model)
        if not checkpoint_bytes:
            return False
        host_bytes = host_memory_bytes()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"ODS native shared-slot default skipped: {error}", file=sys.stderr)
        return False
    return shared_slot_budget_fits(host_bytes, checkpoint_bytes)


def supported(help_text, flag):
    """True when --help lists the flag as a live option, not a removed one."""
    pattern = re.compile(r"(?<![\w-])" + re.escape(flag) + r"(?![\w-])")
    return any(pattern.search(line) and "has been removed" not in line.lower()
               for line in help_text.splitlines())


def runtime_help(binary):
    help_result = subprocess.run([binary, "--help"], capture_output=True, text=True,
                                 timeout=15, check=True)
    help_text = help_result.stdout + help_result.stderr
    if len(help_text) > HELP_LIMIT:
        raise ValueError("Native runtime help output exceeds limit")
    return help_text


def reasoning_arguments(help_text, mode, fallback_format):
    """--reasoning where the runtime has it, else the caller's --reasoning-format."""
    if help_text is not None and mode in REASONING_CHOICES and supported(help_text, "--reasoning"):
        return ["--reasoning", mode]
    return ["--reasoning-format", fallback_format] if fallback_format else []


def qualify(binary, values, draft=("", "", ""), *, spec_type="", spec_default="", defaults=False,
            reasoning=None, reasoning_format="", parallel="", model=""):
    """Return the runtime's arguments.

    With ``defaults`` this helper also owns --parallel: ``parallel`` is
    LLAMA_PARALLEL and ``model`` the GGUF path that selects the shared-slot
    layout. ``reasoning`` (LLAMA_REASONING) and ``reasoning_format`` (the
    caller's mapped --reasoning-format) hand the reasoning flags to this
    helper; the caller must then pass neither --parallel nor
    --reasoning-format itself.
    """
    arguments = requested_arguments(values)
    drafts = requested_draft_settings(*draft)
    slots = requested_parallel(parallel) if defaults else ""
    default_spec = default_spec_type(spec_default) if defaults and not _unquote(spec_type) else ""
    default_checkpoints = defaults and _unquote(values[1] if len(values) > 1 else "") == ""
    # An owner setting for slots, checkpoints or the RAM cache turns the shared-slot layout off.
    shared_candidate = (default_checkpoints and not slots and _unquote(model) != ""
                        and _unquote(values[2] if len(values) > 2 else "") == "")
    slot_arguments = ["--parallel", slots or DEFAULT_PARALLEL] if defaults else []
    handle_reasoning = defaults and reasoning is not None
    reasoning_mode = _unquote(reasoning or "") or "off"
    fallback_format = _unquote(reasoning_format)
    if fallback_format and not re.fullmatch(r"[A-Za-z0-9_.,-]{1,64}", fallback_format):
        raise ValueError("--reasoning-format requires a llama.cpp format name")
    if not arguments and not drafts and not default_spec and not default_checkpoints and not handle_reasoning:
        return slot_arguments
    try:
        help_text = runtime_help(binary)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        if arguments or drafts:
            raise
        print(f"ODS native runtime defaults skipped: {error}", file=sys.stderr)
        return slot_arguments + (reasoning_arguments(None, reasoning_mode, fallback_format) if handle_reasoning else [])
    for flag in arguments[::2]:
        if not supported(help_text, flag):
            raise ValueError(f"Selected native runtime does not support {flag}; leave this setting empty or qualify a compatible runtime")
    for flags, value in drafts:
        flag = next((candidate for candidate in flags if supported(help_text, candidate)), None)
        if flag is None:
            raise ValueError(f"Selected native runtime does not support {flags[0]}; leave this setting empty or qualify a compatible runtime")
        arguments.extend((flag, value))
    checkpoint_arguments = ["--ctx-checkpoints", DEFAULT_CTX_CHECKPOINTS]
    if shared_candidate and shared_slots_fit(help_text, model):
        slot_arguments = ["--parallel", str(SHARED_SLOTS), "--kv-unified"]
        checkpoint_arguments = ["--ctx-checkpoints", str(SHARED_SLOT_CHECKPOINTS), "--cache-ram", "0"]
    if default_checkpoints and supported(help_text, "--ctx-checkpoints"):
        arguments.extend(checkpoint_arguments)
    if default_spec and supported(help_text, NGRAM_MOD_CAPABILITY) and supported(help_text, "--spec-type"):
        arguments.extend(("--spec-type", default_spec))
    if handle_reasoning:
        arguments.extend(reasoning_arguments(help_text, reasoning_mode, fallback_format))
    return slot_arguments + arguments


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--interval", default="")
    parser.add_argument("--checkpoints", default="")
    parser.add_argument("--cache-mib", default="")
    parser.add_argument("--idle-seconds", default="")
    parser.add_argument("--min-spacing", default="")
    # LLAMA_PARALLEL and the GGUF path; used only with --apply-defaults.
    parser.add_argument("--parallel", default="")
    parser.add_argument("--model", default="")
    # LLAMA_ARG_SPEC_TYPE is passed to llama-server by the caller; it is read
    # here only so that an explicit choice suppresses the default.
    parser.add_argument("--explicit-spec-type", default="")
    parser.add_argument("--spec-default", default="")
    parser.add_argument("--draft-n-max", default="")
    parser.add_argument("--draft-type-k", default="")
    parser.add_argument("--draft-type-v", default="")
    # LLAMA_REASONING (empty means ODS's default, off) and the --reasoning-format
    # the caller would otherwise pass. With both, this helper owns the flags.
    parser.add_argument("--reasoning-mode", default=None)
    parser.add_argument("--reasoning-format-fallback", default="")
    parser.add_argument("--apply-defaults", action="store_true",
                        help="add the macOS defaults the runtime supports (not for registered model profiles)")
    args = parser.parse_args()
    try:
        arguments = qualify(
            args.binary,
            (args.interval, args.checkpoints, args.cache_mib, args.idle_seconds, args.min_spacing),
            (args.draft_n_max, args.draft_type_k, args.draft_type_v),
            spec_type=args.explicit_spec_type,
            spec_default=args.spec_default,
            defaults=args.apply_defaults,
            reasoning=args.reasoning_mode,
            reasoning_format=args.reasoning_format_fallback,
            parallel=args.parallel,
            model=args.model,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"ODS native runtime tuning rejected: {error}", file=sys.stderr)
        return 1
    for argument in arguments:
        sys.stdout.buffer.write(argument.encode() + b"\0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
