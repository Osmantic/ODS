"""Shared reader for single values out of the install ``.env`` file.

``gpu`` and ``performance_oracle`` each carried an identical private helper that
scanned ``<install_dir>/.env`` line by line for a single ``KEY=value`` entry.
This is the one place that logic lives now.

``config`` keeps its own reader on purpose because it also tolerates
undecodable bytes. The state-returning helper here lets other callers preserve
the same distinction between an empty assignment and a missing key.

Kept intentionally minimal and dependency-free (stdlib only) so it stays a leaf
module importable from anywhere without any risk of an import cycle. Callers
that also honour the process environment continue to check ``os.environ``
themselves before falling back to this file lookup.
"""

from __future__ import annotations

from pathlib import Path


def read_env_file_value_state(key: str, install_dir: str | Path) -> tuple[bool, str]:
    """Return whether ``key`` exists and its value from ``<install_dir>/.env``.

    The presence flag distinguishes a missing key from an intentional empty
    assignment. Surrounding single or double quotes on the value are stripped.
    """
    env_path = Path(install_dir) / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return True, line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return False, ""


def read_env_file_value(key: str, install_dir: str | Path) -> str:
    """Return ``key``'s value, or an empty string when it is unavailable."""
    return read_env_file_value_state(key, install_dir)[1]
