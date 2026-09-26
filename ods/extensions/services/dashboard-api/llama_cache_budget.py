"""Default size of llama.cpp's RAM prompt cache for a Docker llama-server.

llama.cpp keeps earlier prompts, with their context checkpoints, in host RAM
(``--cache-ram`` / ``LLAMA_ARG_CACHE_RAM``) so a conversation that lost the
slot to another client resumes without re-processing its whole prompt. b9014
caps that cache at 8192 MiB. No ODS memory check counts it: the container
limit, the VRAM fit and the catalog profiles all size weights, KV cache and
checkpoints only.

The installers apply the same rule (``ods_default_llama_cache_ram_mib`` in
installers/lib/llama-memory-budget.sh and ``Get-ODSDefaultLlamaCacheRamMiB``
in installers/windows/lib/env-generator.ps1); the host agent uses this module
when a model activation finds no size in .env.
"""

from __future__ import annotations

import re

LLAMA_CPP_DEFAULT_CACHE_RAM_MIB = 8192
# Memory the rest of ODS and the OS keep for themselves. In a 16 GB WSL VM
# running the default stack the other containers held 3.6 GiB at idle; when
# llama-server was OOM-killed, every other process held 4.6 GiB of RAM and
# 3.6 GiB of swap.
STACK_RESERVE_GIB = 6
MIN_CACHE_RAM_MIB = 512

_MEMORY_LIMIT = re.compile(r"^([0-9]+)([kKmMgGtT]?)[bB]?$")
_UNIT_MIB = {"T": 1024 * 1024, "G": 1024, "M": 1}

# Compose defaults of the llama-server memory limit per Docker backend.
COMPOSE_MEMORY_LIMITS = {
    "nvidia": "64G",
    "cpu": "6G",
    "none": "6G",
    "intel": "24G",
    "sycl": "24G",
}


def memory_limit_mib(value: object) -> int | None:
    """A Docker memory limit (12G, 512m, 12gb or plain bytes) in MiB."""
    match = _MEMORY_LIMIT.match(str(value or "").strip())
    if not match:
        return None
    number = int(match.group(1))
    unit = match.group(2).upper()
    if unit == "K":
        return number // 1024
    if not unit:
        return number // (1024 * 1024)
    return number * _UNIT_MIB[unit]


def default_cache_ram_mib(memory_gib: int, container_limit: object = None) -> int | None:
    """Return the default --cache-ram in MiB, or None when 8192 MiB fits.

    A third of the memory left after ``STACK_RESERVE_GIB`` for the rest of
    ODS and the OS, at most a quarter of the llama-server container limit (the
    rest of the container holds weights, KV cache and checkpoints), and at
    least ``MIN_CACHE_RAM_MIB``. In a 16 GB WSL VM that is 3 GiB: one
    64K-token Qwen3.5-9B agent conversation with its 32 checkpoints (~2.5 GiB)
    plus a few short prompts. b9014 always keeps the newest cached prompt even
    when it alone exceeds the cap. ``memory_gib`` is 0 when unknown.
    """
    cache_mib = LLAMA_CPP_DEFAULT_CACHE_RAM_MIB
    if memory_gib > 0:
        cache_mib = min(cache_mib, max(0, memory_gib - STACK_RESERVE_GIB) * 1024 // 3)
    limit_mib = memory_limit_mib(container_limit)
    if limit_mib:
        cache_mib = min(cache_mib, limit_mib // 4)
    cache_mib = max(MIN_CACHE_RAM_MIB, cache_mib)
    if cache_mib >= LLAMA_CPP_DEFAULT_CACHE_RAM_MIB:
        return None
    return cache_mib
