"""Qualify native llama-server tuning and speculation for the selected runtime.

Every flag is checked against the selected executable's own --help before a
live model is stopped, because installs keep the llama-server they were given
(b8210 on older installs, b9014 on new ones) and llama.cpp renames flags.

Explicit .env settings fail closed when the runtime cannot honour them. The
settings for the ODS default runtime (--apply-defaults: --ctx-checkpoints 32,
--spec-type ngram-mod, --reasoning from LLAMA_REASONING) are best effort: each
one is added only when the runtime advertises support, and a runtime that
cannot be probed simply starts without them, as it did before they existed.
"""
import argparse
import re
import subprocess
import sys


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
            reasoning=None, reasoning_format=""):
    """Return the runtime's arguments.

    ``reasoning`` (LLAMA_REASONING) and ``reasoning_format`` (the caller's
    mapped --reasoning-format) hand the reasoning flags to this helper; the
    caller must then not pass --reasoning-format itself.
    """
    arguments = requested_arguments(values)
    drafts = requested_draft_settings(*draft)
    default_spec = default_spec_type(spec_default) if defaults and not _unquote(spec_type) else ""
    default_checkpoints = defaults and _unquote(values[1] if len(values) > 1 else "") == ""
    handle_reasoning = defaults and reasoning is not None
    reasoning_mode = _unquote(reasoning or "") or "off"
    fallback_format = _unquote(reasoning_format)
    if fallback_format and not re.fullmatch(r"[A-Za-z0-9_.,-]{1,64}", fallback_format):
        raise ValueError("--reasoning-format requires a llama.cpp format name")
    if not arguments and not drafts and not default_spec and not default_checkpoints and not handle_reasoning:
        return []
    try:
        help_text = runtime_help(binary)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        if arguments or drafts:
            raise
        print(f"ODS native runtime defaults skipped: {error}", file=sys.stderr)
        return reasoning_arguments(None, reasoning_mode, fallback_format) if handle_reasoning else []
    for flag in arguments[::2]:
        if not supported(help_text, flag):
            raise ValueError(f"Selected native runtime does not support {flag}; leave this setting empty or qualify a compatible runtime")
    for flags, value in drafts:
        flag = next((candidate for candidate in flags if supported(help_text, candidate)), None)
        if flag is None:
            raise ValueError(f"Selected native runtime does not support {flags[0]}; leave this setting empty or qualify a compatible runtime")
        arguments.extend((flag, value))
    if default_checkpoints and supported(help_text, "--ctx-checkpoints"):
        arguments.extend(("--ctx-checkpoints", DEFAULT_CTX_CHECKPOINTS))
    if default_spec and supported(help_text, NGRAM_MOD_CAPABILITY) and supported(help_text, "--spec-type"):
        arguments.extend(("--spec-type", default_spec))
    if handle_reasoning:
        arguments.extend(reasoning_arguments(help_text, reasoning_mode, fallback_format))
    return arguments


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--interval", default="")
    parser.add_argument("--checkpoints", default="")
    parser.add_argument("--cache-mib", default="")
    parser.add_argument("--idle-seconds", default="")
    parser.add_argument("--min-spacing", default="")
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
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"ODS native runtime tuning rejected: {error}", file=sys.stderr)
        return 1
    for argument in arguments:
        sys.stdout.buffer.write(argument.encode() + b"\0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
