"""The Windows Desktop profile's own llama-server.

The Desktop launcher starts llama-server through the existing native runtime
(installers/windows/lib/native-llama-runtime.ps1): loopback only, with an API
key file and ready.json/intent.json in one private runtime folder. This module
reads that folder and builds requests; it never logs or returns the key.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import urlsplit

from runtime_profile import RUNTIME_PROFILE, is_loopback_host


class NativeLlmConfigError(ValueError):
    """The launcher did not describe a usable native llama-server."""


@dataclass(frozen=True)
class NativeLlm:
    base_url: str
    runtime_dir: Path

    @property
    def key_file(self) -> Path:
        return self.runtime_dir / "api-key"


def parse(env: Mapping[str, str]) -> NativeLlm:
    """Pure: the loopback llama-server and its private runtime folder."""
    raw = env.get("ODS_NATIVE_LLM_URL", "").strip().rstrip("/")
    url = urlsplit(raw)
    if (url.scheme != "http" or not url.hostname or not is_loopback_host(url.hostname)
            or url.port is None or url.path or url.query or url.fragment
            or url.username is not None or url.password is not None):
        raise NativeLlmConfigError(
            "ODS_NATIVE_LLM_URL must be http://127.0.0.1:<port> for the Windows Desktop profile")
    runtime_dir = env.get("ODS_NATIVE_LLM_RUNTIME_DIR", "").strip()
    if not runtime_dir or not Path(runtime_dir).is_absolute():
        raise NativeLlmConfigError("ODS_NATIVE_LLM_RUNTIME_DIR must be an absolute path")
    return NativeLlm(f"http://{url.hostname}:{url.port}", Path(runtime_dir))


_key_cache: dict[Path, tuple[int, int, str]] = {}


def read_key(llm: NativeLlm) -> str:
    """The current key, re-read only when the file changes (setup may rotate it)."""
    stat = llm.key_file.stat()
    cached = _key_cache.get(llm.key_file)
    if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    key = llm.key_file.read_text(encoding="utf-8").strip()
    if not key:
        raise NativeLlmConfigError(f"The llama-server key file is empty: {llm.key_file}")
    _key_cache[llm.key_file] = (stat.st_mtime_ns, stat.st_size, key)
    return key


def headers(llm: NativeLlm) -> dict[str, str]:
    return {"Authorization": f"Bearer {read_key(llm)}", "Content-Type": "application/json"}


def _read_record(path: Path) -> Optional[dict]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None  # A record being rewritten right now reads as absent.
    return value if isinstance(value, dict) else None


def runtime_records(llm: NativeLlm) -> tuple[Optional[dict], Optional[dict]]:
    """(intent.json, ready.json) as the native runtime last wrote them."""
    return _read_record(llm.runtime_dir / "intent.json"), _read_record(llm.runtime_dir / "ready.json")


def configured() -> Optional[NativeLlm]:
    """The Desktop profile's llama-server, or None in the Compose profile."""
    if not RUNTIME_PROFILE.is_desktop:
        return None
    return parse(os.environ)
