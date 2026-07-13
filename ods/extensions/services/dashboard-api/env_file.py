"""Shared reader for single values out of the install ``.env`` file.

``gpu`` and ``performance_oracle`` each carried an identical private helper that
scanned ``<install_dir>/.env`` line by line for a single ``KEY=value`` entry.
This is the one place that logic lives now.

``config`` keeps its own reader on purpose: it reports whether the key was
present at all (so ``read_live_env_value`` can distinguish an empty value from a
missing one) and it tolerates undecodable bytes, neither of which this helper
does.

Kept intentionally minimal and dependency-free (stdlib only) so it stays a leaf
module importable from anywhere without any risk of an import cycle. Callers
that also honour the process environment continue to check ``os.environ``
themselves before falling back to this file lookup.
"""

from __future__ import annotations

from pathlib import Path


def read_env_file_value(key: str, install_dir: str | Path) -> str:
    """Return ``key``'s value from ``<install_dir>/.env`` (split on first ``=``).

    Returns an empty string when the file is missing/unreadable or the key is
    absent. Surrounding single or double quotes on the value are stripped.
    """
    env_path = Path(install_dir) / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return ""
