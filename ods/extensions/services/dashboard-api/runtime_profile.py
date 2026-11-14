"""Which runtime this dashboard-api serves, read once from the environment.

The default "compose" profile is the Docker Compose stack. "windows-desktop"
is a native Windows process with no Docker, no WSL and no host agent: the
launcher must hand it a private data directory and the API key, because the
container defaults (/data, a generated key, host.docker.internal) either land
in a drive root or reach another ODS installation on the same PC.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import PureWindowsPath, PurePosixPath
from typing import Mapping, Optional

COMPOSE = "compose"
WINDOWS_DESKTOP = "windows-desktop"
_PROFILES = (COMPOSE, WINDOWS_DESKTOP)


class RuntimeProfileError(ValueError):
    """The environment does not describe a runnable profile."""


@dataclass(frozen=True)
class RuntimeProfile:
    name: str
    data_dir: Optional[str]

    @property
    def is_desktop(self) -> bool:
        return self.name == WINDOWS_DESKTOP

    @property
    def host_agent(self) -> bool:
        """The Desktop profile has no host agent; it must never be dialed."""
        return not self.is_desktop

    @property
    def child_processes(self) -> bool:
        """Desktop runs windowless; a console child would flash on screen."""
        return not self.is_desktop


def is_loopback_host(host: str) -> bool:
    """True for a name or address that can only mean this computer."""
    host = host.strip().strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_absolute(path: str) -> bool:
    return PureWindowsPath(path).is_absolute() or PurePosixPath(path).is_absolute()


def parse(env: Mapping[str, str]) -> RuntimeProfile:
    """Return the profile the environment selects, or raise."""
    name = env.get("ODS_RUNTIME_PROFILE", "").strip() or COMPOSE
    if name not in _PROFILES:
        raise RuntimeProfileError(
            f"ODS_RUNTIME_PROFILE={name!r} is not one of: {', '.join(_PROFILES)}"
        )
    if name == COMPOSE:
        return RuntimeProfile(name, env.get("ODS_DATA_DIR") or None)
    data_dir = env.get("ODS_DATA_DIR", "").strip()
    if not data_dir or not _is_absolute(data_dir):
        raise RuntimeProfileError(
            "The windows-desktop profile needs ODS_DATA_DIR set to an absolute path"
        )
    if not env.get("DASHBOARD_API_KEY", "").strip():
        raise RuntimeProfileError("The windows-desktop profile needs DASHBOARD_API_KEY")
    return RuntimeProfile(name, data_dir)


RUNTIME_PROFILE = parse(os.environ)
