"""Default llama.cpp RAM prompt-cache size (llama_cache_budget.py).

The table matches tests/test-llama-memory-budget.sh (Linux installer) and
tests/test-windows-llama-memory-budget.ps1 (Windows installer), which apply
the same rule.
"""

from pathlib import Path

import pytest

from llama_cache_budget import (
    COMPOSE_MEMORY_LIMITS,
    default_cache_ram_mib,
    memory_limit_mib,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("12G", 12288),
        ("12gb", 12288),
        ("512m", 512),
        ("1T", 1048576),
        ("12884901888", 12288),
        ("1.5G", None),
        ("", None),
        (None, None),
    ],
)
def test_memory_limit_mib(value, expected):
    assert memory_limit_mib(value) == expected


@pytest.mark.parametrize(
    ("memory_gib", "limit", "expected"),
    [
        # 16 GB WSL VM (MemTotal 15.3 GiB) with the 12G 8 GB-GPU profile.
        (15, "12G", 3072),
        (15, "64G", 3072),
        (16, "64G", 3413),
        (24, "20G", 5120),
        (29, "64G", 7850),
        # 32 GB host (31 GiB) with the NVIDIA 27G default: the limit binds.
        (31, "27G", 6912),
        (30, "64G", None),
        # A large host whose profile limits the container to 12G.
        (62, "12G", 3072),
        (125, "64G", None),
        # The CPU compose limit bounds any host.
        (64, "6G", 1536),
        (8, "5G", 682),
        (7, "4G", 512),
        (4, "1G", 512),
        (0, "12G", 3072),
        (0, "", None),
    ],
)
def test_default_cache_ram_mib(memory_gib, limit, expected):
    assert default_cache_ram_mib(memory_gib, limit) == expected


def test_compose_limits_match_the_overlays():
    root = Path(__file__).resolve().parents[4]
    for backend, overlay in (
        ("nvidia", "docker-compose.nvidia.yml"),
        ("cpu", "docker-compose.cpu.yml"),
        ("intel", "docker-compose.intel.yml"),
        ("sycl", "docker-compose.arc.yml"),
    ):
        text = (root / overlay).read_text(encoding="utf-8")
        expected = "memory: ${LLAMA_SERVER_MEMORY_LIMIT:-" + COMPOSE_MEMORY_LIMITS[backend] + "}"
        assert expected in text, (backend, overlay)
    assert COMPOSE_MEMORY_LIMITS["none"] == COMPOSE_MEMORY_LIMITS["cpu"]
    assert "amd" not in COMPOSE_MEMORY_LIMITS
    assert "apple" not in COMPOSE_MEMORY_LIMITS
