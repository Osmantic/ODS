"""Bring the live gateway's connected-apps entries in line with Pixel's configure output.

The installer runs this as the owner before it compares the live configuration
with Pixel's candidate for the same release. A connect or disconnect that the
coordinator could not finish (Portal was busy, or the machine restarted) then
converges here, instead of failing the update's same-release comparison.

Usage: converge.py LIVE_CONFIG LIVE_GATEWAY_ENV GENERATED_GATEWAY_ENV PIXEL_ENV INSTALL_DIR
Prints ``changed`` or ``unchanged``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pixel_connections import planner, plugins  # noqa: E402

MAX_BYTES = 2 * 1024 * 1024
ZONE_LINE = re.compile(r"^PIXEL_USER_TIMEZONE='([A-Za-z0-9_+\-/]*)'$")


def _read(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_size > MAX_BYTES):
            raise SystemExit(f"unsafe Pixel file: {path.name}")
        return handle.read(MAX_BYTES + 1)


def _replace(path: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.ods-connections.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), 0o600)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def _zone(pixel_env: bytes) -> str | None:
    for line in pixel_env.decode("utf-8").splitlines():
        match = ZONE_LINE.fullmatch(line)
        if match:
            return match.group(1) or None
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        raise SystemExit(__doc__)
    config_path, env_path, generated_env, pixel_env, install_dir = map(Path, argv)
    config_raw, env_raw = _read(config_path), _read(env_path)
    # Plugin changes never stay half-done (the coordinator keeps a choice only once
    # the gateway takes it), so the live configuration's plugins are already right.
    target = dict(planner.gateway_limbs(_read(generated_env).decode("utf-8")), userTimezone=_zone(_read(pixel_env)),
                  plugins=plugins.enabled_in(json.loads(config_raw)))
    try:
        planned = planner.plan_config(json.loads(config_raw), target, install_dir=str(install_dir))
        planned_env = planner.plan_gateway_env(env_raw.decode("utf-8"), target).encode("utf-8")
    except (planner.PlanError, ValueError) as error:
        raise SystemExit(f"Pixel's live configuration is outside the connected-apps shape: {error}") from None
    changed = False
    if planned != json.loads(config_raw):
        _replace(config_path, (json.dumps(planned, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        changed = True
    if planned_env != env_raw:
        _replace(env_path, planned_env)
        changed = True
    print("changed" if changed else "unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
