#!/usr/bin/env python3
"""ODS Host Agent — manages extension containers from the host."""

# PEP 604 union syntax (e.g. `threading.Thread | None`) is evaluated at runtime
# in non-stringified annotations, which crashes on Python 3.9 — the version
# Apple ships as /usr/bin/python3 on macOS 14.x. The LaunchAgent fails at
# import with `TypeError: unsupported operand type(s) for |: 'type' and
# 'NoneType'`, leaving ODS's macOS install with no host agent.
# `from __future__ import annotations` makes ALL annotations lazy strings,
# so PEP 604 syntax parses on Python 3.7+. The host-agent doesn't use
# typing.get_type_hints() at runtime, so lazy annotations are safe here.
from __future__ import annotations

import argparse
import atexit
import collections
import importlib
import json
import logging
import os
import platform
import re
import secrets
import shutil
import signal
import socket
import stat as stat_mod
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

VERSION = "1.0.0"
ODS_VERSION = VERSION
SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
MAX_BODY = 16384
SUBPROCESS_TIMEOUT_START = 600  # 10 min — image pulls can be slow
SUBPROCESS_TIMEOUT_STOP = 120   # 2 min — stop should be fast
HOOK_TIMEOUT = 120              # 2 min — hook execution timeout
VALID_HOOK_NAMES = frozenset({
    "pre_install", "post_install", "pre_start", "post_start",
    "pre_uninstall", "post_uninstall",
})
logger = logging.getLogger("ods-host-agent")

# Hardcoded fallback — used when core-service-ids.json is missing or unreadable.
# Prevents fail-open: without this, a missing JSON file would allow anyone with
# the API key to stop core services like llama-server or dashboard-api.
_FALLBACK_CORE_IDS = frozenset({
    "dashboard-api", "dashboard", "llama-server", "open-webui",
    "litellm", "langfuse", "hermes", "hermes-proxy", "n8n", "openclaw", "opencode",
    "perplexica", "searxng", "qdrant", "tts", "whisper",
    "embeddings", "token-spy", "comfyui", "ape", "privacy-shield",
})

INSTALL_DIR: Path = Path()
DATA_DIR: Path = Path()
AGENT_API_KEY: str = ""
GPU_BACKEND: str = "nvidia"
TIER: str = "1"
GPU_COUNT: str = "1"
CORE_SERVICE_IDS: set = set()
# Always-on services defined in docker-compose.base.yml — never stoppable via API.
# Distinct from CORE_SERVICE_IDS (which is the allowlist of known service IDs).
ALWAYS_ON_SERVICES: frozenset = frozenset({"llama-server", "open-webui", "dashboard", "dashboard-api"})
USER_EXTENSIONS_DIR: Path = Path()
EXTENSIONS_DIR: Path = Path()

# Per-service locks to prevent concurrent start+stop races on the same service
_service_locks: dict[str, threading.Lock] = collections.defaultdict(threading.Lock)
_ALLOWED_CORE_RECREATE_IDS = frozenset({
    "llama-server", "open-webui", "litellm", "langfuse", "n8n",
    "hermes", "hermes-proxy", "openclaw", "opencode", "perplexica", "searxng", "qdrant",
    "tts", "whisper", "embeddings", "token-spy", "comfyui",
    "ape", "privacy-shield",
})


def _to_bash_path(path: Path) -> str:
    """Convert a Windows path into a Git-Bash-friendly POSIX path when needed."""
    resolved = str(path)
    if platform.system() != "Windows":
        return resolved
    normalized = resolved.replace("\\", "/")
    match = re.match(r"^([A-Za-z]):/(.*)$", normalized)
    if match:
        drive, tail = match.groups()
        return f"/{drive.lower()}/{tail}"
    return normalized


def _python_can_import(python_cmd: str, module: str) -> bool:
    try:
        result = subprocess.run(
            [python_cmd, "-c", f"import {module}"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _process_can_import(module: str) -> bool:
    """Return whether this already-running host-agent process can import module."""
    importlib.invalidate_caches()
    try:
        importlib.import_module(module)
    except ImportError:
        return False
    return True


def _ensure_windows_resolver_pyyaml(python_cmd: str) -> None:
    """Ensure the Windows host Python and this process can import PyYAML."""
    if platform.system() != "Windows":
        return
    if _python_can_import(python_cmd, "yaml") and _process_can_import("yaml"):
        return

    logger.warning(
        "PyYAML is missing from %s; installing it so compose resolution can validate extensions",
        python_cmd,
    )
    pip_cmd = [
        python_cmd, "-m", "pip", "install",
        "--user", "--disable-pip-version-check", "--quiet", "PyYAML",
    ]
    try:
        result = subprocess.run(
            pip_cmd,
            capture_output=True, text=True, timeout=180, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"failed to install PyYAML for compose resolution: {exc}") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            "PyYAML is required for compose resolution, but automatic install failed: "
            f"{detail[:1000]}"
        )

    if not _python_can_import(python_cmd, "yaml"):
        raise RuntimeError(
            "PyYAML install completed but the Windows resolver Python still cannot import yaml"
        )
    if not _process_can_import("yaml"):
        raise RuntimeError(
            "PyYAML install completed but this host-agent process still cannot import yaml"
        )


def _find_usable_bash() -> str | None:
    """Return a Bash executable that can run shell scripts on this host."""
    global _usable_bash
    if isinstance(_usable_bash, str):
        return _usable_bash
    if _usable_bash is False:
        return None

    candidates: list[str] = []
    found = shutil.which("bash")
    if found:
        candidates.append(found)

    if platform.system() == "Windows":
        candidates.extend([
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
        ])
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            candidates.extend([
                str(Path(local_appdata) / "Programs" / "Git" / "bin" / "bash.exe"),
                str(Path(local_appdata) / "Programs" / "Git" / "usr" / "bin" / "bash.exe"),
            ])

    seen: set[str] = set()
    for bash in candidates:
        if not bash or bash in seen:
            continue
        seen.add(bash)
        if not Path(bash).exists() and shutil.which(bash) is None:
            continue
        try:
            result = subprocess.run(
                [bash, "-lc", "printf ok"],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0 and result.stdout == "ok":
            _usable_bash = bash
            return bash

    _usable_bash = False
    return None

# Model download state — only one download at a time
_model_download_lock = threading.Lock()
_model_download_thread: threading.Thread | None = None
_model_download_proc: subprocess.Popen | None = None
_model_download_cancel = threading.Event()
# Model activation lock — prevent concurrent .env writes and Docker restarts
_model_activate_lock = threading.Lock()
# Update lock/state: only one background ods-update run at a time.
_update_lock = threading.Lock()
_update_status_lock = threading.Lock()
_update_thread: threading.Thread | None = None
_update_usable_bash: str | bool | None = None
_usable_bash: str | bool | None = None


def load_env(env_path: Path) -> dict:
    """Parse .env file, return dict of key=value pairs."""
    env = {}
    if not env_path.exists():
        return env
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip().strip("'\"")
    return env


def load_core_service_ids(config_path: Path) -> set:
    if not config_path.exists():
        logger.warning("core-service-ids.json not found at %s — using hardcoded fallback", config_path)
        return set(_FALLBACK_CORE_IDS)
    try:
        with open(config_path, encoding="utf-8") as f:
            ids = json.load(f)
        return set(ids) if isinstance(ids, list) else set(_FALLBACK_CORE_IDS)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read core-service-ids.json: %s — using fallback", e)
        return set(_FALLBACK_CORE_IDS)


def _detect_docker_network_gateway(network_name: str) -> str:
    """Detect a Docker network gateway IP for scoped host-agent binding.

    Returns the gateway IP (for example ``172.18.0.1``) or empty string on
    failure. Containers on that Docker network can reach this address, while
    LAN devices cannot route to it directly.
    """
    import ipaddress as _ipaddress
    try:
        result = subprocess.run(
            ["docker", "network", "inspect", network_name,
             "--format", "{{(index .IPAM.Config 0).Gateway}}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            addr = result.stdout.strip()
            if addr:
                _ipaddress.ip_address(addr)  # validate — Docker can return "<no value>"
                logger.info("Detected Docker network gateway for %s: %s", network_name, addr)
                return addr
        else:
            logger.warning(
                "Docker network gateway detection failed for %s (exit %d): %s",
                network_name,
                result.returncode,
                result.stderr.strip() or "<no stderr>",
            )
    except ValueError:
        logger.debug("Docker network %s returned non-IP gateway value, ignoring", network_name)
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("Docker network gateway detection failed for %s: %s", network_name, exc)
    return ""


def _detect_docker_bridge_gateway() -> str:
    """Detect Docker's default bridge gateway as a compatibility fallback."""
    return _detect_docker_network_gateway("bridge")


def _resolve_agent_bind_addr(env: dict, system_name: str | None = None) -> str:
    """Resolve the host-agent bind address without exposing LAN by default."""
    explicit = env.get("ODS_AGENT_BIND", "").strip()
    if explicit:
        return explicit

    system_name = system_name or platform.system()
    if system_name in ("Darwin", "Windows"):
        return "127.0.0.1"

    if system_name == "Linux":
        # Prefer ODS's actual compose network. The bridge fallback keeps
        # older/partial installs reachable without binding the Docker
        # management API to every LAN interface.
        return (
            _detect_docker_network_gateway("ods-network")
            or _detect_docker_bridge_gateway()
            or "127.0.0.1"
        )

    return "127.0.0.1"


def invalidate_compose_cache() -> None:
    """Drop the saved .compose-flags cache so the next resolve re-runs the script."""
    (INSTALL_DIR / ".compose-flags").unlink(missing_ok=True)


def resolve_compose_flags() -> list:
    flags_file = INSTALL_DIR / ".compose-flags"
    if flags_file.exists():
        raw = flags_file.read_text(encoding="utf-8").strip()
        if raw:
            return raw.split()

    script = INSTALL_DIR / "scripts" / "resolve-compose-stack.sh"
    # Contract note: every resolver launch below must include --gpu-count.
    if not script.exists():
        raise RuntimeError(f"resolve-compose-stack.sh not found at {script}")
    bash = _find_usable_bash()
    if not bash:
        raise RuntimeError(
            "Compose resolution requires a usable Bash runtime. "
            "Install Git Bash or run ODS through WSL/Linux."
        )
    # --gpu-count gates the multigpu-{backend}.yml overlay; without it,
    # the host agent would resolve a single-GPU stack on multi-GPU hosts.
    env = os.environ.copy()
    if platform.system() == "Windows":
        _ensure_windows_resolver_pyyaml(sys.executable)
        env["ODS_PYTHON_CMD"] = _to_bash_path(Path(sys.executable))
    cmd = [
        bash, _to_bash_path(script),
        "--script-dir", _to_bash_path(INSTALL_DIR),
        "--tier", TIER,
        "--gpu-backend", GPU_BACKEND,
        "--gpu-count", GPU_COUNT,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, check=True,
            cwd=str(INSTALL_DIR), timeout=30, env=env,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(
            f"compose resolver failed: {detail[:1000]}",
        ) from exc
    return result.stdout.strip().split()


# Filesystem types that silently ignore POSIX ownership/permissions.
# Used by _precreate_data_dirs to skip os.chown when running on exFAT/FAT/NTFS-fuseblk
# instead of raising a misleading PermissionError.
_NON_POSIX_FS = frozenset({
    "exfat", "msdos", "vfat", "fat", "fat32", "fat16",
    "ntfs", "ntfs-3g", "fuseblk", "9p", "drvfs",
    "ms-dos",
})


def _fs_type(path: Path) -> str | None:
    """Return the lowercased filesystem type for ``path``, or ``None``.

    Linux: walk /proc/self/mountinfo to find the longest matching mountpoint.
    macOS / BSD: shell out to ``stat -f %T`` (Python's ``os.statvfs_result``
    does not expose ``f_basetype``).
    """
    try:
        target = str(Path(path).resolve())
    except OSError:
        return None

    mountinfo = Path("/proc/self/mountinfo")
    if mountinfo.exists():
        try:
            best_match = ""
            best_fstype: str | None = None
            with mountinfo.open("r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split()
                    if "-" not in parts:
                        continue
                    sep_idx = parts.index("-")
                    if sep_idx + 1 >= len(parts) or sep_idx < 5:
                        continue
                    mountpoint = parts[4]
                    fstype = parts[sep_idx + 1]
                    if target == mountpoint or target.startswith(mountpoint.rstrip("/") + "/"):
                        if len(mountpoint) >= len(best_match):
                            best_match = mountpoint
                            best_fstype = fstype
            if best_fstype:
                return best_fstype.lower()
        except OSError:
            pass

    try:
        result = subprocess.run(
            ["stat", "-f", "%T", target],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().lower()
    except (FileNotFoundError, subprocess.SubprocessError):
        pass

    return None


def _precreate_data_dirs(service_id: str):
    """Pre-create data directories for an extension with correct ownership."""
    ext_dir = _find_ext_dir(service_id)
    if ext_dir is None:
        return
    compose_path = ext_dir / "compose.yaml"
    if not compose_path.exists():
        return
    try:
        import yaml
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    except ImportError:
        # PyYAML not available — skip pre-creation
        logger.debug("PyYAML not available, skipping data dir pre-creation for %s", service_id)
        return
    except (OSError, yaml.YAMLError) as e:
        logger.debug("Failed to parse compose.yaml for %s: %s", service_id, e)
        return
    if not isinstance(data, dict):
        return
    manifest_uid = None
    manifest = _read_manifest(ext_dir)
    if isinstance(manifest, dict):
        service_def = manifest.get("service", {})
        if isinstance(service_def, dict):
            container_uid = service_def.get("container_uid")
            if isinstance(container_uid, int):
                manifest_uid = container_uid
            elif isinstance(container_uid, str) and container_uid.isdigit():
                manifest_uid = int(container_uid)
    for svc_name, svc_def in data.get("services", {}).items():
        if not isinstance(svc_def, dict):
            continue
        uid = None
        user_field = svc_def.get("user")
        if user_field:
            user_str = str(user_field).split(":")[0]
            m = re.match(r'\$\{[^:}]+:-(\d+)\}', user_str)
            if m:
                uid = int(m.group(1))
            elif user_str.isdigit():
                uid = int(user_str)
        if uid is None:
            uid = manifest_uid
        volumes = svc_def.get("volumes", [])
        if not isinstance(volumes, list):
            continue
        for vol in volumes:
            if isinstance(vol, dict):
                # Compose long-form mount; only bind mounts have a host source.
                if vol.get("type") != "bind":
                    continue
                vol_str = vol.get("source", "")
            else:
                vol_str = str(vol).split(":")[0]
            # Skip sources compose does not pre-expand (env vars, home,
            # backticks, Windows-style escapes) — we cannot resolve them safely.
            if not vol_str or vol_str.startswith(("~", "$", "`", "\\")):
                continue
            # Accept any relative bind-mount source (e.g. "./data/state",
            # "./upload", "config/stuff"). Skip named volumes (no "/") and
            # absolute paths ("/etc/..."). Docker Compose v2 resolves relative
            # bind paths against the project directory (the first -f file's
            # parent = INSTALL_DIR), not the individual fragment's directory,
            # so anchor on INSTALL_DIR to match where Compose actually mounts.
            if vol_str.startswith("/") or "/" not in vol_str:
                continue
            dir_path = (INSTALL_DIR / vol_str.lstrip("./")).resolve()
            try:
                dir_path.relative_to(INSTALL_DIR.resolve())
            except ValueError:
                logger.warning("Skipping out-of-tree volume path in %s: %s", service_id, vol_str)
                continue
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
                if uid is not None and os.getuid() == 0:
                    # Defense-in-depth: the installer preflight already
                    # blocks non-POSIX filesystems at INSTALL_DIR, but
                    # runtime extension installs (post-setup) can still
                    # land on a non-POSIX volume. chown there is a silent
                    # no-op or raises EPERM/EOPNOTSUPP — skip cleanly.
                    fs = _fs_type(dir_path)
                    if fs in _NON_POSIX_FS:
                        logger.warning(
                            "Skipping chown for %s on non-POSIX filesystem %s "
                            "(extension may not function correctly)",
                            dir_path, fs,
                        )
                    else:
                        os.chown(str(dir_path), uid, uid)
            except OSError as e:
                logger.warning("Failed to pre-create %s: %s", dir_path, e)


def docker_compose_action(service_id: str, action: str) -> tuple:
    flags = resolve_compose_flags()
    if action == "start":
        _precreate_data_dirs(service_id)
        cmd = ["docker", "compose"] + flags + ["up", "-d", service_id]
    elif action == "stop":
        cmd = ["docker", "compose"] + flags + ["stop", service_id]
    else:
        return False, f"Unknown action: {action}"
    timeout = SUBPROCESS_TIMEOUT_START if action == "start" else SUBPROCESS_TIMEOUT_STOP
    try:
        result = subprocess.run(
            cmd, cwd=str(INSTALL_DIR),
            capture_output=True, text=True, timeout=timeout,
        )
        return (True, "") if result.returncode == 0 else (False, result.stderr[:500])
    except subprocess.TimeoutExpired:
        return False, f"Docker compose operation timed out ({timeout}s)"


def validate_core_recreate_ids(service_ids: list[str]) -> tuple[bool, str]:
    """Validate a requested set of core services for safe recreation."""
    if not isinstance(service_ids, list) or not service_ids:
        return False, "service_ids must be a non-empty list"

    for service_id in service_ids:
        if not isinstance(service_id, str) or not SERVICE_ID_RE.match(service_id):
            return False, f"Invalid service_id: {service_id!r}"
        if service_id not in CORE_SERVICE_IDS:
            return False, f"Service is not a core ODS service: {service_id}"
        if service_id not in _ALLOWED_CORE_RECREATE_IDS:
            return False, f"Service is not eligible for dashboard-triggered recreation: {service_id}"

    return True, ""


def docker_compose_recreate(service_ids: list[str]) -> tuple:
    """Force-recreate a set of allowed core services using the current compose stack."""
    ok, error = validate_core_recreate_ids(service_ids)
    if not ok:
        return False, error

    flags = resolve_compose_flags()
    cmd = ["docker", "compose"] + flags + ["up", "-d", "--no-deps", "--force-recreate"] + service_ids
    try:
        result = subprocess.run(
            cmd, cwd=str(INSTALL_DIR),
            capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_START,
        )
        return (True, "") if result.returncode == 0 else (False, result.stderr[:500] or result.stdout[:500])
    except subprocess.TimeoutExpired:
        return False, f"Docker compose operation timed out ({SUBPROCESS_TIMEOUT_START}s)"


def _post_install_core_recreate(service_id: str) -> None:
    """Force-recreate core services whose env was overridden by ``service_id``'s
    compose.yaml overlay.

    ``docker compose up -d <ext>`` (how _handle_install starts the extension)
    will not pick up overlay changes targeting already-running core services
    without ``--force-recreate``. openclaw's compose.yaml appends an
    OPENAI_API_BASE_URLS entry to open-webui; without this post-install
    recreate that overlay is silently ignored until the next core restart.

    Failure is logged and swallowed — the extension itself is already running;
    the overlay will apply on the next manual restart of the core service.
    """
    if service_id != "openclaw":
        return
    ok, err = docker_compose_recreate(["open-webui"])
    if not ok:
        logger.warning(
            "Post-install recreate of open-webui failed after openclaw install: %s",
            err,
        )


def _parse_mem_value(s: str) -> float:
    """Parse Docker memory string like '256MiB' or '4GiB' to MB."""
    s = s.strip()
    multipliers = {"TiB": 1024*1024, "GiB": 1024, "MiB": 1, "KiB": 1/1024, "B": 1/(1024*1024)}
    for suffix, mult in multipliers.items():
        if s.endswith(suffix):
            try:
                return float(s[:-len(suffix)].strip()) * mult
            except ValueError:
                return 0.0
    return 0.0


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._\-=+/]+", re.IGNORECASE)


def _write_progress(service_id: str, status: str, phase_label: str = "",
                    error: str | None = None) -> None:
    """Atomically write install progress file."""
    progress_dir = DATA_DIR / "extension-progress"
    progress_dir.mkdir(parents=True, exist_ok=True)
    progress_file = progress_dir / f"{service_id}.json"
    tmp_file = progress_file.with_suffix(".json.tmp")

    # Preserve started_at from existing file
    started_at = _iso_now()
    if progress_file.exists():
        try:
            existing = json.loads(progress_file.read_text(encoding="utf-8"))
            started_at = existing.get("started_at", started_at)
        except (json.JSONDecodeError, OSError):
            pass

    sanitized_error = _BEARER_RE.sub("Bearer [REDACTED]", error) if error else None

    data = {
        "service_id": service_id,
        "status": status,
        "phase_label": phase_label,
        "error": sanitized_error,
        "started_at": started_at,
        "updated_at": _iso_now(),
    }
    tmp_file.write_text(json.dumps(data), encoding="utf-8")
    # os.replace (not os.rename) — Windows os.rename raises FileExistsError
    # when the destination exists; os.replace always overwrites atomically.
    os.replace(str(tmp_file), str(progress_file))


def _model_file_ready(path: Path) -> bool:
    """Return True only for a final GGUF file that exists and is non-empty."""
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _local_model_name_from_gguf(gguf_file: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(gguf_file).stem).strip("-._")
    return name or "local-gguf"


def _local_gguf_filename_from_id(model_id: str) -> str | None:
    """Map a Dashboard/local model id to a safe GGUF filename candidate."""
    token = str(model_id or "").strip()
    if token.lower().startswith("extra."):
        token = token[6:]
    if not token or any(sep in token for sep in ("/", "\\", "\x00")):
        return None
    filename = token if token.lower().endswith(".gguf") else f"{token}.gguf"
    if filename.lower().endswith(".part") or Path(filename).name != filename:
        return None
    return filename


def _resolve_local_gguf_filename(model_id: str, models_dir: Path) -> str | None:
    """Resolve a local GGUF id to the exact on-disk filename.

    Dashboard fallback entries use the file stem as the public id. Preserve
    exact filename case when the extension is `.GGUF` or otherwise mixed-case.
    """
    candidate = _local_gguf_filename_from_id(model_id)
    if not candidate or not models_dir.is_dir():
        return None

    candidate_lower = candidate.lower()
    candidate_stem = Path(candidate).stem.lower()
    exact_matches: list[Path] = []
    stem_matches: list[Path] = []
    try:
        for path in models_dir.iterdir():
            if not path.is_file() or not path.name.lower().endswith(".gguf"):
                continue
            if path.name.lower() == candidate_lower:
                exact_matches.append(path)
            elif path.stem.lower() == candidate_stem:
                stem_matches.append(path)
    except OSError:
        return None

    matches = exact_matches or stem_matches
    if len(matches) == 1:
        return matches[0].name
    if len(matches) > 1:
        logger.warning("Ambiguous local GGUF model id %s matched %s", model_id, [p.name for p in matches])
    return None


def _read_progress_status(service_id: str) -> str | None:
    """Return the ``status`` field of the progress file, or None if absent/unreadable.

    Used by the enable-retry path to detect a prior failed install so the
    host agent can re-run the post_install hook instead of silently calling
    ``docker compose up`` against a half-configured service.
    """
    progress_file = DATA_DIR / "extension-progress" / f"{service_id}.json"
    if not progress_file.exists():
        return None
    try:
        data = json.loads(progress_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    status = data.get("status")
    return status if isinstance(status, str) else None


def _run_post_install_hook(service_id: str, ext_dir: Path) -> tuple[bool, str]:
    """Run an extension's ``post_install`` hook with sandboxed env.

    Shared between the install path (``_handle_install._run_install``) and
    the enable-retry path (``_enable_retry_work``) so both write the same
    progress transitions and use the same env allowlist.

    Returns ``(ok, error_message)``:
    - ``(True, "")`` when no hook is declared OR the hook completes with
      exit code 0. The caller continues with its own next progress write.
    - ``(False, msg)`` when the hook times out or exits non-zero. The
      helper has already written an ``error`` progress entry; the caller
      should abort and NOT overwrite progress.

    Progress writes:
    - ``setup_hook`` ("Running setup...") only when a hook is actually
      resolved — callers must NOT pre-write this message, otherwise the
      "Running setup..." status appears for extensions with no hook.
    - ``error`` on timeout / non-zero exit.
    - On success the helper writes nothing further; the caller proceeds.

    The 8-key env allowlist mirrors ``_execute_hook`` (L1488-1498) to
    keep host-agent secrets out of extension scripts. Stderr is sliced
    tail-500 so the actionable end of the output reaches the dashboard.
    """
    hook_path = _resolve_hook(ext_dir, "post_install")
    if not hook_path:
        return (True, "")

    _write_progress(service_id, "setup_hook", "Running setup...")
    manifest = _read_manifest(ext_dir)
    if manifest is None:
        return False, f"Service manifest is unavailable: {service_id}"
    service_def = manifest.get("service", {})
    if not isinstance(service_def, dict):
        service_def = {}
    hook_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", ""),
        "SERVICE_ID": service_id,
        "SERVICE_PORT": str(service_def.get("port", 0)),
        "SERVICE_DATA_DIR": str(DATA_DIR / service_id),
        "ODS_VERSION": ODS_VERSION,
        "GPU_BACKEND": GPU_BACKEND,
        "HOOK_NAME": "post_install",
    }
    bash = _find_usable_bash()
    if not bash:
        msg = "post_install hook requires a usable Bash runtime. Install Git Bash or run ODS through WSL/Linux."
        _write_progress(service_id, "error", "Setup failed", error=msg)
        return (False, msg)
    try:
        result = subprocess.run(
            [bash, str(hook_path), str(INSTALL_DIR), GPU_BACKEND],
            cwd=str(ext_dir), env=hook_env,
            capture_output=True, text=True,
            timeout=SUBPROCESS_TIMEOUT_START,
        )
    except subprocess.TimeoutExpired:
        msg = f"post_install hook timed out ({SUBPROCESS_TIMEOUT_START}s)"
        _write_progress(service_id, "error", "Setup failed", error=msg)
        return (False, msg)

    if result.returncode != 0:
        msg = (result.stderr or "")[-500:]
        _write_progress(service_id, "error", "Setup failed", error=msg)
        return (False, msg)

    return (True, "")


def _enable_retry_work(service_id: str) -> None:
    """Re-run post_install hook (if declared) then start the service.

    Writes progress transitions (``starting`` → ``setup_hook`` → ``started``/
    ``error``) so the dashboard UI can poll the state of an enable-retry.
    """
    try:
        _write_progress(service_id, "starting", "Retrying after failure...")

        ext_dir = _find_ext_dir(service_id)
        if ext_dir is None:
            _write_progress(service_id, "error", "Retry failed",
                            error=f"Extension directory not found for {service_id}")
            return

        # Re-run the post_install hook when declared. Setup hooks are
        # expected to be idempotent (check-then-create for secrets,
        # env vars, data dirs) so re-running repopulates anything an
        # earlier failed install may have left unset.
        ok, _ = _run_post_install_hook(service_id, ext_dir)
        if not ok:
            return

        _write_progress(service_id, "starting", "Starting container...")
        ok, err = docker_compose_action(service_id, "start")
        if not ok:
            _write_progress(service_id, "error", "Start failed", error=err)
            return

        retry_manifest = _read_manifest(ext_dir)
        retry_service_def = retry_manifest.get("service", {}) if retry_manifest else {}
        if not isinstance(retry_service_def, dict):
            retry_service_def = {}
        container_name = retry_service_def.get("container_name") or f"ods-{service_id}"
        startup_check = retry_service_def.get("startup_check", True)

        if startup_check:
            startup_timeout = retry_service_def.get("startup_timeout", 15)
            deadline = time.monotonic() + startup_timeout
            state: str | None = None
            state_error = ""
            while time.monotonic() < deadline:
                try:
                    inspect_result = subprocess.run(
                        ["docker", "inspect", "--format",
                         "{{.State.Status}}|{{.State.Error}}", container_name],
                        capture_output=True, text=True, timeout=5,
                    )
                except subprocess.TimeoutExpired:
                    inspect_result = None
                if inspect_result is not None and inspect_result.returncode == 0:
                    parts = inspect_result.stdout.strip().split("|", 1)
                    state = parts[0] if parts else ""
                    state_error = parts[1] if len(parts) > 1 else ""
                    if state == "running":
                        break
                time.sleep(1)

            if state != "running":
                msg = f"Container did not reach running state within {startup_timeout}s (state={state or 'unknown'})"
                if state_error:
                    msg += f": {state_error}"
                _write_progress(service_id, "error", "Start failed", error=msg)
                return

        _write_progress(service_id, "started", "Service started")
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        logger.exception("Enable-retry failed for %s", service_id)
        _write_progress(service_id, "error", "Retry failed",
                        error=str(exc)[:500])


def _start_enable_retry(handler, service_id: str, lock: threading.Lock) -> None:
    """Dispatch the enable-retry worker on a daemon thread.

    The caller must hold ``lock``; the thread releases it on exit. Sends
    the 202 response before spawning the thread so the HTTP request
    returns promptly (hook + compose start can take minutes).
    """
    def _thread_target() -> None:
        try:
            _enable_retry_work(service_id)
        finally:
            lock.release()

    try:
        json_response(handler, 202, {"status": "retrying",
                                     "service_id": service_id,
                                     "action": "start"})
        threading.Thread(target=_thread_target, daemon=True).start()
    except Exception:
        lock.release()
        # If 202 was already sent, the dashboard expects a progress
        # transition. Without this, the stale "error" from the prior
        # failed install stays visible. Best-effort write — if progress
        # itself fails, prefer the original exception.
        try:
            _write_progress(service_id, "error", "Retry failed",
                            error="Failed to start retry thread")
        except Exception:
            pass
        raise


def json_response(handler, code: int, body: dict):
    payload = json.dumps(body).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def _split_nmcli_terse(line: str) -> list[str]:
    """Split a `nmcli -t` (terse) line on UNESCAPED colons, then unescape.

    nmcli's terse mode escapes literal colons in values as ``\\:`` (and
    backslashes as ``\\\\``) so the colon delimiter stays unambiguous.
    The naive ``str.split(':')`` corrupts any field containing ':' — and
    SSIDs, security strings, and connection names legally can.

    Reference: ``man 1 nmcli`` — "-t, --terse" describes the escaping.

    Returns the unescaped field list. Empty input → ``[]``.
    """
    if not line:
        return []
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == "\\" and i + 1 < n:
            # Escaped character — consume the next char literally.
            buf.append(line[i + 1])
            i += 2
            continue
        if ch == ":":
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _network_supported(handler) -> bool:
    """Linux + nmcli precondition for Wi-Fi endpoints. Sends a 501 on failure
    so the caller doesn't need to repeat the check; returns True only when
    nmcli is callable.
    """
    if platform.system() != "Linux":
        json_response(handler, 501, {
            "error": f"Wi-Fi management only supported on Linux (this is {platform.system()})",
        })
        return False
    if shutil.which("nmcli") is None:
        json_response(handler, 501, {
            "error": "nmcli not found; install NetworkManager to enable Wi-Fi management",
        })
        return False
    return True


def check_auth(handler) -> bool:
    auth = handler.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        json_response(handler, 401, {"error": "Authorization header required"})
        return False
    if not secrets.compare_digest(auth[7:], AGENT_API_KEY):
        json_response(handler, 403, {"error": "Invalid API key"})
        return False
    return True


def read_json_body(handler) -> dict | None:
    try:
        length = int(handler.headers.get("Content-Length", 0))
    except (ValueError, TypeError):
        json_response(handler, 400, {"error": "Invalid Content-Length"})
        return None
    if length <= 0:
        json_response(handler, 400, {"error": "Request body required"})
        return None
    try:
        return json.loads(handler.rfile.read(min(length, MAX_BODY)))
    except (json.JSONDecodeError, UnicodeDecodeError):
        json_response(handler, 400, {"error": "Invalid JSON"})
        return None


def read_optional_json_body(handler) -> dict | None:
    try:
        length = int(handler.headers.get("Content-Length", 0))
    except (ValueError, TypeError):
        json_response(handler, 400, {"error": "Invalid Content-Length"})
        return None
    if length <= 0:
        return {}
    try:
        data = json.loads(handler.rfile.read(min(length, MAX_BODY)))
    except (json.JSONDecodeError, UnicodeDecodeError):
        json_response(handler, 400, {"error": "Invalid JSON"})
        return None
    if not isinstance(data, dict):
        json_response(handler, 400, {"error": "JSON body must be an object"})
        return None
    return data


def validate_service_id(handler, body: dict) -> str | None:
    sid = body.get("service_id", "")
    if not isinstance(sid, str) or not SERVICE_ID_RE.match(sid):
        json_response(handler, 400, {"error": "Invalid service_id"})
        return None
    if sid in ALWAYS_ON_SERVICES:
        json_response(handler, 403, {"error": f"Cannot manage always-on service: {sid}"})
        return None
    # Verify the service_id maps to an actual installed extension.
    # Check user-extensions first, then built-in extensions.
    ext_dir = USER_EXTENSIONS_DIR / sid
    if not ext_dir.is_dir():
        ext_dir = EXTENSIONS_DIR / sid
    manifest_exists = any((ext_dir / n).exists() for n in ("manifest.yaml", "manifest.yml", "manifest.json"))
    if not ext_dir.is_dir() or not manifest_exists:
        json_response(handler, 404, {"error": f"Extension not found: {sid}"})
        return None
    return sid


def _resolve_container_name(service_id: str) -> str:
    """Resolve actual container name via Docker Compose labels.

    Falls back to ods-{service_id} convention if label lookup fails.
    """
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter",
             f"label=com.docker.compose.service={service_id}",
             "--filter", "label=com.docker.compose.project=ods",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=5,
        )
        names = result.stdout.strip().splitlines()
        if names:
            return names[0]
    except (subprocess.TimeoutExpired, OSError):
        pass
    return f"ods-{service_id}"


def _read_manifest(ext_dir: Path) -> dict | None:
    """Read and return the parsed manifest from an extension directory."""
    for name in ("manifest.yaml", "manifest.yml"):
        candidate = ext_dir / name
        if candidate.exists():
            try:
                import yaml
                manifest = yaml.safe_load(candidate.read_text(encoding="utf-8"))
                if isinstance(manifest, dict):
                    return manifest
            except ImportError:
                logger.error("PyYAML not available on host")
                return None  # no point trying other files without PyYAML
            except (OSError, yaml.YAMLError) as exc:
                logger.warning("Failed to read manifest %s: %s", candidate, exc)
                continue  # try next candidate
    return None


def _validate_hook_path(ext_dir: Path, hook_script: str) -> Path | None:
    """Resolve hook path and verify it stays inside ext_dir."""
    hook_path = (ext_dir / hook_script).resolve()
    try:
        hook_path.relative_to(ext_dir.resolve())
    except ValueError:
        logger.warning("Path traversal attempt in hook for %s: %s", ext_dir.name, hook_script)
        return None
    if not hook_path.is_file():
        return None
    return hook_path


def _resolve_hook(ext_dir: Path, hook_name: str) -> Path | None:
    """Resolve a lifecycle hook script from an extension manifest.

    Checks ``hooks`` map first, falls back to ``setup_hook`` for
    ``post_install`` only.
    """
    manifest = _read_manifest(ext_dir)
    if manifest is None:
        return None
    service_def = manifest.get("service", {})
    if not isinstance(service_def, dict):
        return None

    # Check hooks map first
    hooks = service_def.get("hooks", {})
    if isinstance(hooks, dict):
        hook_script = hooks.get(hook_name, "")
        if isinstance(hook_script, str) and hook_script:
            return _validate_hook_path(ext_dir, hook_script)

    # Fallback: setup_hook -> post_install only
    if hook_name == "post_install":
        setup_hook = service_def.get("setup_hook", "")
        if isinstance(setup_hook, str) and setup_hook:
            return _validate_hook_path(ext_dir, setup_hook)

    return None


def _check_bash_version() -> tuple[bool, str]:
    """On macOS, verify bash >= 4.0. Returns (ok, message)."""
    if platform.system() != "Darwin":
        return True, ""
    try:
        result = subprocess.run(
            ["bash", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        # Parse "GNU bash, version X.Y.Z..."
        import re as _re
        match = _re.search(r"version (\d+)\.(\d+)", result.stdout)
        if match:
            major = int(match.group(1))
            if major < 4:
                return False, f"Bash {match.group(1)}.{match.group(2)} is too old (need 4.0+). Install via: brew install bash"
        return True, ""
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"Could not check bash version: {exc}"


def _find_ext_dir(service_id: str) -> Path | None:
    """Find extension directory for a service_id (user-installed or built-in)."""
    # Check user extensions first
    user_dir = USER_EXTENSIONS_DIR / service_id
    if user_dir.is_dir():
        return user_dir
    # Check built-in extensions
    builtin_dir = EXTENSIONS_DIR / service_id
    if builtin_dir.is_dir():
        return builtin_dir
    return None


def _service_has_docker_container(service_id: str) -> tuple[bool, str]:
    """Return whether service_id maps to a Docker container restart target."""
    ext_dir = _find_ext_dir(service_id)
    if ext_dir is None:
        if service_id in CORE_SERVICE_IDS:
            return True, ""
        return False, f"Service not found: {service_id}"

    manifest = _read_manifest(ext_dir)
    if manifest is None:
        return False, f"Service manifest is unavailable: {service_id}"
    service_def = manifest.get("service", {})
    if not isinstance(service_def, dict):
        return False, f"Service manifest is invalid: {service_id}"
    service_type = service_def.get("type", "docker") or "docker"
    if service_type == "host-systemd":
        return False, f"Service is host-level, not a Docker container: {service_id}"
    if service_type != "docker":
        return False, f"Service type is not Docker: {service_id}"
    container_name = service_def.get("container_name", f"ods-{service_id}")
    if not isinstance(container_name, str) or not container_name.strip():
        return False, f"Service does not declare a Docker container: {service_id}"
    return True, ""


def _is_other_ext_compose(fpath: str, service_id: str, ext_roots: tuple) -> bool:
    """True if fpath points to an extension compose file owned by an
    extension other than service_id. Used to filter `-f` args from the
    install pull command so unrelated extensions' ${VAR:?} guards don't
    abort the pull.
    """
    p = Path(fpath)
    if not p.is_absolute():
        p = INSTALL_DIR / p
    try:
        resolved = p.resolve()
    except OSError:
        return False
    if resolved.parent.name == service_id:
        return False
    for root in ext_roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _narrow_install_pull_flags(flags: list, service_id: str) -> list:
    """Return a filtered copy of `flags` with `-f <path>` pairs pointing
    at OTHER extensions' compose fragments removed. Base compose, GPU
    overlay, and the target extension's own fragments are preserved.
    """
    ext_roots = (EXTENSIONS_DIR.resolve(), USER_EXTENSIONS_DIR.resolve())
    narrowed: list = []
    i = 0
    while i < len(flags):
        if (flags[i] == "-f" and i + 1 < len(flags)
                and _is_other_ext_compose(flags[i + 1], service_id, ext_roots)):
            i += 2
            continue
        narrowed.append(flags[i])
        i += 1
    return narrowed


def _narrowed_compose_set_resolves(narrowed_flags: list, service_id: str,
                                   cwd: str, timeout: int) -> bool:
    """Verify the narrowed compose set parses cleanly and includes the
    target service. Some extensions declare cross-extension `depends_on`
    (e.g. perplexica → searxng); narrowing must fall back to the full
    flag set whenever that drops a referenced service, otherwise
    `docker compose pull` errors with "depends on undefined service".
    """
    try:
        result = subprocess.run(
            ["docker", "compose"] + narrowed_flags + ["config", "--services"],
            cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    return service_id in result.stdout.split()


def _update_status_path() -> Path:
    return INSTALL_DIR / "data" / "update-status.json"


def _write_update_status(status: str, action: str, **fields) -> None:
    with _update_status_lock:
        path = _update_status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": status,
            "action": action,
            "updated_at": _iso_now(),
            **fields,
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)


def _read_update_status() -> dict:
    with _update_status_lock:
        path = _update_status_path()
        if not path.exists():
            return {"status": "idle"}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "unknown", "error": "could not read update status"}
    return data if isinstance(data, dict) else {"status": "unknown"}


def _fail_stale_update_status(data: dict) -> dict:
    """Convert a non-live queued/running update record into a terminal failure."""
    if data.get("status") not in {"queued", "running"}:
        return data

    action = data.get("action")
    if not isinstance(action, str) or not action:
        action = "update"

    fields = {
        key: value
        for key, value in data.items()
        if key not in {"status", "action", "updated_at", "error", "finished_at"}
    }
    fields["error"] = data.get("error") or "Update process exited before reporting completion."
    fields["finished_at"] = _iso_now()
    _write_update_status("failed", action, **fields)
    return _read_update_status()


def _find_update_script() -> Path | None:
    for candidate in (
        INSTALL_DIR / "ods-update.sh",
        INSTALL_DIR / "scripts" / "ods-update.sh",
        INSTALL_DIR.parent / "scripts" / "ods-update.sh",
    ):
        if candidate.exists():
            return candidate
    return None


def _find_update_bash() -> str | None:
    global _update_usable_bash
    if isinstance(_update_usable_bash, str):
        return _update_usable_bash
    if _update_usable_bash is False:
        return None

    bash = _find_usable_bash()
    _update_usable_bash = bash if bash else False
    return bash


def _update_command(script_path: Path, *args: str) -> list[str]:
    if platform.system() != "Windows":
        return [str(script_path), *args]
    bash = _find_update_bash()
    if not bash:
        raise RuntimeError(
            "Update actions require a usable Bash runtime on Windows. "
            "Install Git Bash or run ODS through WSL/Linux."
        )
    return [bash, _to_bash_path(script_path), *args]


def _run_update_script(action: str, *args: str, timeout: int | None) -> subprocess.CompletedProcess:
    script = _find_update_script()
    if script is None:
        raise FileNotFoundError("ods-update.sh not found")
    return subprocess.run(
        _update_command(script, action, *args),
        cwd=str(INSTALL_DIR),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class AgentHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.info(fmt, *args)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            json_response(self, 200, {"status": "ok", "version": VERSION})
        elif path == "/v1/service/stats":
            self._handle_service_stats()
        elif path == "/v1/model/list":
            self._handle_model_list()
        elif path == "/v1/model/status":
            self._handle_model_status()
        elif path == "/v1/network/wifi-scan":
            self._handle_network_wifi_scan()
        elif path == "/v1/network/status":
            self._handle_network_status()
        elif path == "/v1/tailscale/status":
            self._handle_tailscale_status()
        elif path == "/v1/ap-mode/status":
            self._handle_ap_mode_status()
        elif path == "/v1/update/status":
            self._handle_update_status()
        elif path == "/v1/host/port":
            self._handle_host_port_status(parse_qs(parsed.query))
        else:
            json_response(self, 404, {"error": "Not found"})

    def _handle_host_port_status(self, query: dict[str, list[str]]):
        """Return whether a host-local TCP port is reachable.

        Dashboard-api runs in Docker, so it cannot reliably probe services that
        intentionally bind to the host loopback interface (for example
        OpenCode). Keep this endpoint local-only to avoid turning the host-agent
        into a network scanner.
        """
        if not check_auth(self):
            return

        host = (query.get("host") or ["127.0.0.1"])[0]
        if host not in {"127.0.0.1", "localhost", "::1"}:
            json_response(self, 400, {"error": "host must be loopback"})
            return

        try:
            port = int((query.get("port") or [""])[0])
        except ValueError:
            json_response(self, 400, {"error": "port must be an integer"})
            return
        if port < 1 or port > 65535:
            json_response(self, 400, {"error": "port out of range"})
            return

        started = time.monotonic()
        reachable = False
        error = ""
        try:
            with socket.create_connection((host, port), timeout=2):
                reachable = True
        except OSError as exc:
            error = str(exc)

        payload = {
            "host": host,
            "port": port,
            "reachable": reachable,
            "response_time_ms": round((time.monotonic() - started) * 1000, 1),
        }
        if error:
            payload["error"] = error[:200]
        json_response(self, 200, payload)

    def _write_tailscale_status_payload(self, payload: dict, source: str):
        """Distill `tailscale status --json` into the dashboard response."""
        self_node = payload.get("Self", {}) or {}
        magic_dns = payload.get("MagicDNSSuffix") or ""
        dns_name = self_node.get("DNSName", "").rstrip(".") or None
        tailnet = payload.get("CurrentTailnet")
        tailnet_name = (
            tailnet.get("Name") if isinstance(tailnet, dict) else None
        )
        json_response(self, 200, {
            "running": True,
            "authenticated": payload.get("BackendState") == "Running",
            "backend_state": payload.get("BackendState"),
            "source": source,
            "self": {
                "hostname": self_node.get("HostName"),
                "dns_name": dns_name,
                "ips": self_node.get("TailscaleIPs", []),
                "online": self_node.get("Online", False),
            },
            "magic_dns_suffix": magic_dns,
            "tailnet_name": tailnet_name,
        })

    def _find_native_tailscale_cli(self) -> str | None:
        """Return a host-native tailscale CLI path if one is installed."""
        tailscale = shutil.which("tailscale")
        if tailscale:
            return tailscale
        if platform.system() == "Windows":
            for base in (
                os.environ.get("ProgramFiles"),
                os.environ.get("ProgramFiles(x86)"),
            ):
                if not base:
                    continue
                candidate = Path(base) / "Tailscale" / "tailscale.exe"
                if candidate.exists():
                    return str(candidate)
        return None

    def _try_native_tailscale_status(self) -> bool:
        """Return True after writing a response from host-native Tailscale."""
        tailscale = self._find_native_tailscale_cli()
        if not tailscale:
            return False
        try:
            result = subprocess.run(
                [tailscale, "status", "--json"],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError):
            return False

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            lowered = stderr.lower()
            if "logged out" in lowered or "needs login" in lowered:
                json_response(self, 200, {
                    "running": True,
                    "authenticated": False,
                    "source": "native",
                    "reason": "Native Tailscale is installed but not authenticated.",
                })
                return True
            return False

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False

        self._write_tailscale_status_payload(payload, "native")
        return True

    def _handle_tailscale_status(self):
        """Return Tailscale daemon status from ODS's container or the host.

        Three outcome shapes:
          1. Tailscale running AND authenticated:
             {running:true, authenticated:true, self:{...},
              magic_dns_suffix:"tail-xxxxx.ts.net", source:"..."}
          2. Tailscale running but not authenticated (auth key absent,
             rejected, or host app logged out):
             {running:true, authenticated:false, reason:"..."}
          3. ODS container and host-native Tailscale are not running:
             {running:false}

        We never return 5xx for "container not running" — that's a normal
        state. 5xx is reserved for "the docker daemon itself broke."
        """
        if not check_auth(self):
            return
        try:
            result = subprocess.run(
                ["docker", "exec", "ods-tailscale",
                 "tailscale", "status", "--json"],
                capture_output=True, text=True, timeout=10,
            )
        except subprocess.TimeoutExpired:
            json_response(self, 504, {"error": "docker exec timed out"})
            return
        except OSError as exc:
            json_response(self, 500, {"error": f"docker exec failed: {exc}"})
            return

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            lowered = stderr.lower()
            # Container not running -> try host-native Tailscale first. This
            # covers Windows/macOS installs where users already run Tailscale
            # outside Docker; absent both, it remains a normal "not enabled
            # yet" state.
            if "no such container" in lowered or "is not running" in lowered:
                if self._try_native_tailscale_status():
                    return
                json_response(self, 200, {"running": False})
                return
            # Container up but daemon not yet authed.
            if "logged out" in lowered or "needs login" in lowered:
                json_response(self, 200, {
                    "running": True,
                    "authenticated": False,
                    "reason": "Tailscale is running but not yet authenticated. Set TS_AUTHKEY and restart.",
                })
                return
            json_response(self, 200, {
                "running": True,
                "authenticated": False,
                "error": stderr[:300] or "tailscale status returned non-zero",
            })
            return

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            json_response(self, 500, {"error": f"could not parse tailscale status: {exc}"})
            return

        self._write_tailscale_status_payload(payload, "container")

    def _handle_ap_mode_status(self):
        """Read-only AP-mode status snapshot.

        Reads /run/ods-ap-mode/state.json which ap-mode.sh writes
        when the AP is up. Returns {"status": "inactive"} if the file
        doesn't exist. NEVER enables or disables AP mode itself —
        toggling is operator-only via systemctl, by design (turning
        on an AP from an HTTP endpoint is a great way to lock yourself
        out of a remote box).
        """
        if not check_auth(self):
            return
        state_path = Path("/run/ods-ap-mode/state.json")
        if not state_path.exists():
            json_response(self, 200, {"status": "inactive"})
            return
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            json_response(self, 503, {
                "status": "unknown",
                "error": f"could not read AP state file: {exc}",
            })
            return
        json_response(self, 200, data)

    def _handle_service_stats(self):
        """Return CPU/memory stats for all ODS-managed containers."""
        if not check_auth(self):
            return

        try:
            result = subprocess.run(
                ["docker", "stats", "--no-stream",
                 "--format", '{"name":"{{.Name}}","cpu":"{{.CPUPerc}}","mem_usage":"{{.MemUsage}}","mem_percent":"{{.MemPerc}}","pids":"{{.PIDs}}"}'],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                logger.warning("docker stats returned non-zero: %s", result.stderr[:200] if result.stderr else "")

            containers = []
            for line in result.stdout.strip().splitlines():
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue

                name = raw.get("name", "")
                if not name.startswith("ods-"):
                    continue

                cpu_str = raw.get("cpu", "0%").rstrip("%")
                try:
                    cpu_percent = float(cpu_str)
                except ValueError:
                    cpu_percent = 0.0

                mem_parts = raw.get("mem_usage", "0B / 0B").split("/")
                mem_used_mb = _parse_mem_value(mem_parts[0].strip()) if len(mem_parts) >= 1 else 0
                mem_limit_mb = _parse_mem_value(mem_parts[1].strip()) if len(mem_parts) >= 2 else 0

                mem_pct_str = raw.get("mem_percent", "0%").rstrip("%")
                try:
                    mem_percent = float(mem_pct_str)
                except ValueError:
                    mem_percent = 0.0

                service_id = name.removeprefix("ods-")

                try:
                    pids = int(raw.get("pids", "0") or "0")
                except (ValueError, TypeError):
                    pids = 0

                containers.append({
                    "service_id": service_id,
                    "container_name": name,
                    "cpu_percent": round(cpu_percent, 1),
                    "memory_used_mb": round(mem_used_mb),
                    "memory_limit_mb": round(mem_limit_mb),
                    "memory_percent": round(mem_percent, 1),
                    "pids": pids,
                })

            json_response(self, 200, {
                "containers": containers,
                "timestamp": _iso_now(),
            })
        except subprocess.TimeoutExpired:
            json_response(self, 503, {"error": "docker stats timed out"})
        except Exception as exc:
            json_response(self, 500, {"error": f"Failed to fetch stats: {exc}"})

    def do_POST(self):
        if self.path in ("/v1/extension/start", "/v1/extension/stop"):
            action = "start" if self.path.endswith("/start") else "stop"
            self._handle_extension(action)
        elif self.path == "/v1/core/recreate":
            self._handle_core_recreate()
        elif self.path == "/v1/extension/logs":
            self._handle_logs()
        elif self.path == "/v1/extension/install":
            self._handle_install()
        elif self.path == "/v1/extension/setup-hook":
            self._handle_setup_hook()
        elif self.path == "/v1/extension/hooks":
            self._handle_hook()
        elif self.path == "/v1/extension/activate":
            self._handle_extension_compose_toggle(activate=True)
        elif self.path == "/v1/extension/deactivate":
            self._handle_extension_compose_toggle(activate=False)
        elif self.path == "/v1/extension/sync_config":
            self._handle_extension_sync_config()
        elif self.path == "/v1/service/logs":
            self._handle_service_logs()
        elif self.path == "/v1/service/restart":
            self._handle_service_restart()
        elif self.path == "/v1/model/download":
            self._handle_model_download()
        elif self.path == "/v1/model/download/cancel":
            self._handle_model_download_cancel()
        elif self.path == "/v1/model/activate":
            self._handle_model_activate()
        elif self.path == "/v1/model/delete":
            self._handle_model_delete()
        elif self.path == "/v1/compose/invalidate-cache":
            self._handle_invalidate_compose_cache()
        elif self.path == "/v1/env/update":
            self._handle_env_update()
        elif self.path in ("/v1/update/check", "/v1/update/backup", "/v1/update/start"):
            self._handle_update_action()
        elif self.path == "/v1/network/wifi-connect":
            self._handle_network_wifi_connect()
        elif self.path == "/v1/network/wifi-forget":
            self._handle_network_wifi_forget()
        else:
            json_response(self, 404, {"error": "Not found"})

    def _handle_invalidate_compose_cache(self):
        """Drop the .compose-flags cache file so the next CLI call re-resolves it."""
        if not check_auth(self):
            return
        invalidate_compose_cache()
        logger.info("compose-flags cache invalidated")
        json_response(self, 200, {"status": "ok"})

    def _handle_update_status(self):
        """Return the last host-agent managed update run status."""
        if not check_auth(self):
            return
        data = _read_update_status()
        with _update_lock:
            running = _update_thread is not None and _update_thread.is_alive()
        if running:
            data = {**data, "status": "running"}
        else:
            data = _fail_stale_update_status(data)
        json_response(self, 200, data)

    def _handle_update_action(self):
        """Run ods-update.sh from the host-agent trust boundary."""
        if not check_auth(self):
            return
        body = read_optional_json_body(self)
        if body is None:
            return

        endpoint_action = self.path.rsplit("/", 1)[-1]
        if endpoint_action == "check":
            self._handle_update_check()
        elif endpoint_action == "backup":
            backup_id = body.get("backup_id")
            if not isinstance(backup_id, str) or not backup_id.strip():
                backup_id = f"dashboard-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            self._handle_update_backup(backup_id.strip())
        elif endpoint_action == "start":
            self._handle_update_start()
        else:
            json_response(self, 404, {"error": "Not found"})

    def _handle_update_check(self):
        try:
            result = _run_update_script("check", timeout=30)
        except FileNotFoundError:
            json_response(self, 501, {"error": "Update system not installed."})
            return
        except RuntimeError as exc:
            json_response(self, 501, {"error": str(exc)})
            return
        except subprocess.TimeoutExpired:
            json_response(self, 504, {"error": "Update check timed out"})
            return
        except OSError as exc:
            json_response(self, 500, {"error": f"Update check failed: {exc}"})
            return

        output = (result.stdout or "") + (result.stderr or "")
        json_response(self, 200, {
            "success": result.returncode in (0, 2),
            "update_available": result.returncode == 2,
            "returncode": result.returncode,
            "output": output,
        })

    def _handle_update_backup(self, backup_id: str):
        try:
            result = _run_update_script("backup", backup_id, timeout=60)
        except FileNotFoundError:
            json_response(self, 501, {"error": "Update system not installed."})
            return
        except RuntimeError as exc:
            json_response(self, 501, {"error": str(exc)})
            return
        except subprocess.TimeoutExpired:
            json_response(self, 504, {"error": "Backup timed out"})
            return
        except OSError as exc:
            json_response(self, 500, {"error": f"Backup failed: {exc}"})
            return

        output = (result.stdout or "") + (result.stderr or "")
        json_response(self, 200, {
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "output": output,
        })

    def _handle_update_start(self):
        global _update_thread

        with _update_lock:
            if _update_thread is not None and _update_thread.is_alive():
                json_response(self, 409, {
                    "success": False,
                    "status": "running",
                    "message": "Update already running",
                })
                return

            _write_update_status("queued", "update", started_at=_iso_now())

            def _run_background_update():
                try:
                    _write_update_status("running", "update", started_at=_iso_now())
                    result = _run_update_script("update", timeout=3600)
                    output = ((result.stdout or "") + (result.stderr or ""))[-8000:]
                    _write_update_status(
                        "succeeded" if result.returncode == 0 else "failed",
                        "update",
                        returncode=result.returncode,
                        output_tail=output,
                        finished_at=_iso_now(),
                    )
                except FileNotFoundError:
                    _write_update_status(
                        "failed", "update",
                        error="Update system not installed.",
                        finished_at=_iso_now(),
                    )
                except RuntimeError as exc:
                    _write_update_status("failed", "update", error=str(exc), finished_at=_iso_now())
                except subprocess.TimeoutExpired:
                    _write_update_status("failed", "update", error="Update timed out", finished_at=_iso_now())
                except OSError as exc:
                    _write_update_status(
                        "failed", "update",
                        error=f"Update failed: {exc}",
                        finished_at=_iso_now(),
                    )
                except Exception as exc:
                    logger.exception("Unhandled update failure")
                    _write_update_status(
                        "failed", "update",
                        error=f"Update failed unexpectedly: {exc}",
                        finished_at=_iso_now(),
                    )

            _update_thread = threading.Thread(target=_run_background_update, daemon=True)
            _update_thread.start()

        json_response(self, 202, {
            "success": True,
            "status": "started",
            "message": "Update started in background. Check update status for progress.",
        })

    # ------------------------------------------------------------------
    # Wi-Fi / network management (Linux + NetworkManager only)
    # ------------------------------------------------------------------
    #
    # These endpoints back the first-boot wizard's "join a network" step.
    # Linux + nmcli is the only supported path today; macOS and Windows
    # return 501 with a clear platform message so the wizard can fall
    # back to "use ethernet / configure manually" without crashing.
    #
    # Security:
    #   * Wi-Fi passwords are NEVER logged. Only the SSID and "password set"
    #     boolean go to logs.
    #   * Passwords pass through argv to nmcli. On modern Linux with
    #     `kernel.yama.ptrace_scope >= 1` (default on Ubuntu/Fedora) and
    #     the host-agent running as root, only root processes can see the
    #     cmdline — that's an acceptable v1 posture. Hardening this further
    #     (`nmcli con add` + secrets file) is a follow-up.
    #   * SSID is rejected if it contains control characters; nmcli's own
    #     argv parsing handles spaces and most special characters fine.

    def _handle_network_wifi_scan(self):
        if not check_auth(self):
            return
        if not _network_supported(self):
            return
        # Best-effort rescan — fresh networks take 5-10s to populate. We
        # tolerate the rescan failing (e.g. radio off) and read whatever
        # cached list nmcli has.
        try:
            subprocess.run(
                ["nmcli", "device", "wifi", "rescan"],
                capture_output=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass

        try:
            # NOTE: we deliberately do NOT pass `-e no`. With escaping enabled
            # (the nmcli default in -t mode), nmcli backslash-escapes any
            # colons that appear inside field values (e.g. an SSID called
            # "Cafe:Lounge" comes back as "Cafe\:Lounge"). We then split on
            # *unescaped* colons via _split_nmcli_terse() and un-escape each
            # part. Disabling escaping with `-e no` corrupts the parse for
            # any SSID, security name, or connection name containing ':'.
            result = subprocess.run(
                ["nmcli", "-t", "-f",
                 "SSID,SIGNAL,SECURITY,IN-USE", "device", "wifi", "list"],
                capture_output=True, text=True, timeout=15,
            )
        except subprocess.TimeoutExpired:
            json_response(self, 504, {"error": "nmcli wifi list timed out"})
            return
        except OSError as exc:
            json_response(self, 500, {"error": f"nmcli failed: {exc}"})
            return

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()[:200]
            json_response(self, 503, {"error": stderr or "nmcli wifi list failed"})
            return

        networks_by_ssid = {}
        for line in result.stdout.splitlines():
            # Format: SSID:SIGNAL:SECURITY:IN-USE (IN-USE is empty or "*")
            parts = _split_nmcli_terse(line)
            if len(parts) < 4:
                continue
            ssid, signal_str, security, in_use_str = parts[0], parts[1], parts[2], parts[3]
            if not ssid:
                continue
            try:
                signal_pct = int(signal_str)
            except (ValueError, TypeError):
                signal_pct = 0
            existing = networks_by_ssid.get(ssid)
            if existing and existing["signal"] >= signal_pct:
                continue
            # nmcli sometimes returns multiple rows per SSID (one per BSSID).
            # Collapse on SSID and keep the strongest signal observed.
            networks_by_ssid[ssid] = {
                "ssid": ssid,
                "signal": signal_pct,
                "security": security or "open",
                "in_use": in_use_str == "*",
            }

        # Strongest signal first — that's the order the wizard wants to display.
        networks = list(networks_by_ssid.values())
        networks.sort(key=lambda n: -n["signal"])
        json_response(self, 200, {"networks": networks})

    def _handle_network_wifi_connect(self):
        if not check_auth(self):
            return
        if not _network_supported(self):
            return
        body = read_json_body(self)
        if body is None:
            return

        ssid = body.get("ssid", "")
        password = body.get("password", "")

        if not isinstance(ssid, str) or not ssid or len(ssid) > 32:
            json_response(self, 400, {"error": "ssid must be 1-32 chars"})
            return
        if any(c in ssid for c in ("\n", "\r", "\0")):
            json_response(self, 400, {"error": "ssid contains invalid characters"})
            return
        if not isinstance(password, str) or len(password) > 63:
            # WPA2 PSK max is 63 chars. Open networks pass empty string.
            json_response(self, 400, {"error": "password must be 0-63 chars"})
            return
        if any(c in password for c in ("\n", "\r", "\0")):
            json_response(self, 400, {"error": "password contains invalid characters"})
            return

        logger.info(
            "wifi-connect ssid=%s password_set=%s", ssid, bool(password)
        )

        args = ["nmcli", "device", "wifi", "connect", ssid]
        if password:
            args += ["password", password]

        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=45,
            )
        except subprocess.TimeoutExpired:
            json_response(self, 504, {"error": "Connection attempt timed out"})
            return
        except OSError as exc:
            json_response(self, 500, {"error": f"nmcli failed: {exc}"})
            return

        if result.returncode != 0:
            # nmcli errors don't echo the password. Map common ones to
            # something the wizard can show without leaking internals.
            raw = (result.stderr or result.stdout or "").strip()[:300]
            lowered = raw.lower()
            if "secrets were required" in lowered or "(7)" in raw:
                err_msg = "Wrong password"
            elif "no network with ssid" in lowered or "not found" in lowered:
                err_msg = "Network not found"
            elif "timeout" in lowered:
                err_msg = "Connection timed out"
            else:
                err_msg = raw or "Connection failed"
            json_response(self, 400, {
                "error": err_msg, "code": result.returncode,
            })
            return

        json_response(self, 200, {"success": True, "ssid": ssid})

    def _handle_network_wifi_forget(self):
        """Delete a saved NetworkManager connection profile by name.

        Hard-gated to Wi-Fi profiles only. The endpoint name is "wifi-forget"
        and that's all it should do — we MUST NOT delete wired / VPN / bridge /
        bond / tun profiles even if the caller passes their names, because
        that's a great way to cut off the host's connectivity. We resolve
        the profile's TYPE field first via `nmcli connection show` and only
        proceed when type starts with "802-11-wireless".
        """
        if not check_auth(self):
            return
        if not _network_supported(self):
            return
        body = read_json_body(self)
        if body is None:
            return

        connection = body.get("connection", "")
        if not isinstance(connection, str) or not connection or len(connection) > 64:
            json_response(self, 400, {"error": "connection must be 1-64 chars"})
            return
        if any(c in connection for c in ("\n", "\r", "\0")):
            json_response(self, 400, {"error": "connection contains invalid characters"})
            return

        # Step 1: resolve and verify this is a Wi-Fi profile. Use -t for
        # terse output and -f to limit fields; we still split on the FIRST
        # colon only so a value containing ':' doesn't fool the parser.
        try:
            check = subprocess.run(
                ["nmcli", "-t", "-f", "connection.type", "connection", "show", connection],
                capture_output=True, text=True, timeout=10,
            )
        except subprocess.TimeoutExpired:
            json_response(self, 504, {"error": "nmcli show timed out"})
            return
        except OSError as exc:
            json_response(self, 500, {"error": f"nmcli failed: {exc}"})
            return

        if check.returncode != 0:
            stderr = (check.stderr or "").strip()[:200]
            # 404 if the profile doesn't exist; 400 for other errors.
            if "no such" in stderr.lower() or "unknown" in stderr.lower() or "not found" in stderr.lower():
                json_response(self, 404, {"error": f"No such connection: {connection}"})
            else:
                json_response(self, 400, {"error": stderr or "Failed to inspect connection"})
            return

        # Parse "connection.type:802-11-wireless" — split on the FIRST ':' only
        # so a connection name containing ':' (unusual but legal) doesn't
        # confuse the result.
        ctype_line = (check.stdout or "").strip()
        _, _, ctype = ctype_line.partition(":")
        ctype = ctype.strip().lower()
        if not ctype.startswith("802-11-wireless"):
            json_response(self, 400, {
                "error": (
                    f"Refusing to delete non-Wi-Fi connection '{connection}' "
                    f"(type='{ctype or 'unknown'}'). The wifi-forget endpoint "
                    "only deletes Wi-Fi profiles; use nmcli directly for other types."
                ),
            })
            return

        # Step 2: type-confirmed Wi-Fi → safe to delete.
        try:
            result = subprocess.run(
                ["nmcli", "connection", "delete", connection],
                capture_output=True, text=True, timeout=15,
            )
        except subprocess.TimeoutExpired:
            json_response(self, 504, {"error": "nmcli delete timed out"})
            return
        except OSError as exc:
            json_response(self, 500, {"error": f"nmcli failed: {exc}"})
            return

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()[:200]
            json_response(self, 400, {"error": stderr or "Forget failed"})
            return

        json_response(self, 200, {"success": True, "connection": connection})

    def _handle_network_status(self):
        if not check_auth(self):
            return
        if platform.system() != "Linux":
            json_response(self, 200, {
                "platform_supported": False,
                "platform": platform.system(),
                "reason": "Wi-Fi management requires Linux + NetworkManager",
            })
            return
        if shutil.which("nmcli") is None:
            json_response(self, 200, {
                "platform_supported": False,
                "reason": "nmcli not installed",
            })
            return

        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"],
                capture_output=True, text=True, timeout=5,
            )
        except subprocess.TimeoutExpired:
            json_response(self, 504, {"error": "nmcli timed out"})
            return
        except OSError as exc:
            json_response(self, 500, {"error": f"nmcli failed: {exc}"})
            return

        if result.returncode != 0:
            stderr = (result.stderr or result.stdout or "").strip()[:200]
            json_response(self, 200, {
                "platform_supported": False,
                "reason": stderr or "nmcli device status failed",
            })
            return

        devices = []
        wifi_connected = False
        for line in result.stdout.splitlines():
            # See _split_nmcli_terse — connection names containing ':' come
            # through as '\:' under default `nmcli -t` escaping; naive
            # str.split(':') would corrupt them.
            parts = _split_nmcli_terse(line)
            if len(parts) < 4:
                continue
            device, typ, state, connection = parts[0], parts[1], parts[2], parts[3]
            if state != "connected":
                continue
            ip_addr = ""
            gateway = ""
            try:
                ip_result = subprocess.run(
                    ["nmcli", "-t", "-f", "IP4.ADDRESS,IP4.GATEWAY",
                     "device", "show", device],
                    capture_output=True, text=True, timeout=5,
                )
                for ip_line in ip_result.stdout.splitlines():
                    if ip_line.startswith("IP4.ADDRESS"):
                        _, _, val = ip_line.partition(":")
                        ip_addr = val.split("/")[0]
                    elif ip_line.startswith("IP4.GATEWAY"):
                        _, _, val = ip_line.partition(":")
                        gateway = val
            except (subprocess.TimeoutExpired, OSError):
                pass

            devices.append({
                "device": device,
                "type": typ,
                "state": state,
                "connection": connection,
                "ip": ip_addr,
                "gateway": gateway,
            })
            if typ == "wifi":
                wifi_connected = True

        json_response(self, 200, {
            "platform_supported": True,
            "devices": devices,
            "wifi_connected": wifi_connected,
        })

    def _handle_env_update(self):
        """Write a validated .env file. Dashboard-api delegates here because the
        container mount is :ro — only the host agent may write secrets to disk.

        Bypasses read_json_body() because the default 16 KB body limit truncates
        real .env files (.env.example alone is ~11 KB)."""
        if not check_auth(self):
            return

        client_ip = self.client_address[0] if hasattr(self, "client_address") else "?"
        MAX_ENV_BODY = 65536  # env files routinely exceed the default 16 KB cap

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            logger.warning("env_update rejected: invalid Content-Length from %s", client_ip)
            json_response(self, 400, {"error": "Invalid Content-Length"})
            return
        if length <= 0:
            logger.warning("env_update rejected: empty body from %s", client_ip)
            json_response(self, 400, {"error": "Empty body"})
            return
        if length > MAX_ENV_BODY:
            logger.warning("env_update rejected: body too large (%d bytes) from %s", length, client_ip)
            json_response(self, 413, {"error": f"Body too large: {length} > {MAX_ENV_BODY}"})
            return
        try:
            raw = self.rfile.read(length)
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("env_update rejected: invalid JSON from %s: %s", client_ip, exc)
            json_response(self, 400, {"error": f"Invalid JSON: {exc}"})
            return

        raw_text = body.get("raw_text")
        if not isinstance(raw_text, str) or not raw_text.strip():
            logger.warning("env_update rejected: raw_text missing/empty from %s", client_ip)
            json_response(self, 400, {"error": "raw_text required"})
            return
        backup = body.get("backup", True)

        schema_path = INSTALL_DIR / ".env.schema.json"
        if not schema_path.exists():
            logger.warning("env_update rejected: schema missing at %s (request from %s)", schema_path, client_ip)
            json_response(self, 500, {"error": f".env.schema.json not found at {schema_path}"})
            return
        try:
            with open(schema_path, encoding="utf-8") as f:
                schema = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("env_update rejected: failed to read schema (request from %s): %s", client_ip, exc)
            json_response(self, 500, {"error": f"Failed to read .env.schema.json: {exc}"})
            return
        allowed_keys = set(schema.get("properties", {}).keys())

        for line in raw_text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                logger.warning("env_update rejected: malformed line %r from %s", stripped[:80], client_ip)
                json_response(self, 400, {"error": f"Malformed line: {stripped[:80]}"})
                return
            key, _, value = stripped.partition("=")
            key = key.strip()
            if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', key):
                logger.warning("env_update rejected: invalid key name %r from %s", key[:40], client_ip)
                json_response(self, 400, {"error": f"Invalid key name: {key[:40]}"})
                return
            if key not in allowed_keys:
                # Warn but accept — extension install hooks and GPU pinning write
                # keys that are not in the core schema (e.g. JWT_SECRET from
                # LibreChat, COMFYUI_GPU_UUID from the installer).  Rejecting
                # them breaks the dashboard Settings save for any install that
                # has ever enabled an extension.
                logger.info("env_update: non-schema key %r from %s (accepted)", key, client_ip)
            # Defense in depth: reject values containing control chars (null bytes,
            # escape sequences, etc.). splitlines() already consumed \n/\r/\u2028/\u2029;
            # this catches the residual edge cases flagged by security review.
            if any(ord(c) < 32 and c != "\t" for c in value):
                logger.warning("env_update rejected: control char in value for key %r from %s", key, client_ip)
                json_response(self, 400, {"error": f"Value contains control characters for key: {key}"})
                return

        # Coordinate with model activation, which also writes .env under this lock.
        if not _model_activate_lock.acquire(blocking=False):
            logger.warning("env_update rejected: lock contention from %s", client_ip)
            json_response(self, 409, {"error": "Model activation or another env update in progress; try again shortly"})
            return

        env_path = INSTALL_DIR / ".env"
        backup_relative_path = None
        try:
            if backup and env_path.exists():
                backup_dir = DATA_DIR / "config-backups"
                backup_dir.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                backup_path = backup_dir / f".env.backup.{timestamp}"
                shutil.copy2(env_path, backup_path)
                backup_relative_path = f"data/{backup_path.relative_to(DATA_DIR).as_posix()}"

            payload_text = raw_text if raw_text.endswith("\n") else raw_text + "\n"
            tmp_path = env_path.with_name(".env.tmp")
            tmp_path.write_text(payload_text, encoding="utf-8")
            os.replace(str(tmp_path), str(env_path))
        except OSError as exc:
            logger.warning("env_update OSError from %s: %s", client_ip, exc)
            json_response(self, 500, {"error": str(exc)})
            return
        finally:
            _model_activate_lock.release()

        logger.info(".env updated via host agent from %s (backup=%s)", client_ip, backup_relative_path or "none")
        json_response(self, 200, {"status": "ok", "backup_path": backup_relative_path})

    def _handle_core_recreate(self):
        if not check_auth(self):
            return
        body = read_json_body(self)
        if body is None:
            return

        requested = body.get("service_ids", [])
        unique_service_ids = sorted(set(requested)) if isinstance(requested, list) else requested
        ok, error = validate_core_recreate_ids(unique_service_ids)
        if not ok:
            json_response(self, 400, {"error": error})
            return

        locks = []
        try:
            for service_id in unique_service_ids:
                lock = _service_locks[service_id]
                if not lock.acquire(blocking=False):
                    json_response(self, 409, {"error": f"Operation already in progress for {service_id}"})
                    return
                locks.append(lock)

            logger.info("Recreating core services: %s", ", ".join(unique_service_ids))
            ok, err = docker_compose_recreate(unique_service_ids)
            if ok:
                json_response(self, 200, {
                    "status": "ok",
                    "action": "recreate",
                    "service_ids": unique_service_ids,
                })
            else:
                json_response(self, 503 if "timed out" in err else 500, {"error": err})
        except RuntimeError as exc:
            json_response(self, 500, {"error": str(exc)})
        except subprocess.CalledProcessError as exc:
            json_response(self, 500, {"error": f"Compose resolution failed: {exc.stderr[:300]}"})
        finally:
            for lock in reversed(locks):
                lock.release()

    def _handle_extension(self, action: str):
        if not check_auth(self):
            return
        body = read_json_body(self)
        if body is None:
            return
        service_id = validate_service_id(self, body)
        if service_id is None:
            return
        logger.info("%s extension: %s", action, service_id)
        lock = _service_locks[service_id]
        if not lock.acquire(blocking=False):
            json_response(self, 409, {"error": f"Operation already in progress for {service_id}"})
            return

        # Enable-retry path: if a prior install left progress status=error,
        # "start" must re-run the post_install hook (if declared) and write
        # progress updates — otherwise the UI stays stuck on the old error and
        # env vars populated by the hook never get regenerated. Hook + start
        # can take minutes, so mirror _handle_install's 202-accept-then-thread
        # pattern. Non-retry start/stop keeps the existing synchronous path.
        if action == "start" and _read_progress_status(service_id) == "error":
            _start_enable_retry(self, service_id, lock)
            return

        try:
            ok, err = docker_compose_action(service_id, action)
        except RuntimeError as exc:
            json_response(self, 500, {"error": str(exc)})
            return
        except subprocess.CalledProcessError as exc:
            json_response(self, 500, {"error": f"Compose resolution failed: {exc.stderr[:300]}"})
            return
        finally:
            lock.release()
        if ok:
            json_response(self, 200, {"status": "ok", "service_id": service_id, "action": action})
        else:
            json_response(self, 503 if "timed out" in err else 500, {"error": err})

    def _handle_extension_compose_toggle(self, activate: bool):
        """Rename compose.yaml.disabled <-> compose.yaml for an extension.

        Used by dashboard-api when the extensions mount is read-only (:ro).
        The host agent runs on the host filesystem where the files are writable.
        """
        if not check_auth(self):
            return
        body = read_json_body(self)
        if body is None:
            return

        # Validate service_id format and existence
        sid = body.get("service_id", "")
        if not isinstance(sid, str) or not SERVICE_ID_RE.match(sid):
            json_response(self, 400, {"error": "Invalid service_id"})
            return

        ext_dir = _find_ext_dir(sid)
        if ext_dir is None:
            json_response(self, 404, {"error": f"Extension not found: {sid}"})
            return

        if sid in ALWAYS_ON_SERVICES:
            json_response(self, 403, {"error": f"Cannot modify always-on service: {sid}"})
            return

        action = "activate" if activate else "deactivate"
        if activate:
            src = ext_dir / "compose.yaml.disabled"
            dst = ext_dir / "compose.yaml"
        else:
            src = ext_dir / "compose.yaml"
            dst = ext_dir / "compose.yaml.disabled"

        lock = _service_locks[sid]
        if not lock.acquire(blocking=False):
            json_response(self, 409, {"error": f"Operation already in progress for {sid}"})
            return
        try:
            # Check existence inside the lock to prevent TOCTOU races
            if not src.exists():
                state = "enabled" if activate else "disabled"
                json_response(self, 409, {"error": f"Extension already {state}: {sid}"})
                return
            # os.replace (not os.rename) — Windows os.rename raises
            # FileExistsError when destination exists; os.replace always
            # overwrites atomically.
            os.replace(str(src), str(dst))
        except OSError as exc:
            json_response(self, 500, {"error": f"Failed to {action} extension: {exc}"})
            return
        finally:
            lock.release()

        logger.info("%sd extension compose: %s", action, sid)
        json_response(self, 200, {"status": "ok", "service_id": sid, "action": action})

    def _handle_extension_sync_config(self):
        """Copy <ext_dir>/config/* into INSTALL_DIR/config/.

        Some extensions ship a config/ subdirectory whose files are
        bind-mounted by compose.yaml relative to the compose project root
        (INSTALL_DIR), not the extension directory.  Without this sync,
        Docker auto-creates the mount source as an empty directory and
        the container fails at startup.

        The dashboard-api previously did this copy itself, but its
        bind-mount of /ods/config is read-only, so it cannot
        write there.  The host agent runs on the host filesystem
        (writable) and is the right place for this work.
        """
        if not check_auth(self):
            return
        body = read_json_body(self)
        if body is None:
            return

        sid = body.get("service_id", "")
        if not isinstance(sid, str) or not SERVICE_ID_RE.match(sid):
            json_response(self, 400, {"error": "Invalid service_id"})
            return

        # Only user-installed extensions ship a config/ subdir for sync
        # at install time; built-in configs are pre-created by the
        # installer and must not be overwritten on re-toggle.
        ext_dir = USER_EXTENSIONS_DIR / sid
        if not ext_dir.is_dir():
            # Not a user extension — no-op (built-ins handled by installer).
            json_response(self, 200, {"status": "ok", "service_id": sid, "synced": []})
            return

        ext_config = ext_dir / "config"
        if not ext_config.is_dir():
            json_response(self, 200, {"status": "ok", "service_id": sid, "synced": []})
            return

        # Reject ANY symlink in the config/ tree (or if config/ itself is a
        # symlink). _copytree_safe (the install-time copier) strips symlinks
        # from user extensions, so legitimate extensions never have any.
        # A symlink here implies tampering or a packaging bug, and would be
        # dereferenced by shutil.copytree(symlinks=False) below — exfiltrating
        # link-target content into a path the dashboard-api container can read.
        # Iterating dirs + files (not just files) closes the symlinked-directory
        # gap: os.walk(followlinks=False) does NOT recurse into symlinked dirs,
        # so they only ever surface in the parent's `dirs` list.
        # The walk covers the WHOLE config/ tree (including out-of-scope
        # siblings) — a symlink anywhere is treated as tampering, even if the
        # contract restriction below means we wouldn't have copied it anyway.
        if ext_config.is_symlink():
            json_response(self, 400, {
                "error": (
                    f"config sync refused: {sid}/config is a symlink "
                    f"(symlinks are not permitted in extension configs)"
                ),
            })
            return
        for root, dirs, files in os.walk(str(ext_config), followlinks=False):
            for name in dirs + files:
                if (Path(root) / name).is_symlink():
                    json_response(self, 400, {
                        "error": (
                            f"config sync refused: symlink {name} in "
                            f"{sid}/config (symlinks are not permitted)"
                        ),
                    })
                    return

        # Default copy contract: an extension may only write to its OWN
        # config tree — `<ext>/config/<service_id>/` → `INSTALL_DIR/config/<service_id>/`.
        # Anything else under `<ext>/config/` (e.g. `<ext>/config/open-webui/`,
        # `<ext>/config/litellm/`) is silently ignored — copying those would let
        # a user extension overwrite installer-managed core configs or another
        # extension's config tree. Cross-service writes are not part of the
        # default contract; if a legitimate use case ever surfaces, an explicit
        # manifest allowlist field is the right escape hatch (out of scope here).
        src_svc = ext_config / sid

        # Inventory siblings so the response can audit what was ignored.
        out_of_scope: list[str] = []
        for child in ext_config.iterdir():
            if child.name != sid:
                out_of_scope.append(child.name)
                logger.info(
                    "ignoring out-of-scope config entry %s/config/%s "
                    "(default contract: only %s/config/%s/ is synced)",
                    sid, child.name, sid, sid,
                )

        # If the extension ships no `config/<sid>/` at all, no-op.
        if not src_svc.exists():
            json_response(self, 200, {
                "status": "ok",
                "service_id": sid,
                "synced": [],
                "skipped": out_of_scope,
            })
            return
        if not src_svc.is_dir():
            json_response(self, 400, {
                "error": (
                    f"config sync refused: {sid}/config/{sid} must be a directory"
                ),
            })
            return

        install_config = (INSTALL_DIR / "config").resolve()
        try:
            install_config.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            json_response(self, 500, {"error": f"Failed to prepare config dir: {exc}"})
            return

        target = (install_config / sid).resolve()
        # Path-traversal guard: target must stay under install_config. Always true
        # because sid is validated against SERVICE_ID_RE above (no slashes / dots),
        # but kept as defense-in-depth in case the regex ever loosens.
        if not target.is_relative_to(install_config):
            json_response(self, 400, {
                "error": f"config sync refused: target outside install dir for {sid}",
            })
            return

        synced: list[str] = []
        lock = _service_locks[sid]
        if not lock.acquire(blocking=False):
            json_response(self, 409, {"error": f"Operation already in progress for {sid}"})
            return
        try:
            try:
                shutil.copytree(
                    str(src_svc), str(target),
                    dirs_exist_ok=True, symlinks=False,
                )
                synced.append(sid)
            except OSError as exc:
                json_response(self, 500, {
                    "error": f"Failed to copy {sid}/config/{sid}: {exc}",
                })
                return
            # Mark .sh files executable in the synced service tree.
            for root, _dirs, files in os.walk(str(target)):
                for fname in files:
                    if fname.endswith(".sh"):
                        fpath = Path(root) / fname
                        try:
                            fpath.chmod(
                                fpath.stat().st_mode
                                | stat_mod.S_IXUSR | stat_mod.S_IXGRP | stat_mod.S_IXOTH,
                            )
                        except OSError as exc:
                            logger.warning("chmod +x failed for %s: %s", fpath, exc)
        finally:
            lock.release()

        logger.info(
            "synced config for extension %s (%d in-scope, %d out-of-scope ignored)",
            sid, len(synced), len(out_of_scope),
        )
        json_response(self, 200, {
            "status": "ok",
            "service_id": sid,
            "synced": synced,
            "skipped": out_of_scope,
        })

    def _handle_logs(self):
        if not check_auth(self):
            return
        body = read_json_body(self)
        if body is None:
            return
        service_id = validate_service_id(self, body)
        if service_id is None:
            return
        try:
            tail = min(max(int(body.get("tail", 100)), 1), 500)
        except (ValueError, TypeError):
            tail = 100
        try:
            # Use docker logs directly (faster than docker compose logs, no flag resolution needed)
            container_name = f"ods-{service_id}"
            cmd = ["docker", "logs", "--tail", str(tail), container_name]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=5,
            )
            # Handle container not yet created (e.g. during image pull)
            if result.returncode != 0 and "no such container" in (result.stderr or "").lower():
                json_response(self, 200, {
                    "service_id": service_id,
                    "logs": "Container is starting up — logs will appear once it is running.",
                    "lines": 0,
                })
                return
            # docker logs writes to stderr for some containers
            output = result.stdout or result.stderr or ""
            json_response(self, 200, {
                "service_id": service_id,
                "logs": output[-50000:],
                "lines": tail,
            })
        except subprocess.TimeoutExpired:
            json_response(self, 503, {"error": "Log fetch timed out"})
        except Exception as exc:
            json_response(self, 500, {"error": f"Failed to fetch logs: {exc}"})


    def _handle_service_logs(self):
        """Read-only log access for ANY service (core + extensions).

        Unlike _handle_logs() which uses validate_service_id() and blocks
        core services, this endpoint only validates the service_id format.
        """
        if not check_auth(self):
            return
        body = read_json_body(self)
        if body is None:
            return

        sid = body.get("service_id", "")
        if not isinstance(sid, str) or not SERVICE_ID_RE.match(sid):
            json_response(self, 400, {"error": "Invalid service_id"})
            return

        try:
            tail = min(max(int(body.get("tail", 100)), 1), 500)
        except (ValueError, TypeError):
            tail = 100

        container_name = _resolve_container_name(sid)

        try:
            result = subprocess.run(
                ["docker", "logs", "--tail", str(tail), container_name],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0 and "no such container" in (result.stderr or "").lower():
                json_response(self, 200, {
                    "service_id": sid,
                    "container_name": container_name,
                    "logs": "Container is not running.",
                    "lines": 0,
                })
                return
            if result.returncode != 0:
                json_response(self, 500, {"error": f"docker logs failed: {(result.stderr or '')[:500]}"})
                return
            output = result.stdout or result.stderr or ""
            json_response(self, 200, {
                "service_id": sid,
                "container_name": container_name,
                "logs": output[-50000:],
                "lines": tail,
            })
        except subprocess.TimeoutExpired:
            json_response(self, 503, {"error": "Log fetch timed out"})
        except Exception as exc:
            json_response(self, 500, {"error": f"Failed to fetch logs: {exc}"})

    def _handle_service_restart(self):
        """Restart one known ODS service container."""
        if not check_auth(self):
            return
        body = read_json_body(self)
        if body is None:
            return

        sid = body.get("service_id", "")
        if not isinstance(sid, str) or not SERVICE_ID_RE.match(sid):
            json_response(self, 400, {"error": "Invalid service_id"})
            return

        has_container, restart_error = _service_has_docker_container(sid)
        if not has_container:
            status = 404 if restart_error.startswith("Service not found") else 400
            json_response(self, status, {"error": restart_error})
            return

        lock = _service_locks[sid]
        if not lock.acquire(blocking=False):
            json_response(self, 409, {"error": f"Operation already in progress for {sid}"})
            return

        try:
            delay_seconds = min(max(float(body.get("delay_seconds", 0) or 0), 0), 10)
        except (ValueError, TypeError):
            json_response(self, 400, {"error": "Invalid delay_seconds"})
            lock.release()
            return

        container_name = _resolve_container_name(sid)

        def restart_container():
            if delay_seconds > 0:
                time.sleep(delay_seconds)
            result = subprocess.run(
                ["docker", "restart", container_name],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                stderr = (result.stderr or result.stdout or "").strip()
                status = 404 if "no such container" in stderr.lower() else 500
                json_response(self, status, {
                    "error": f"docker restart failed: {stderr[:500]}",
                    "service_id": sid,
                    "container_name": container_name,
                })
                return
            json_response(self, 200, {
                "status": "ok",
                "service_id": sid,
                "container_name": container_name,
                "action": "restart",
            })

        def restart_container_later():
            try:
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                result = subprocess.run(
                    ["docker", "restart", container_name],
                    capture_output=True, text=True, timeout=60,
                )
                if result.returncode != 0:
                    stderr = (result.stderr or result.stdout or "").strip()
                    logger.warning("Delayed restart failed for %s (%s): %s", sid, container_name, stderr[:500])
            except Exception as exc:
                logger.warning("Delayed restart failed for %s (%s): %s", sid, container_name, exc)
            finally:
                lock.release()

        if delay_seconds > 0:
            threading.Thread(target=restart_container_later, daemon=True).start()
            json_response(self, 202, {
                "status": "accepted",
                "service_id": sid,
                "container_name": container_name,
                "action": "restart",
                "delay_seconds": delay_seconds,
            })
            return

        try:
            restart_container()
        except subprocess.TimeoutExpired:
            json_response(self, 503, {"error": "Service restart timed out"})
        except Exception as exc:
            json_response(self, 500, {"error": f"Failed to restart service: {exc}"})
        finally:
            lock.release()


    def _handle_setup_hook(self):
        """Backwards-compatible wrapper — delegates to hook resolution with post_install."""
        if not check_auth(self):
            return
        body = read_json_body(self)
        if body is None:
            return
        service_id = validate_service_id(self, body)
        if service_id is None:
            return

        ext_dir = _find_ext_dir(service_id)
        if ext_dir is None:
            json_response(self, 404, {"error": f"Extension not found: {service_id}"})
            return

        hook_path = _resolve_hook(ext_dir, "post_install")
        if hook_path is None:
            json_response(self, 404, {"error": f"No setup_hook defined for {service_id}"})
            return

        self._execute_hook(service_id, ext_dir, hook_path, "post_install")

    def _handle_hook(self):
        """Generic lifecycle hook endpoint: POST /v1/extension/hooks."""
        if not check_auth(self):
            return
        body = read_json_body(self)
        if body is None:
            return

        # Validate service_id
        sid = body.get("service_id", "")
        if not isinstance(sid, str) or not SERVICE_ID_RE.match(sid):
            json_response(self, 400, {"error": "Invalid service_id"})
            return

        # Validate hook name
        hook_name = body.get("hook", "")
        if not isinstance(hook_name, str) or hook_name not in VALID_HOOK_NAMES:
            json_response(self, 400, {
                "error": f"Invalid hook name. Must be one of: {', '.join(sorted(VALID_HOOK_NAMES))}",
            })
            return

        ext_dir = _find_ext_dir(sid)
        if ext_dir is None:
            json_response(self, 404, {"error": f"Extension not found: {sid}"})
            return

        hook_path = _resolve_hook(ext_dir, hook_name)
        if hook_path is None:
            # No hook defined — not an error
            json_response(self, 404, {"error": f"No {hook_name} hook defined for {sid}"})
            return

        self._execute_hook(sid, ext_dir, hook_path, hook_name)

    def _execute_hook(self, service_id: str, ext_dir: Path, hook_path: Path, hook_name: str):
        """Execute a resolved hook script with sandboxed environment."""
        # macOS: validate bash version >= 4.0
        bash_ok, bash_msg = _check_bash_version()
        if not bash_ok:
            json_response(self, 500, {"error": f"Cannot run hook: {bash_msg}"})
            return

        # Read manifest for service port
        manifest = _read_manifest(ext_dir)
        service_def = manifest.get("service", {}) if manifest else {}
        if not isinstance(service_def, dict):
            service_def = {}

        # Minimal allowlist environment
        hook_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", ""),
            "SERVICE_ID": service_id,
            "SERVICE_PORT": str(service_def.get("port", 0)),
            "SERVICE_DATA_DIR": str(DATA_DIR / service_id),
            "ODS_VERSION": ODS_VERSION,
            "GPU_BACKEND": GPU_BACKEND,
            "HOOK_NAME": hook_name,
        }
        bash = _find_usable_bash()
        if not bash:
            json_response(self, 500, {
                "error": f"Cannot run hook: {hook_name} hook requires a usable Bash runtime. Install Git Bash or run ODS through WSL/Linux."
            })
            return

        logger.info("Running %s hook for %s: %s", hook_name, service_id, hook_path)
        try:
            popen_kwargs = {
                "cwd": str(ext_dir),
                "env": hook_env,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
            }
            if platform.system() != "Windows":
                popen_kwargs["preexec_fn"] = os.setsid
            proc = subprocess.Popen(
                [bash, str(hook_path), str(INSTALL_DIR), GPU_BACKEND],
                **popen_kwargs,
            )
            try:
                stdout, stderr = proc.communicate(timeout=HOOK_TIMEOUT)
            except subprocess.TimeoutExpired:
                if platform.system() == "Windows":
                    proc.kill()
                else:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.wait()
                json_response(self, 500, {"error": f"{hook_name} hook timed out ({HOOK_TIMEOUT}s)"})
                return

            if proc.returncode != 0:
                logger.error("%s hook failed for %s (exit %d): %s",
                             hook_name, service_id, proc.returncode, (stderr or b"").decode()[:500])
                # post_start failure is non-terminal
                if hook_name == "post_start":
                    json_response(self, 200, {
                        "status": "warning",
                        "service_id": service_id,
                        "hook": hook_name,
                        "warning": f"post_start hook exited with code {proc.returncode}",
                        "stderr": (stderr or b"").decode()[:500],
                    })
                    return
                json_response(self, 500, {
                    "error": f"{hook_name} hook exited with code {proc.returncode}",
                    "stderr": (stderr or b"").decode()[:500],
                })
                return
        except OSError as exc:
            json_response(self, 500, {"error": f"Failed to execute hook: {exc}"})
            return

        logger.info("%s hook completed for %s", hook_name, service_id)
        json_response(self, 200, {"status": "ok", "service_id": service_id, "hook": hook_name})

    def _handle_install(self):
        """Combined install: setup_hook → pull → start with progress tracking."""
        if not check_auth(self):
            return
        body = read_json_body(self)
        if body is None:
            return
        service_id = validate_service_id(self, body)
        if service_id is None:
            return
        run_setup_hook = body.get("run_setup_hook", False)

        lock = _service_locks[service_id]
        if not lock.acquire(blocking=False):
            json_response(self, 409, {"error": f"Operation in progress for {service_id}"})
            return

        def _run_install():
            try:
                flags = resolve_compose_flags()

                ext_dir = _find_ext_dir(service_id)
                if ext_dir is None:
                    _write_progress(service_id, "error", "Installation failed",
                                    error=f"Extension directory not found for {service_id}")
                    return

                # Step 1: Setup hook (if requested). The helper is a no-op
                # when no hook is declared — it does not pre-write any
                # "Running setup..." progress, so extensions without a hook
                # don't show a misleading setup phase in the dashboard.
                if run_setup_hook:
                    ok, _ = _run_post_install_hook(service_id, ext_dir)
                    if not ok:
                        return

                # Step 2: Pull (best-effort — failure is non-fatal if cached image exists).
                # Narrow the pull to base + GPU overlay + this extension's own
                # compose so we don't refetch images for every other installed
                # extension on each install. The `up` step below keeps full
                # `flags` so cross-service `depends_on` still resolves.
                #
                # Some extensions declare cross-extension `depends_on`
                # (e.g. perplexica → searxng). Narrowing those out makes
                # `docker compose pull` fail at config-parse time with
                # "depends on undefined service". Validate the narrowed
                # set with `config --services` first; if it doesn't
                # resolve, fall back to the full flag set.
                narrowed = _narrow_install_pull_flags(flags, service_id)
                # 30s mirrors `resolve_compose_flags`: `config --services`
                # is essentially instant when Docker is healthy; a long
                # timeout just delays detection of a hung daemon.
                if narrowed != flags and _narrowed_compose_set_resolves(
                    narrowed, service_id, str(INSTALL_DIR), 30,
                ):
                    pull_flags = narrowed
                else:
                    if narrowed != flags:
                        logger.info(
                            "Narrowed compose for %s drops a referenced service; using full set",
                            service_id,
                        )
                    pull_flags = flags

                _write_progress(service_id, "pulling", "Downloading image...")
                pull_result = subprocess.run(
                    ["docker", "compose"] + pull_flags + ["pull", service_id],
                    cwd=str(INSTALL_DIR), capture_output=True, text=True,
                    timeout=SUBPROCESS_TIMEOUT_START,
                )
                if pull_result.returncode != 0:
                    logger.warning("Pull failed for %s (rc=%d), proceeding to start: %s",
                                   service_id, pull_result.returncode, pull_result.stderr[-200:])

                # Step 3: Start
                _write_progress(service_id, "starting", "Starting container...")
                _precreate_data_dirs(service_id)
                start_result = subprocess.run(
                    ["docker", "compose"] + flags + ["up", "-d", service_id],
                    cwd=str(INSTALL_DIR), capture_output=True, text=True,
                    timeout=SUBPROCESS_TIMEOUT_START,
                )
                if start_result.returncode != 0:
                    _write_progress(service_id, "error", "Installation failed",
                                    error=start_result.stderr[-500:])
                    return

                # By default, poll for running state: compose `up -d`
                # returns 0 even for Created/Exited/Restarting containers,
                # so a 0 exit is NOT conclusive proof the service actually
                # started. Extensions whose containers intentionally exit
                # after init (one-shot setup containers, extensions whose
                # value is purely the setup_hook) can opt out via the
                # manifest's `service.startup_check: false`, in which
                # case compose's 0 exit is taken as success.
                install_manifest = _read_manifest(ext_dir)
                install_service_def = install_manifest.get("service", {}) if install_manifest else {}
                if not isinstance(install_service_def, dict):
                    install_service_def = {}
                container_name = install_service_def.get("container_name") or f"ods-{service_id}"

                # Manifest-driven opt-out for one-shot / setup-only extensions
                # whose containers intentionally exit (init containers,
                # extensions whose value is purely the setup_hook). Setting
                # `service.startup_check: false` skips the running-state poll
                # — compose up's clean exit is taken as success. Default is
                # True so existing long-running services are unchanged.
                startup_check = install_service_def.get("startup_check", True)

                if startup_check:
                    # Per-extension startup deadline; manifests with heavy init
                    # (postgres, clickhouse, JVM-based services) can override the
                    # 15s default via service.startup_timeout.
                    startup_timeout = install_service_def.get("startup_timeout", 15)
                    deadline = time.monotonic() + startup_timeout
                    state: str | None = None
                    state_error = ""
                    while time.monotonic() < deadline:
                        try:
                            inspect_result = subprocess.run(
                                ["docker", "inspect", "--format",
                                 "{{.State.Status}}|{{.State.Error}}", container_name],
                                capture_output=True, text=True, timeout=5,
                            )
                        except subprocess.TimeoutExpired:
                            inspect_result = None
                        if inspect_result is not None and inspect_result.returncode == 0:
                            parts = inspect_result.stdout.strip().split("|", 1)
                            state = parts[0] if parts else ""
                            state_error = parts[1] if len(parts) > 1 else ""
                            if state == "running":
                                break
                        time.sleep(1)

                    if state != "running":
                        msg = f"Container did not reach running state within {startup_timeout}s (state={state or 'unknown'})"
                        if state_error:
                            msg += f": {state_error}"
                        _write_progress(service_id, "error", "Installation failed",
                                        error=msg)
                        return

                # Step 4: Success
                _write_progress(service_id, "started", "Service started")

                # Step 5: Post-install core recreate (best-effort, non-fatal).
                # Some extensions (e.g. openclaw) add overlay env to already-
                # running core services; `up -d <ext>` (without --force-recreate)
                # won't apply those changes. Failure here must not fail the install.
                try:
                    _post_install_core_recreate(service_id)
                except Exception:
                    logger.exception(
                        "Post-install core recreate raised for %s (ignored)",
                        service_id,
                    )

            except subprocess.TimeoutExpired:
                _write_progress(service_id, "error", "Installation failed",
                                error=f"timed out ({SUBPROCESS_TIMEOUT_START}s)")
            except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                logger.exception("Install failed for %s", service_id)
                _write_progress(service_id, "error", "Installation failed",
                                error=str(exc)[:500])
            finally:
                lock.release()

        try:
            json_response(self, 202, {"status": "accepted", "service_id": service_id, "action": "install"})
            threading.Thread(target=_run_install, daemon=True).start()
        except Exception:
            lock.release()
            raise


    # ── Model management handlers ──

    def _handle_model_list(self):
        """Return model library catalog + on-disk GGUFs + active model."""
        if not check_auth(self):
            return
        try:
            models_dir = INSTALL_DIR / "data" / "models"
            library_path = INSTALL_DIR / "config" / "model-library.json"
            env_path = INSTALL_DIR / ".env"

            # Load library. A missing file is fine (fresh install); an
            # unreadable/malformed file is a real error — surface it as 500
            # rather than silently returning an empty catalog.
            library = []
            if library_path.exists():
                try:
                    library = json.loads(library_path.read_text(encoding="utf-8")).get("models", [])
                except (json.JSONDecodeError, OSError):
                    logger.exception("Model library catalog unavailable")
                    json_response(self, 500, {"error": "Model catalog unavailable"})
                    return

            # Scan downloaded GGUFs
            downloaded = {}
            if models_dir.is_dir():
                for f in models_dir.iterdir():
                    if f.name.lower().endswith(".gguf") and _model_file_ready(f):
                        try:
                            downloaded[f.name] = f.stat().st_size
                        except OSError:
                            pass

            # Active model from .env
            active_gguf = ""
            if env_path.exists():
                env = load_env(env_path)
                active_gguf = env.get("GGUF_FILE", "")

            json_response(self, 200, {
                "library": library,
                "downloaded": downloaded,
                "active_gguf": active_gguf,
            })
        except Exception as exc:
            json_response(self, 500, {"error": f"Failed to list models: {exc}"})

    def _handle_model_status(self):
        """Return current model download progress."""
        if not check_auth(self):
            return
        status_path = INSTALL_DIR / "data" / "model-download-status.json"
        if not status_path.exists():
            json_response(self, 200, {"status": "idle"})
            return
        try:
            data = json.loads(status_path.read_text(encoding="utf-8"))
            json_response(self, 200, data)
        except (json.JSONDecodeError, OSError):
            json_response(self, 200, {"status": "idle"})

    def _handle_model_download(self):
        """Start async model download. Only one download at a time.

        Supports both single-file and split-file (gguf_parts) models.
        For split models, the caller sends gguf_parts as an array of
        {"file": ..., "url": ...} dicts.  The first part's filename is
        used as gguf_file for status tracking.
        """
        global _model_download_thread
        if not check_auth(self):
            return
        body = read_json_body(self)
        if body is None:
            return

        gguf_file = body.get("gguf_file", "")
        gguf_url = body.get("gguf_url", "")
        gguf_parts = body.get("gguf_parts", [])

        if not gguf_file or (not gguf_url and not gguf_parts):
            json_response(self, 400, {"error": "gguf_file and gguf_url (or gguf_parts) are required"})
            return

        # Build the download plan: list of (filename, url) tuples
        if gguf_parts:
            download_plan = [(p["file"], p["url"]) for p in gguf_parts if p.get("file") and p.get("url")]
            if not download_plan:
                json_response(self, 400, {"error": "gguf_parts entries must have file and url"})
                return
        else:
            download_plan = [(gguf_file, gguf_url)]

        # Validate against library (prevent arbitrary URL downloads).
        # Also harvest expected SHA256s keyed by filename so verification can
        # cover every part of split-file downloads, not just single-file models.
        library_path = INSTALL_DIR / "config" / "model-library.json"
        allowed = False
        # Sentinel: distinguishes "catalog unreadable/missing" (500) from
        # "catalog readable but model not listed" (403). Conflating the two
        # masks broken installs as policy denials.
        catalog_ok = False
        expected_sha_by_file: dict = {}
        if library_path.exists():
            try:
                lib = json.loads(library_path.read_text(encoding="utf-8"))
                catalog_ok = True
                for m in lib.get("models", []):
                    if m.get("gguf_file") != gguf_file:
                        continue
                    if gguf_parts:
                        # Verify every (file, url) in the request matches the library
                        lib_parts_meta = {
                            (p["file"], p["url"]): p.get("sha256", "")
                            for p in m.get("gguf_parts", [])
                            if p.get("file") and p.get("url")
                        }
                        req_parts = set(download_plan)
                        if req_parts and req_parts <= set(lib_parts_meta.keys()):
                            allowed = True
                            expected_sha_by_file = {
                                file: lib_parts_meta[(file, url)]
                                for file, url in download_plan
                            }
                    elif m.get("gguf_url") == gguf_url:
                        allowed = True
                        expected_sha_by_file = {gguf_file: m.get("gguf_sha256", "")}
                    break
            except (json.JSONDecodeError, OSError):
                logger.exception("Model library catalog unavailable")
                json_response(self, 500, {"error": "Model catalog unavailable"})
                return
        if not catalog_ok:
            json_response(self, 500, {"error": "Model catalog unavailable"})
            return
        if not allowed:
            json_response(self, 403, {"error": "Model not in library catalog"})
            return

        models_dir = INSTALL_DIR / "data" / "models"
        status_path = INSTALL_DIR / "data" / "model-download-status.json"
        # For split models, check ALL parts exist (not just the first)
        all_downloaded = all(_model_file_ready(models_dir / fn) for fn, _ in download_plan)
        if all_downloaded:
            # A previous process can leave stale "downloading" status after the
            # final file is already on disk. Normalize that here so the
            # dashboard stops showing phantom progress.
            _write_model_status(status_path, "complete", gguf_file, 0, 0)
            json_response(self, 200, {"status": "already_downloaded"})
            return
        for fn, _ in download_plan:
            target = models_dir / fn
            if target.is_file() and not _model_file_ready(target):
                target.unlink(missing_ok=True)
        pending_download_plan = [
            (idx, fn, url)
            for idx, (fn, url) in enumerate(download_plan, 1)
            if not _model_file_ready(models_dir / fn)
        ]

        # Check for concurrent download
        with _model_download_lock:
            if _model_download_thread is not None and _model_download_thread.is_alive():
                json_response(self, 409, {"error": "Another download is in progress"})
                return

            _model_download_cancel.clear()

            def _download():
                global _model_download_proc
                try:
                    models_dir.mkdir(parents=True, exist_ok=True)
                    label = gguf_file if len(download_plan) == 1 else f"{gguf_file} ({len(download_plan)} parts)"
                    _write_model_status(status_path, "downloading", label, 0, 0)

                    for part_idx, part_file_name, part_url in pending_download_plan:
                        if _model_download_cancel.is_set():
                            break
                        part_target = models_dir / part_file_name
                        part_tmp = models_dir / f"{part_file_name}.part"
                        part_label = part_file_name if len(download_plan) == 1 else f"{part_file_name} (part {part_idx}/{len(download_plan)})"

                        # Get real file size by following redirects and reading final Content-Length
                        part_total = 0
                        try:
                            head_result = subprocess.run(
                                ["curl", "-sI", "-L", "--connect-timeout", "10", part_url],
                                capture_output=True, text=True, timeout=30,
                            )
                            # Take the LAST content-length header (after all redirects)
                            for line in head_result.stdout.splitlines():
                                if line.lower().startswith("content-length:"):
                                    val = int(line.split(":", 1)[1].strip())
                                    if val > 10000:  # Ignore redirect page sizes
                                        part_total = val
                        except (subprocess.TimeoutExpired, ValueError):
                            pass

                        _write_model_status(status_path, "downloading", part_label, 0, part_total)

                        # Progress polling: update status by checking .part file size.
                        # Also kills the active curl process when cancel is requested.
                        _stop_progress = threading.Event()

                        def _poll_progress():
                            while not _stop_progress.is_set():
                                if _model_download_cancel.is_set():
                                    proc_ref = _model_download_proc
                                    if proc_ref is not None:
                                        try:
                                            proc_ref.kill()
                                        except (OSError, AttributeError):
                                            pass
                                try:
                                    if part_tmp.exists():
                                        current = part_tmp.stat().st_size
                                        _write_model_status(status_path, "downloading", part_label, current, part_total)
                                except OSError:
                                    pass
                                _stop_progress.wait(2)  # Poll every 2 seconds

                        progress_thread = threading.Thread(target=_poll_progress, daemon=True)
                        progress_thread.start()

                        # Download with retry. Use Popen (not run) so the process can
                        # be killed from the cancel handler or _poll_progress thread.
                        success = False
                        last_error = ""
                        for attempt in range(1, 4):
                            if _model_download_cancel.is_set():
                                break
                            if attempt > 1:
                                logger.info("Model download retry %d/3 for %s", attempt, part_file_name)
                                # Use wait() instead of sleep() so cancel is honored immediately
                                _model_download_cancel.wait(5)
                            proc = subprocess.Popen(
                                ["curl", "-fSL", "-C", "-", "--connect-timeout", "30",
                                 "-o", str(part_tmp), part_url],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            )
                            _model_download_proc = proc
                            try:
                                proc.wait(timeout=14400)
                            except subprocess.TimeoutExpired:
                                proc.kill()
                                proc.wait(timeout=5)
                            _model_download_proc = None

                            if _model_download_cancel.is_set():
                                break
                            if proc.returncode == 0:
                                try:
                                    part_tmp.rename(part_target)
                                except OSError as exc:
                                    last_error = f"Download finished but final file could not be moved into place: {exc}"
                                else:
                                    if _model_file_ready(part_target):
                                        success = True
                                        break
                                    last_error = "Download finished but model file is missing or empty"
                                    part_target.unlink(missing_ok=True)
                            else:
                                last_error = f"curl exited with code {proc.returncode}"
                            _write_model_status(
                                status_path,
                                "downloading",
                                part_label,
                                0,
                                part_total,
                                f"Retry {attempt}/3: {last_error}",
                            )

                        _stop_progress.set()
                        progress_thread.join(timeout=3)

                        if _model_download_cancel.is_set():
                            part_tmp.unlink(missing_ok=True)
                            _write_model_status(status_path, "cancelled", gguf_file, 0, 0, "Download cancelled by user")
                            logger.info("Model download cancelled: %s", gguf_file)
                            return

                        if not success:
                            part_tmp.unlink(missing_ok=True)
                            _write_model_status(
                                status_path,
                                "failed",
                                part_label,
                                0,
                                part_total,
                                last_error or "Download failed after 3 attempts",
                            )
                            return

                    # Verify SHA256 for every downloaded part. Catalog is the
                    # source of truth: split-file models carry per-part sha256
                    # in expected_sha_by_file, single-file models carry one
                    # entry. Empty checksum -> warn (do not silently skip), so
                    # missing catalog entries surface during operator review.
                    import hashlib
                    if _model_download_cancel.is_set():
                        _write_model_status(status_path, "cancelled", gguf_file, 0, 0, "Download cancelled by user")
                        return
                    for part_idx, (part_file_name, _) in enumerate(download_plan, 1):
                        expected = expected_sha_by_file.get(part_file_name, "")
                        final_target = models_dir / part_file_name
                        if not _model_file_ready(final_target):
                            _write_model_status(
                                status_path,
                                "failed",
                                part_file_name,
                                0,
                                0,
                                "Download finished but model file is missing or empty",
                            )
                            return
                        if not expected:
                            logger.warning(
                                "SHA256 verification skipped for %s: no checksum in model-library.json",
                                part_file_name,
                            )
                            continue
                        final_size = final_target.stat().st_size
                        verify_label = (
                            part_file_name
                            if len(download_plan) == 1
                            else f"{part_file_name} (part {part_idx}/{len(download_plan)})"
                        )
                        _write_model_status(status_path, "verifying", verify_label, final_size, final_size)
                        sha = hashlib.sha256()
                        with open(final_target, "rb") as f:
                            for chunk in iter(lambda: f.read(1048576), b""):
                                sha.update(chunk)
                        actual = sha.hexdigest()
                        if actual != expected:
                            final_target.unlink(missing_ok=True)
                            _write_model_status(
                                status_path,
                                "failed",
                                part_file_name,
                                0,
                                0,
                                f"SHA256 mismatch: expected {expected[:12]}..., got {actual[:12]}...",
                            )
                            return

                    _write_model_status(status_path, "complete", gguf_file, 0, 0)
                    logger.info("Model download complete: %s (%d parts)", gguf_file, len(download_plan))
                except Exception as exc:
                    logger.error("Model download failed: %s", exc)
                    _write_model_status(status_path, "failed", gguf_file, 0, 0, str(exc))

            _model_download_thread = threading.Thread(target=_download, daemon=True)
            _model_download_thread.start()

        json_response(self, 200, {"status": "started"})

    def _handle_model_download_cancel(self):
        """Cancel an in-progress model download."""
        if not check_auth(self):
            return
        with _model_download_lock:
            if _model_download_thread is None or not _model_download_thread.is_alive():
                json_response(self, 200, {"status": "no_download"})
                return
        _model_download_cancel.set()
        # Capture local reference to avoid TOCTOU race — the download thread
        # may null out _model_download_proc between the check and kill.
        proc_ref = _model_download_proc
        if proc_ref is not None:
            try:
                proc_ref.kill()
            except (OSError, AttributeError):
                pass
        json_response(self, 200, {"status": "cancelling"})

    def _handle_model_activate(self):
        """Swap active model: update .env + models.ini + restart llama-server."""
        if not check_auth(self):
            return
        body = read_json_body(self)
        if body is None:
            return

        model_id = body.get("model_id", "")
        if not model_id:
            json_response(self, 400, {"error": "model_id is required"})
            return

        if not _model_activate_lock.acquire(blocking=False):
            json_response(self, 409, {"error": "Another model activation is in progress"})
            return

        try:
            self._do_model_activate(model_id)
        finally:
            _model_activate_lock.release()

    def _do_model_activate(self, model_id: str):
        """Inner activate logic — called with _model_activate_lock held."""
        import time

        def local_gguf_model_from_id(raw_model_id: str) -> dict | None:
            models_dir = INSTALL_DIR / "data" / "models"
            gguf_file = _resolve_local_gguf_filename(raw_model_id, models_dir)
            if not gguf_file:
                return None
            target = (models_dir / gguf_file).resolve()
            if not target.is_relative_to(models_dir.resolve()) or not target.is_file():
                return None

            env_values = load_env(INSTALL_DIR / ".env")
            context_length = 32768
            for key in ("MAX_CONTEXT", "CTX_SIZE"):
                try:
                    value = int(env_values.get(key) or 0)
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    context_length = value
                    break

            llm_model_name = _local_model_name_from_gguf(gguf_file)
            return {
                "id": llm_model_name,
                "gguf_file": gguf_file,
                "llm_model_name": llm_model_name,
                "context_length": context_length,
                "runtime_profiles": [],
                "local": True,
            }

        # Look up model in library
        library_path = INSTALL_DIR / "config" / "model-library.json"
        model = None
        if library_path.exists():
            try:
                lib = json.loads(library_path.read_text(encoding="utf-8"))
                for m in lib.get("models", []):
                    if m.get("id") == model_id:
                        model = m
                        break
            except (json.JSONDecodeError, OSError):
                pass
        if model is None:
            model = local_gguf_model_from_id(model_id)
            if model is None:
                json_response(self, 404, {"error": f"Model '{model_id}' not found in library or local GGUF files"})
                return

        gguf_file = model.get("gguf_file", "")
        llm_model_name = model.get("llm_model_name", model_id)
        context_length = model.get("context_length", 32768)
        llama_server_image = model.get("llama_server_image")

        # Verify GGUF exists on disk (with path traversal protection)
        models_dir = INSTALL_DIR / "data" / "models"
        target = (models_dir / gguf_file).resolve()
        if not target.is_relative_to(models_dir.resolve()):
            json_response(self, 400, {"error": "Invalid model file path"})
            return
        if not _model_file_ready(target):
            json_response(self, 400, {"error": f"Model file not downloaded or empty: {gguf_file}"})
            return

        env_path = INSTALL_DIR / ".env"
        models_ini = INSTALL_DIR / "config" / "llama-server" / "models.ini"
        lemonade_yaml = INSTALL_DIR / "config" / "litellm" / "lemonade.yaml"
        hermes_live_config = INSTALL_DIR / "data" / "hermes" / "config.yaml"
        hermes_template_config = INSTALL_DIR / "extensions" / "services" / "hermes" / "cli-config.yaml.template"

        # Hoisted so the outer except's rollback can reference them safely.
        # None means the snapshot was not captured, so rollback must skip it.
        env_backup: str | None = None
        ini_backup: str | None = None
        lemonade_backup = None
        hermes_backups: dict[Path, str] = {}
        committed = False

        def restore_backups():
            if env_backup is not None:
                env_path.write_text(env_backup, encoding="utf-8")
            if ini_backup is not None:
                models_ini.write_text(ini_backup, encoding="utf-8")
            if lemonade_backup is not None:
                lemonade_yaml.write_text(lemonade_backup, encoding="utf-8")
            for hermes_path, hermes_text in hermes_backups.items():
                hermes_path.write_text(hermes_text, encoding="utf-8")

        try:
            # Read current env BEFORE modification — needed for gpu_backend guard
            env_pre = load_env(env_path)
            gpu_backend = env_pre.get("GPU_BACKEND", "nvidia")
            windows_host_lemonade = _is_windows_host_lemonade(env_pre)
            windows_lemonade_already_serving = False
            if windows_host_lemonade and env_pre.get("GGUF_FILE") == gguf_file:
                lemonade_port = env_pre.get("AMD_INFERENCE_PORT", "8080") or "8080"
                windows_lemonade_already_serving = _lemonade_completion_ready(
                    "127.0.0.1", lemonade_port, gguf_file,
                )
                if windows_lemonade_already_serving:
                    logger.info(
                        "Windows Lemonade is already serving %s; refreshing configs "
                        "without restarting native Lemonade",
                        gguf_file,
                    )
            runtime_profile = _select_runtime_profile(model, env_pre)
            runtime_env = {}
            if runtime_profile:
                try:
                    context_length = int(runtime_profile.get("context_length") or context_length)
                except (TypeError, ValueError):
                    pass
                llama_server_image = runtime_profile.get("llama_server_image") or llama_server_image
                runtime_env = runtime_profile.get("env") if isinstance(runtime_profile.get("env"), dict) else {}

            def _context_from_env_key(key: str) -> int:
                try:
                    return int(env_pre.get(key) or 0)
                except (TypeError, ValueError):
                    return 0

            hermes_context_floor = max(_context_from_env_key("MAX_CONTEXT"), _context_from_env_key("CTX_SIZE"))
            try:
                hermes_live_exists_for_context = hermes_live_config.exists()
            except PermissionError:
                hermes_live_exists_for_context = False
            if hermes_context_floor > context_length and hermes_live_exists_for_context:
                # A Hermes-enabled install may intentionally raise llama.cpp's
                # context above the catalog/profile value. Do not let dashboard
                # model activation silently lower Hermes back under its own
                # 64K minimum.
                context_length = hermes_context_floor

            # Save rollback snapshot
            env_backup = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
            ini_backup = models_ini.read_text(encoding="utf-8") if models_ini.exists() else ""
            lemonade_backup = lemonade_yaml.read_text(encoding="utf-8") if lemonade_yaml.exists() else None
            # Hermes's live config dir is created by the container at first
            # boot with UID 10000 / mode 0700, so the host-agent (running as
            # the host user) cannot read or write data/hermes/config.yaml.
            # That's expected — patching the model name there is a courtesy
            # so the next Hermes restart picks up the new model. If we can't
            # read it, skip the backup/patch and continue. bootstrap-upgrade.sh
            # already treats this as non-fatal (line ~640) — mirror it here.
            for hermes_path in (hermes_live_config, hermes_template_config):
                try:
                    if hermes_path.exists():
                        hermes_backups[hermes_path] = hermes_path.read_text(encoding="utf-8")
                except PermissionError:
                    logger.warning(
                        "Hermes config %s not readable by host-agent (likely owned by "
                        "container UID); skipping backup. Patch attempt will also skip; "
                        "operator can manually edit the live config and then "
                        "`docker restart ods-hermes` to pick up the new model.",
                        hermes_path,
                    )

            # Update .env
            if env_path.exists():
                lines = env_path.read_text(encoding="utf-8").splitlines()
                updates = {
                    "GGUF_FILE": gguf_file,
                    "LLM_MODEL": llm_model_name,
                    "CTX_SIZE": str(context_length),
                    "MAX_CONTEXT": str(context_length),
                    "MODEL_RUNTIME_PROFILE": runtime_profile.get("id", "") if runtime_profile else "",
                    "MODEL_RUNTIME_PROFILE_LABEL": runtime_profile.get("label", "") if runtime_profile else "",
                    "MODEL_RUNTIME_PROFILE_SOURCE": runtime_profile.get("source_url", "") if runtime_profile else "",
                }
                runtime_keys = {
                    "LLAMA_PARALLEL",
                    "LLAMA_ARG_FLASH_ATTN",
                    "LLAMA_ARG_CACHE_TYPE_K",
                    "LLAMA_ARG_CACHE_TYPE_V",
                    "LLAMA_ARG_N_CPU_MOE",
                    "LLAMA_ARG_NO_CACHE_PROMPT",
                    "LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS",
                    "LLAMA_ARG_SPEC_TYPE",
                    "LLAMA_ARG_SPEC_DRAFT_N_MAX",
                }
                if runtime_profile:
                    for key, value in runtime_env.items():
                        if key in runtime_keys and value is not None:
                            updates[key] = str(value)
                else:
                    updates.update({
                        "LLAMA_PARALLEL": "1",
                        "LLAMA_ARG_FLASH_ATTN": "auto",
                        "LLAMA_ARG_CACHE_TYPE_K": "f16",
                        "LLAMA_ARG_CACHE_TYPE_V": "f16",
                    })
                remove_keys = {
                    "LLAMA_ARG_N_CPU_MOE",
                    "LLAMA_ARG_NO_CACHE_PROMPT",
                    "LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS",
                    "LLAMA_ARG_SPEC_TYPE",
                    "LLAMA_ARG_SPEC_DRAFT_N_MAX",
                    "LLAMA_SERVER_IMAGE",
                }
                remove_keys.difference_update(updates)
                # Only update LLAMA_SERVER_IMAGE on Docker backends.
                # macOS runs llama-server natively (no Docker image to pull).
                if llama_server_image and gpu_backend != "apple":
                    updates["LLAMA_SERVER_IMAGE"] = llama_server_image
                new_lines = []
                seen = set()
                for line in lines:
                    key = line.split("=", 1)[0] if "=" in line and not line.startswith("#") else None
                    if key and key in updates:
                        new_lines.append(f"{key}={updates[key]}")
                        seen.add(key)
                    elif key and key in remove_keys:
                        continue
                    else:
                        new_lines.append(line)
                for key, val in updates.items():
                    if key not in seen:
                        new_lines.append(f"{key}={val}")
                env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

            # Update models.ini
            models_ini.parent.mkdir(parents=True, exist_ok=True)
            models_ini.write_text(
                f"[{llm_model_name}]\n"
                f"filename = {gguf_file}\n"
                f"load-on-startup = true\n"
                f"n-ctx = {context_length}\n",
                encoding="utf-8",
            )

            # Regenerate LiteLLM lemonade config so it routes to the new model.
            # Only written on AMD installs where lemonade.yaml exists.
            if lemonade_yaml.exists():
                _write_lemonade_config(INSTALL_DIR, gguf_file)

            hermes_model_name = f"extra.{gguf_file}" if gpu_backend == "amd" else gguf_file
            try:
                hermes_live_exists = hermes_live_config.exists()
            except PermissionError:
                hermes_live_exists = None
            hermes_base_url = "http://litellm:4000/v1" if windows_host_lemonade else None
            hermes_live_patched = _patch_hermes_model_config(
                hermes_live_config,
                hermes_model_name,
                base_url=hermes_base_url,
                context_length=context_length,
            )
            hermes_template_patched = _patch_hermes_model_config(
                hermes_template_config,
                hermes_model_name,
                base_url=hermes_base_url,
                context_length=context_length,
            )
            # Restart Hermes only when its persisted live config changed, or
            # when no persisted config exists and a patched template can seed
            # the next start. If live config is container-owned and unreadable,
            # restarting would keep the old persisted model, so skip it.
            hermes_patched = hermes_live_patched or (hermes_template_patched and hermes_live_exists is False)
            if hermes_template_patched and not hermes_patched:
                logger.warning(
                    "Patched Hermes template but not the live config; skipping "
                    "ods-hermes restart because it would keep using the old "
                    "persisted config until an operator edits data/hermes/config.yaml."
                )

            # Restart llama-server with the new model.
            # Three strategies depending on platform / agent location:
            # - apple (macOS): llama-server runs natively via Metal, not Docker.
            #   Managed via PID file — SIGTERM the old process, launch new one.
            # - _in_container (Docker Desktop / WSL2): docker inspect+run.
            #   Compose can't be used because relative bind-mount paths resolve
            #   to the agent container's filesystem, not the host.
            # - Host-native Linux: docker compose stop+up, same as bootstrap-upgrade.sh.
            env = load_env(env_path)
            _in_container = bool(os.environ.get("ODS_HOST_INSTALL_DIR"))

            if windows_host_lemonade:
                if not windows_lemonade_already_serving:
                    _restart_windows_lemonade(env)
            elif gpu_backend == "apple":
                # macOS: manage native llama-server process via PID file
                pid_file = INSTALL_DIR / "data" / ".llama-server.pid"
                llama_bin = INSTALL_DIR / "bin" / "llama-server"
                llama_log = INSTALL_DIR / "data" / "llama-server.log"

                if not llama_bin.exists():
                    restore_backups()
                    json_response(self, 500, {"error": "llama-server binary not found — re-run installer"})
                    return

                # Stop existing native process
                if pid_file.exists():
                    try:
                        old_pid = int(pid_file.read_text(encoding="utf-8").strip())
                        # Verify PID is llama-server before killing (prevent PID reuse accidents)
                        try:
                            ps_result = subprocess.run(
                                ["ps", "-p", str(old_pid), "-o", "comm="],
                                capture_output=True, text=True, timeout=5,
                            )
                            if "llama" not in ps_result.stdout.lower():
                                raise OSError("PID is not llama-server")
                        except (subprocess.TimeoutExpired, OSError):
                            pid_file.unlink(missing_ok=True)
                            raise OSError("stale PID")
                        os.kill(old_pid, signal.SIGTERM)
                        for _ in range(20):
                            try:
                                os.kill(old_pid, 0)
                                time.sleep(0.5)
                            except OSError:
                                break
                        else:
                            os.kill(old_pid, signal.SIGKILL)
                    except (ValueError, OSError):
                        pass
                    pid_file.unlink(missing_ok=True)

                # Re-launch native llama-server with new model
                _launch_native_llama_server(env_path, llama_bin, llama_log, pid_file)
            elif _in_container:
                override_image = llama_server_image or ("ghcr.io/ggml-org/llama.cpp:server-cuda-b9014" if gpu_backend == "nvidia" else "")
                _recreate_llama_server(env, override_image=override_image)
            else:
                _compose_restart_llama_server(env)

            # Health check (up to 5 min)
            # Use container name on docker network (localhost is the agent
            # container when running containerized, not the llama-server).
            # Determine health check URL based on where the agent runs:
            # - Inside a container (ODS_HOST_INSTALL_DIR set): use docker
            #   network name + internal port 8080
            # - On the host (native systemd or macOS): use 127.0.0.1 + OLLAMA_PORT.
            #   (Use 127.0.0.1, not localhost — localhost resolves to ::1 on
            #   IPv6-enabled hosts but Docker binds to 127.0.0.1 only.)
            if windows_host_lemonade:
                llama_host = "127.0.0.1"
                llama_port = env.get("AMD_INFERENCE_PORT", "8080")
            elif os.environ.get("ODS_HOST_INSTALL_DIR"):
                llama_host = "ods-llama-server"
                llama_port = "8080"
            else:
                llama_host = "127.0.0.1"
                llama_port = env.get("OLLAMA_PORT", "8080")
            health_path = "/api/v1/health" if gpu_backend == "amd" else "/health"
            health_url = f"http://{llama_host}:{llama_port}{health_path}"
            logger.info("Waiting for llama-server health at %s", health_url)
            healthy = False
            warmup_sent = False
            time.sleep(5)  # Give container time to start
            for attempt in range(60):
                try:
                    result = subprocess.run(
                        ["curl", "-s", "--max-time", "5", health_url],
                        capture_output=True, text=True, timeout=10,
                    )
                    body = result.stdout.strip()
                    if gpu_backend == "amd":
                        # Lemonade returns {"status":"ok","model_loaded":null}
                        # before a model is loaded — must verify model_loaded
                        # is non-null.  Mirrors bootstrap-upgrade.sh:330.
                        if _check_lemonade_health(body):
                            healthy = True
                        elif body:
                            # Send warm-up request every 3rd attempt (~15s)
                            # to trigger on-demand model loading.
                            if not warmup_sent or attempt % 3 == 0:
                                warmup_sent = _send_lemonade_warmup(
                                    llama_host, llama_port, gguf_file, attempt,
                                )
                            if attempt % 6 == 0:
                                logger.info(
                                    "Lemonade healthy but no model loaded (attempt %d)",
                                    attempt + 1,
                                )
                    else:
                        # llama.cpp: 200 with "ok" means model is loaded
                        if '"ok"' in body:
                            healthy = True
                        elif attempt % 6 == 0:
                            logger.info("Health check attempt %d: %s", attempt + 1, body[:100])
                    if healthy:
                        logger.info("llama-server healthy after %d attempts", attempt + 1)
                        break
                except subprocess.TimeoutExpired:
                    if attempt % 6 == 0:
                        logger.info("Health check attempt %d: timeout", attempt + 1)
                time.sleep(5)

            if healthy:
                # Regenerate lemonade.yaml if active.  Lemonade requires the
                # exact model ID (extra.<GGUF_FILE>) — a wildcard doesn't work.
                # Mirrors bootstrap-upgrade.sh lines 364-384.
                ods_mode = env.get("ODS_MODE", "local")
                if ods_mode == "lemonade":
                    _write_lemonade_config(INSTALL_DIR, gguf_file)

                # Restart dependent services so they pick up the new model
                dependent_services = ["ods-litellm"]
                if hermes_patched:
                    dependent_services.append("ods-hermes")
                for svc in dependent_services:
                    subprocess.run(["docker", "restart", svc],
                                   capture_output=True, timeout=60)
                committed = True  # system state is committed before the response write
                json_response(self, 200, {"status": "activated", "model_id": model_id})
            else:
                # Rollback
                logger.warning("Model activation failed — rolling back")
                restore_backups()
                rollback_env = load_env(env_path)
                rollback_windows_host_lemonade = _is_windows_host_lemonade(rollback_env)
                if rollback_windows_host_lemonade:
                    _restart_windows_lemonade(rollback_env)
                elif gpu_backend == "apple":
                    # Stop newly launched native process, re-launch with old params
                    if pid_file.exists():
                        try:
                            new_pid = int(pid_file.read_text(encoding="utf-8").strip())
                            try:
                                ps_result = subprocess.run(
                                    ["ps", "-p", str(new_pid), "-o", "comm="],
                                    capture_output=True, text=True, timeout=5,
                                )
                                if "llama" not in ps_result.stdout.lower():
                                    raise OSError("PID is not llama-server")
                            except (subprocess.TimeoutExpired, OSError):
                                pid_file.unlink(missing_ok=True)
                                raise OSError("stale PID")
                            os.kill(new_pid, signal.SIGTERM)
                            for _ in range(20):
                                try:
                                    os.kill(new_pid, 0)
                                    time.sleep(0.5)
                                except OSError:
                                    break
                            else:
                                os.kill(new_pid, signal.SIGKILL)
                        except (ValueError, OSError):
                            pass
                        pid_file.unlink(missing_ok=True)
                    _launch_native_llama_server(env_path, llama_bin, llama_log, pid_file)
                elif _in_container:
                    _recreate_llama_server(rollback_env)
                else:
                    _compose_restart_llama_server(rollback_env)
                json_response(self, 500, {"error": "Health check failed — rolled back to previous model", "rolled_back": True})

        except Exception as exc:
            if not committed:
                try:
                    restore_backups()
                except OSError:
                    logger.exception("Rollback write failed during model-activate failure handling")
            json_response(self, 500, {"error": f"Model activation failed: {exc}"})

    def _handle_model_delete(self):
        """Delete a downloaded GGUF model file."""
        if not check_auth(self):
            return
        body = read_json_body(self)
        if body is None:
            return

        gguf_file = body.get("gguf_file", "")
        if not gguf_file:
            json_response(self, 400, {"error": "gguf_file is required"})
            return

        models_dir = INSTALL_DIR / "data" / "models"
        target = (models_dir / gguf_file).resolve()

        # Path traversal prevention
        if not target.is_relative_to(models_dir.resolve()):
            json_response(self, 400, {"error": "Invalid file path"})
            return

        if not target.exists():
            json_response(self, 404, {"error": f"File not found: {gguf_file}"})
            return

        # Refuse to delete the active model
        env = load_env(INSTALL_DIR / ".env")
        if env.get("GGUF_FILE", "") == gguf_file:
            json_response(self, 409, {"error": "Cannot delete the currently active model"})
            return

        try:
            # For split models, delete all part files
            library_path = INSTALL_DIR / "config" / "model-library.json"
            parts_to_delete = [target]
            if library_path.exists():
                try:
                    lib = json.loads(library_path.read_text(encoding="utf-8"))
                    for m in lib.get("models", []):
                        if m.get("gguf_file") == gguf_file and m.get("gguf_parts"):
                            parts_to_delete = []
                            for p in m["gguf_parts"]:
                                pf = (models_dir / p["file"]).resolve()
                                if pf.is_relative_to(models_dir.resolve()) and pf.exists():
                                    parts_to_delete.append(pf)
                            break
                except (json.JSONDecodeError, OSError):
                    pass

            for pf in parts_to_delete:
                pf.unlink()
            json_response(self, 200, {"status": "deleted", "gguf_file": gguf_file})
        except OSError as exc:
            json_response(self, 500, {"error": f"Failed to delete: {exc}"})


def _check_lemonade_health(body: str) -> bool:
    """Check if Lemonade health response indicates a model is loaded.

    Lemonade returns {"status": "ok", "model_loaded": null} when healthy
    but no model is loaded yet.  Returns True only when model_loaded is
    non-null.  Mirrors bootstrap-upgrade.sh line 330.
    """
    try:
        data = json.loads(body)
        return data.get("model_loaded") is not None
    except (json.JSONDecodeError, TypeError):
        return False


def _send_lemonade_warmup(host: str, port: str, gguf_file: str, attempt: int) -> bool:
    """Send a warm-up chat completion to trigger Lemonade on-demand model load.

    Lemonade discovers models via --extra-models-dir but only loads them when
    a request arrives for that model ID.  Returns True if the request was
    accepted (model is loading).  Mirrors bootstrap-upgrade.sh lines 343-347.
    """
    model_id = f"extra.{gguf_file}"
    url = f"http://{host}:{port}/api/v1/chat/completions"
    payload = json.dumps({
        "model": model_id,
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 1,
    })
    logger.info("Sending warm-up request for %s (attempt %d/60)", model_id, attempt + 1)
    try:
        result = subprocess.run(
            ["curl", "-sf", "--max-time", "30", "-X", "POST", url,
             "-H", "Content-Type: application/json", "-d", payload],
            capture_output=True, text=True, timeout=35,
        )
        if result.returncode == 0:
            logger.info("Warm-up request accepted — model is loading")
            return True
    except subprocess.TimeoutExpired:
        pass
    return False


def _lemonade_completion_ready(host: str, port: str, gguf_file: str) -> bool:
    """Return True when Lemonade can complete against the requested GGUF."""
    model_id = f"extra.{gguf_file}"
    url = f"http://{host}:{port}/api/v1/chat/completions"
    payload = json.dumps({
        "model": model_id,
        "messages": [{"role": "user", "content": "reply ok"}],
        "max_tokens": 1,
        "temperature": 0,
    })
    try:
        result = subprocess.run(
            ["curl", "-sf", "--max-time", "30", "-X", "POST", url,
             "-H", "Content-Type: application/json", "-d", payload],
            capture_output=True, text=True, timeout=35,
        )
        if result.returncode != 0:
            return False
        data = json.loads(result.stdout or "{}")
        return bool(data.get("choices"))
    except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError):
        return False


def _is_windows_host_lemonade(env: dict) -> bool:
    runtime = env.get("AMD_INFERENCE_RUNTIME", "").lower()
    backend = env.get("LLM_BACKEND", "").lower()
    location = env.get("AMD_INFERENCE_LOCATION", "").lower()
    return (
        platform.system().lower() == "windows"
        and env.get("GPU_BACKEND", "").lower() == "amd"
        and (runtime == "lemonade" or backend == "lemonade")
        and location == "host"
    )


def _restart_windows_lemonade(env: dict):
    """Start managed Windows Lemonade through Task Scheduler.

    Windows OpenSSH can end plain Start-Process children when the SSH logon
    session exits. Task Scheduler gives the native Lemonade runtime an
    independent lifecycle, which keeps fleet/dashboard activation stable.
    """
    exe = None
    executable_names = ("lemonade-server.exe", "LemonadeServer.exe")
    install_folders = ("Lemonade Server", "lemonade_server", "LemonadeServer")
    for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if not root:
            continue
        for folder in install_folders:
            for name in executable_names:
                candidate = Path(root) / folder / "bin" / name
                if candidate.exists():
                    exe = candidate
                    break
            if exe is not None:
                break
        if exe is not None:
            break
    if exe is None:
        raise RuntimeError("Lemonade server executable not found under Program Files")

    ps_env = os.environ.copy()
    ps_env.update({
        "ODS_WIN_LEMONADE_EXE": str(exe),
        "ODS_WIN_MODELS_DIR": str(INSTALL_DIR / "data" / "models"),
        "ODS_WIN_PID_FILE": str(INSTALL_DIR / "data" / "llama-server.pid"),
        "ODS_WIN_LEMONADE_PORT": env.get("AMD_INFERENCE_PORT", "8080") or "8080",
        "ODS_WIN_BIND_ADDR": env.get("BIND_ADDRESS", "127.0.0.1") or "127.0.0.1",
        "ODS_WIN_LEMONADE_TASK": "ODSLemonadeRuntime",
    })
    script = r'''
$ErrorActionPreference = "Stop"
$exe = $env:ODS_WIN_LEMONADE_EXE
$modelsDir = $env:ODS_WIN_MODELS_DIR
$pidPath = $env:ODS_WIN_PID_FILE
$port = [int]$env:ODS_WIN_LEMONADE_PORT
$bindAddr = $env:ODS_WIN_BIND_ADDR
$taskName = $env:ODS_WIN_LEMONADE_TASK

function Stop-ODSProcessId {
    param([int]$ProcId)
    Stop-Process -Id $ProcId -Force -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 30; $i++) {
        if (-not (Get-Process -Id $ProcId -ErrorAction SilentlyContinue)) { return }
        Start-Sleep -Milliseconds 500
    }
    & taskkill.exe /PID $ProcId /T /F | Out-Null
    for ($i = 0; $i -lt 30; $i++) {
        if (-not (Get-Process -Id $ProcId -ErrorAction SilentlyContinue)) { return }
        Start-Sleep -Milliseconds 500
    }
    throw "Could not stop process $ProcId"
}

function Get-ODSLemonadeProcesses {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($binDir, [StringComparison]::OrdinalIgnoreCase)) -or
        ($cacheBin -and $_.ExecutablePath -and $_.ExecutablePath.StartsWith($cacheBin, [StringComparison]::OrdinalIgnoreCase)) -or
        ($_.CommandLine -and $_.CommandLine.IndexOf($modelsDir, [StringComparison]::OrdinalIgnoreCase) -ge 0)
    }
}

$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
try { Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue } catch {}
if (Test-Path $pidPath) {
    $rawPid = (Get-Content -LiteralPath $pidPath -Raw).Trim()
    if ($rawPid -match '^\d+$') { Stop-ODSProcessId -ProcId ([int]$rawPid) }
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}
foreach ($listener in @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
    if ($listener.OwningProcess -gt 0) { Stop-ODSProcessId -ProcId ([int]$listener.OwningProcess) }
}
$binDir = Split-Path -Parent $exe
$userProfile = [Environment]::GetFolderPath("UserProfile")
$cacheBin = if ($userProfile) { Join-Path (Join-Path (Join-Path $userProfile ".cache") "lemonade") "bin" } else { $null }
foreach ($child in @(Get-ODSLemonadeProcesses)) {
    Stop-ODSProcessId -ProcId ([int]$child.ProcessId)
}
$remaining = @(Get-ODSLemonadeProcesses)
if ($remaining.Count -gt 0) {
    $ids = ($remaining | ForEach-Object { "$($_.ProcessId):$($_.Name)" }) -join ", "
    throw "Could not stop existing Lemonade processes: $ids"
}

$argString = "serve --port $port --host $bindAddr --no-tray --llamacpp vulkan --extra-models-dir `"$modelsDir`""
$launchMethod = "scheduled task"
try {
    $action = New-ScheduledTaskAction -Execute $exe -Argument $argString -WorkingDirectory (Split-Path -Parent $exe)
    $trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddYears(1))
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Force -ErrorAction Stop | Out-Null
    Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
} catch {
    if ($existingTask) {
        try {
            Write-Warning "Could not refresh Lemonade scheduled task; reusing existing task: $_"
            Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
        } catch {
            $launchMethod = "direct process"
            Write-Warning "Could not start Lemonade through Task Scheduler: $_"
            Start-Process -FilePath $exe -ArgumentList $argString -WindowStyle Hidden -WorkingDirectory (Split-Path -Parent $exe) | Out-Null
        }
    } else {
        $launchMethod = "direct process"
        Write-Warning "Could not start Lemonade through Task Scheduler: $_"
        Start-Process -FilePath $exe -ArgumentList $argString -WindowStyle Hidden -WorkingDirectory (Split-Path -Parent $exe) | Out-Null
    }
}
$proc = $null
for ($i = 0; $i -lt 45; $i++) {
    Start-Sleep -Seconds 1
    $proc = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.Equals($exe, [StringComparison]::OrdinalIgnoreCase) } |
        Sort-Object ProcessId -Descending |
        Select-Object -First 1
    if ($proc) { break }
}
if (-not $proc -and $launchMethod -eq "scheduled task") {
    $launchMethod = "direct process"
    Write-Warning "Lemonade scheduled task did not start a server process. Starting directly."
    try { Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue } catch {}
    foreach ($listener in @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
        if ($listener.OwningProcess -gt 0) { Stop-ODSProcessId -ProcId ([int]$listener.OwningProcess) }
    }
    Start-Process -FilePath $exe -ArgumentList $argString -WindowStyle Hidden -WorkingDirectory (Split-Path -Parent $exe) | Out-Null
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 1
        $proc = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.ExecutablePath -and $_.ExecutablePath.Equals($exe, [StringComparison]::OrdinalIgnoreCase) } |
            Sort-Object ProcessId -Descending |
            Select-Object -First 1
        if ($proc) { break }
    }
}
if (-not $proc) {
    $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction SilentlyContinue
    $taskResult = if ($taskInfo) { $taskInfo.LastTaskResult } else { "unknown" }
    throw "Lemonade $launchMethod started but no Lemonade process was found (task result: $taskResult)"
}
New-Item -ItemType Directory -Path (Split-Path -Parent $pidPath) -Force | Out-Null
Set-Content -LiteralPath $pidPath -Value $proc.ProcessId
'''
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        timeout=120,
        env=ps_env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Windows Lemonade restart failed: {(result.stderr or result.stdout).strip()[:500]}")
    logger.info("Windows Lemonade scheduled task started")


def _render_runtime_config(
    install_dir: Path,
    surface: str,
    *,
    gguf_file: str,
    lemonade_api_key: str,
    lemonade_api_base: str,
    ods_mode: str,
    gpu_backend: str,
) -> bool:
    renderer = install_dir / "scripts" / "render-runtime-configs.py"
    if not renderer.exists():
        return False
    cmd = [
        sys.executable,
        str(renderer),
        "--surface",
        surface,
        "--ods-mode",
        ods_mode,
        "--gpu-backend",
        gpu_backend,
        "--gguf-file",
        gguf_file,
        "--lemonade-api-base",
        lemonade_api_base,
        "--litellm-key",
        lemonade_api_key,
        "--output-root",
        str(install_dir),
        "--write",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Runtime config renderer failed for %s: %s", surface, exc)
        return False
    if result.returncode != 0:
        logger.warning(
            "Runtime config renderer failed for %s: %s",
            surface,
            (result.stderr or result.stdout).strip(),
        )
        return False
    return True


def _write_lemonade_config(install_dir: Path, gguf_file: str):
    """Regenerate lemonade.yaml with the correct model ID for LiteLLM.

    Lemonade exposes models as ``extra.<GGUF_FILE>`` — the LiteLLM config
    must reference the exact ID, not a wildcard passthrough.
    Mirrors bootstrap-upgrade.sh lines 369-382.
    """
    config_path = install_dir / "config" / "litellm" / "lemonade.yaml"
    # Read from .env via load_env, NOT os.environ. The host-agent systemd
    # unit does not source .env as an EnvironmentFile, so os.environ is
    # unreliable for installer-written values; falling back to the legacy
    # static "sk-lemonade" would silently revert key rotation.
    env = load_env(install_dir / ".env")
    lemonade_api_key = env.get("LITELLM_LEMONADE_API_KEY", "sk-lemonade")
    ods_mode = env.get("ODS_MODE", "lemonade")
    gpu_backend = env.get("GPU_BACKEND", "amd")
    lemonade_api_base = "http://llama-server:8080/api/v1"
    if env.get("AMD_INFERENCE_LOCATION", "").lower() == "host":
        lemonade_port = env.get("AMD_INFERENCE_PORT", "8080") or "8080"
        lemonade_api_base = f"http://host.docker.internal:{lemonade_port}/api/v1"
    if _render_runtime_config(
        install_dir,
        "litellm-lemonade",
        gguf_file=gguf_file,
        lemonade_api_key=lemonade_api_key,
        lemonade_api_base=lemonade_api_base,
        ods_mode=ods_mode,
        gpu_backend=gpu_backend,
    ):
        logger.info("Wrote lemonade.yaml via runtime renderer for model: extra.%s", gguf_file)
        return

    content = (
        "model_list:\n"
        "  - model_name: \"*\"\n"
        "    litellm_params:\n"
        f"      model: openai/extra.{gguf_file}\n"
        f"      api_base: {lemonade_api_base}\n"
        f"      api_key: {lemonade_api_key}\n"
        "      extra_body:\n"
        "        chat_template_kwargs:\n"
        "          enable_thinking: false\n"
        "\n"
        "litellm_settings:\n"
        "  drop_params: true\n"
        "  set_verbose: false\n"
        "  request_timeout: 900\n"
        "  stream_timeout: 900\n"
    )
    config_path.write_text(content, encoding="utf-8")
    logger.info("Wrote lemonade.yaml for model: extra.%s", gguf_file)


def _patch_hermes_model_config(
    path: Path,
    model_name: str,
    base_url: str | None = None,
    context_length: int | None = None,
) -> bool:
    """Patch model routing fields in Hermes config.

    Hermes copies the template once into data/hermes/config.yaml and then uses
    the persisted copy as source of truth. Patch both when present so current
    and future Hermes starts request the model that ODS just loaded.

    Non-fatal: the live config lives in a container-owned dir (UID 10000,
    mode 0700) so the host-agent often can't read or write it. Treat any
    permission error as a skip, not a failure.
    """
    try:
        if not path.exists():
            return False
    except PermissionError:
        logger.warning(
            "Hermes config %s not statable by host-agent (container-owned dir); "
            "skipping patch. Operator can manually edit the live config and then "
            "`docker restart ods-hermes` to pick up the new model.",
            path,
        )
        return False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        logger.warning("Could not read Hermes config for model patch: %s", path)
        return False

    in_model_block = False
    changed = False
    new_lines = []
    for line in lines:
        if re.match(r"^model:\s*(?:#.*)?$", line):
            in_model_block = True
            new_lines.append(line)
            continue
        if in_model_block and line and not line.startswith((" ", "\t", "#")):
            in_model_block = False
        if in_model_block and re.match(r"^\s+default:\s*", line):
            indent = line[:len(line) - len(line.lstrip())]
            new_line = f'{indent}default: "{model_name}"'
            new_lines.append(new_line)
            changed = changed or new_line != line
            continue
        if base_url and in_model_block and re.match(r"^\s+base_url:\s*", line):
            indent = line[:len(line) - len(line.lstrip())]
            new_line = f'{indent}base_url: "{base_url}"'
            new_lines.append(new_line)
            changed = changed or new_line != line
            continue
        if context_length and re.match(r"^\s+context_length:\s*", line):
            indent = line[:len(line) - len(line.lstrip())]
            new_line = f"{indent}context_length: {int(context_length)}"
            new_lines.append(new_line)
            changed = changed or new_line != line
            continue
        new_lines.append(line)

    if not changed:
        return False
    try:
        path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        logger.info("Patched Hermes model.default in %s to %s", path, model_name)
        return True
    except OSError:
        logger.warning("Could not write Hermes config model patch: %s", path)
        return False


def _normalize_key(value) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def _normalize_host_arch(value) -> str:
    key = _normalize_key(value)
    if key in {"aarch64", "arm64"}:
        return "arm64"
    if key in {"x86-64", "x86_64", "amd64", "x64"}:
        return "amd64"
    return key or "unknown"


def _system_ram_gb() -> int:
    try:
        if os.name == "nt":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(round(stat.ullTotalPhys / (1024**3)))
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(round((pages * page_size) / (1024**3)))
    except (AttributeError, OSError, ValueError):
        return 0


def _nvidia_vram_gb() -> float:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if result.returncode == 0:
            first = result.stdout.strip().splitlines()[0].strip()
            return float(first) / 1024.0
    except (IndexError, OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return 0.0


def _select_runtime_profile(model: dict, env: dict) -> dict | None:
    profiles = model.get("runtime_profiles")
    if not isinstance(profiles, list):
        return None
    backend = _normalize_key(env.get("GPU_BACKEND", GPU_BACKEND or ""))
    memory_type = _normalize_key(env.get("GPU_MEMORY_TYPE", "discrete"))
    host_arch = _normalize_host_arch(platform.machine())
    vram_gb = _nvidia_vram_gb() if backend == "nvidia" else 0.0
    try:
        ram_gb = int(env.get("SYSTEM_RAM_GB") or 0) or _system_ram_gb()
    except (TypeError, ValueError):
        ram_gb = _system_ram_gb()
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        if _normalize_key(profile.get("backend")) not in {"", backend}:
            continue
        allowed_arches = {
            _normalize_host_arch(item)
            for item in (profile.get("host_arch") if isinstance(profile.get("host_arch"), list) else [profile.get("host_arch")])
            if item
        }
        if allowed_arches and host_arch not in allowed_arches:
            continue
        required_memory_type = _normalize_key(profile.get("memory_type"))
        if required_memory_type and required_memory_type != memory_type:
            continue
        try:
            if profile.get("vram_min_gb") is not None and vram_gb < float(profile["vram_min_gb"]):
                continue
            if profile.get("vram_max_gb") is not None and vram_gb > float(profile["vram_max_gb"]):
                continue
            if profile.get("system_ram_min_gb") is not None and float(ram_gb or 0) < float(profile["system_ram_min_gb"]):
                continue
        except (TypeError, ValueError):
            continue
        return profile
    return None


def _launch_native_llama_server(env_path: Path, llama_bin: Path, llama_log: Path, pid_file: Path):
    """Launch the native (Metal) llama-server process and write its PID file.

    Reads the current .env for GGUF_FILE, CTX_SIZE, and LLAMA_REASONING so
    the caller only needs to ensure .env is up-to-date before calling.
    """
    env = load_env(env_path)
    gguf_file = env.get("GGUF_FILE", "")
    ctx_size = env.get("CTX_SIZE", "32768")
    model_path = INSTALL_DIR / "data" / "models" / gguf_file
    reasoning = env.get("LLAMA_REASONING", "off")
    reasoning_fmt = {"off": "none", "on": "deepseek"}.get(reasoning, reasoning)
    # Honour the unified BIND_ADDRESS knob (PR #964); empty/missing → loopback.
    bind_addr = env.get("BIND_ADDRESS", "").strip() or "127.0.0.1"
    args = [
        str(llama_bin),
        "--host", bind_addr, "--port", "8080",
        "--model", str(model_path),
        "--ctx-size", ctx_size,
        "--n-gpu-layers", "999",
        "--parallel", env.get("LLAMA_PARALLEL", "1"),
        "--reasoning-format", reasoning_fmt,
        "--metrics",
    ]
    optional_args = {
        "LLAMA_ARG_FLASH_ATTN": "--flash-attn",
        "LLAMA_ARG_CACHE_TYPE_K": "--cache-type-k",
        "LLAMA_ARG_CACHE_TYPE_V": "--cache-type-v",
        "LLAMA_ARG_N_CPU_MOE": "--n-cpu-moe",
        "LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS": "--checkpoint-every-n-tokens",
        "LLAMA_ARG_SPEC_TYPE": "--spec-type",
        "LLAMA_ARG_SPEC_DRAFT_N_MAX": "--spec-draft-n-max",
    }
    for env_key, flag in optional_args.items():
        value = env.get(env_key, "").strip()
        if value:
            args.extend([flag, value])
    if _normalize_key(env.get("LLAMA_ARG_NO_CACHE_PROMPT")) not in {"", "0", "false", "off", "no"}:
        args.append("--no-cache-prompt")
    with open(llama_log, "a") as log_f:
        proc = subprocess.Popen(
            args,
            stdout=log_f, stderr=log_f,
        )
    pid_file.write_text(str(proc.pid), encoding="utf-8")
    logger.info("Native llama-server launched (pid %d, model %s)", proc.pid, gguf_file)


def _compose_restart_llama_server(env: dict):
    """Restart llama-server via docker compose (host-native path).

    This is the primary restart strategy for Linux (systemd) where the agent
    runs natively on the host. It mirrors the proven pattern from
    bootstrap-upgrade.sh lines 289-304.

    Uses resolve_compose_flags() so the compose stack is always built from the
    current install state — avoids stale or missing .compose-flags files.
    Uses stop + up -d (not restart) so that updated .env values are picked up
    by the new container.
    Raises RuntimeError on any docker-layer failure so _do_model_activate can
    surface the error immediately instead of waiting for the health-check loop.
    """
    gpu_backend = env.get("GPU_BACKEND", "nvidia")
    compose_flags = resolve_compose_flags()

    def _run(argv, timeout):
        result = subprocess.run(
            argv, cwd=str(INSTALL_DIR),
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"{' '.join(argv[:3])} failed (exit {result.returncode}): "
                f"{(result.stderr or '').strip()[:300]}"
            )

    if gpu_backend == "amd":
        # Lemonade reads models.ini on boot, so stop + up preserves the named
        # cache volumes while ensuring the fresh config is picked up.
        if compose_flags:
            _run(["docker", "compose"] + compose_flags + ["stop", "llama-server"], 120)
            _run(["docker", "compose"] + compose_flags + ["up", "-d", "llama-server"], 300)
        else:
            _run(["docker", "stop", "ods-llama-server"], 120)
            _run(["docker", "start", "ods-llama-server"], 300)
    else:
        # llama.cpp: recreate to pick up new GGUF_FILE from .env
        if compose_flags:
            _run(["docker", "compose"] + compose_flags + ["stop", "llama-server"], 120)
            _run(["docker", "compose"] + compose_flags + ["up", "-d", "llama-server"], 300)
        else:
            # No compose flags — cannot use compose.  Fall back to
            # inspect-and-recreate, which picks up GGUF_FILE from .env.
            # docker start alone re-uses the old container command.
            logger.warning("No .compose-flags file — using container recreation fallback")
            _recreate_llama_server(env)

    logger.info("llama-server restarted via compose (backend: %s)", gpu_backend)


def _recreate_llama_server(env: dict, override_image: str = ""):
    """Recreate llama-server container with updated model from .env.

    Instead of docker compose (which breaks relative volume mounts when
    run from inside a container), we inspect the existing container and
    create a new one with the same config but updated --model and --ctx-size.

    If override_image is set, use that image instead of the existing one
    (e.g., Gemma 4 models need a different llama.cpp build).
    """
    container = "ods-llama-server"
    gguf_file = env.get("GGUF_FILE", "")
    ctx_size = env.get("CTX_SIZE", "32768")

    # Get existing container config for image, mounts, env, ports, etc.
    result = subprocess.run(
        ["docker", "inspect", container],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        logger.error("Failed to inspect %s: %s", container, result.stderr)
        return

    config = json.loads(result.stdout)[0]

    # Build new command: replace --model and --ctx-size values
    old_cmd = config["Config"]["Cmd"] or []
    new_cmd = []
    skip_next = False
    for i, arg in enumerate(old_cmd):
        if skip_next:
            skip_next = False
            continue
        if arg == "--model" and i + 1 < len(old_cmd):
            new_cmd.append("--model")
            new_cmd.append(f"/models/{gguf_file}")
            skip_next = True
        elif arg == "--ctx-size" and i + 1 < len(old_cmd):
            new_cmd.append("--ctx-size")
            new_cmd.append(ctx_size)
            skip_next = True
        elif arg == "--parallel" and i + 1 < len(old_cmd):
            new_cmd.append("--parallel")
            new_cmd.append(env.get("LLAMA_PARALLEL", "1"))
            skip_next = True
        else:
            new_cmd.append(arg)

    image = override_image or config["Config"]["Image"]
    host_config = config["HostConfig"]

    # Stop and remove old container
    subprocess.run(["docker", "stop", container], capture_output=True, timeout=120)
    subprocess.run(["docker", "rm", container], capture_output=True, timeout=30)

    # Build docker run command from inspected config
    run_cmd = ["docker", "run", "-d", "--name", container]

    # Restart policy
    restart = host_config.get("RestartPolicy", {})
    if restart.get("Name"):
        run_cmd += ["--restart", restart["Name"]]

    # Network + aliases (compose sets service name as alias, e.g. "llama-server")
    # Other containers (LiteLLM, Open WebUI) reference "llama-server" by
    # the compose service name, so we must preserve it as a network alias.
    networks = config.get("NetworkSettings", {}).get("Networks", {})
    for net_name, net_cfg in networks.items():
        run_cmd += ["--network", net_name]
        # Restore aliases from the compose config
        for alias in (net_cfg.get("Aliases") or []):
            if alias != container and alias != config["Config"].get("Hostname", ""):
                run_cmd += ["--network-alias", alias]
        # Always ensure the compose service name is an alias
        run_cmd += ["--network-alias", "llama-server"]
        break  # Use the first network

    # Ports
    port_bindings = host_config.get("PortBindings") or {}
    for container_port, bindings in port_bindings.items():
        if bindings:
            for b in bindings:
                host_ip = b.get("HostIp", "")
                host_port = b.get("HostPort", "")
                if host_ip:
                    run_cmd += ["-p", f"{host_ip}:{host_port}:{container_port}"]
                else:
                    run_cmd += ["-p", f"{host_port}:{container_port}"]

    # Volumes/Bind mounts
    for mount in config.get("Mounts", []):
        src = mount.get("Source", "")
        dst = mount.get("Destination", "")
        mode = "ro" if mount.get("RW") is False else "rw"
        if src and dst:
            run_cmd += ["-v", f"{src}:{dst}:{mode}"]

    # Environment variables
    replacement_env = {
        key: value
        for key, value in env.items()
        if key.startswith("LLAMA_ARG_") or key in {"LLAMA_PARALLEL", "LLAMA_REASONING", "GGUF_FILE", "LLM_MODEL", "CTX_SIZE", "MAX_CONTEXT"}
    }
    seen_env_keys = set()
    for e in (config["Config"].get("Env") or []):
        key = e.split("=", 1)[0]
        if key in replacement_env:
            run_cmd += ["-e", f"{key}={replacement_env[key]}"]
            seen_env_keys.add(key)
        elif key.startswith("LLAMA_ARG_") or key in {"LLAMA_PARALLEL"}:
            continue
        else:
            run_cmd += ["-e", e]
    for key, value in replacement_env.items():
        if key not in seen_env_keys:
            run_cmd += ["-e", f"{key}={value}"]

    # Extra hosts
    for eh in (host_config.get("ExtraHosts") or []):
        run_cmd += ["--add-host", eh]

    # GPU (device requests)
    for dr in (host_config.get("DeviceRequests") or []):
        if dr.get("Driver") == "" or "gpu" in (dr.get("Capabilities") or [[]])[0]:
            count = dr.get("Count", 0)
            device_ids = dr.get("DeviceIDs") or []
            if device_ids:
                run_cmd += ["--gpus", f'device={",".join(device_ids)}']
            elif count == -1:
                run_cmd += ["--gpus", "all"]
            else:
                run_cmd += ["--gpus", str(count)]

    # Security options
    for so in (host_config.get("SecurityOpt") or []):
        run_cmd += ["--security-opt", so]

    # Logging
    log_config = host_config.get("LogConfig", {})
    if log_config.get("Type"):
        run_cmd += ["--log-driver", log_config["Type"]]
        for k, v in (log_config.get("Config") or {}).items():
            run_cmd += ["--log-opt", f"{k}={v}"]

    # Entrypoint (AMD Lemonade overrides this in compose)
    entrypoint = config["Config"].get("Entrypoint")
    if entrypoint:
        run_cmd += ["--entrypoint", entrypoint[0]]

    # Image and command
    run_cmd.append(image)
    run_cmd.extend(new_cmd)

    logger.info("Recreating llama-server: %s with model %s", image, gguf_file)
    result = subprocess.run(run_cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        logger.error("Failed to create llama-server: %s", result.stderr)
        raise RuntimeError(f"docker run failed: {result.stderr[-500:]}")
    logger.info("llama-server container created successfully")


def _write_model_status(path: Path, status: str, model: str, downloaded: int, total: int, error: str = ""):
    """Write model download status JSON atomically."""
    data = {
        "status": status,
        "model": model,
        "bytesDownloaded": downloaded,
        "bytesTotal": total,
        "updatedAt": _iso_now(),
    }
    if error:
        data["error"] = error
    tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except OSError as e:
        # Don't crash the activate flow; surface to the journal so operators
        # can diagnose why progress stalled.
        logger.warning("Failed to write model status to %s: %s", path, e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def _request_server_shutdown(server, signum=None):
    """Ask serve_forever() to exit from a helper thread.

    HTTPServer.shutdown() deadlocks when called from the same thread that is
    running serve_forever(). Python signal handlers run on the main thread, so
    the SIGTERM path must bounce the shutdown request to another thread.
    """
    if signum is not None:
        logger.info("Received signal %s; shutting down", signum)
    threading.Thread(
        target=server.shutdown,
        name="ods-host-agent-shutdown",
        daemon=True,
    ).start()


def main():
    global INSTALL_DIR, DATA_DIR, AGENT_API_KEY, GPU_BACKEND, TIER, GPU_COUNT, CORE_SERVICE_IDS
    global USER_EXTENSIONS_DIR, EXTENSIONS_DIR, ODS_VERSION

    parser = argparse.ArgumentParser(description="ODS Host Agent")
    parser.add_argument("--port", type=int, default=7710, help="Listen port (default: 7710)")
    parser.add_argument("--pid-file", type=str, default="", help="Write PID to this file")
    parser.add_argument("--install-dir", type=str, default="", help="ODS install directory")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not shutil.which("docker"):
        logger.error("docker not found in PATH")
        sys.exit(1)

    if args.install_dir:
        INSTALL_DIR = Path(args.install_dir).resolve()
    elif os.environ.get("ODS_HOME"):
        INSTALL_DIR = Path(os.environ["ODS_HOME"]).resolve()
    else:
        INSTALL_DIR = Path(__file__).resolve().parent.parent
    if not INSTALL_DIR.is_dir():
        logger.error("Install directory not found: %s", INSTALL_DIR)
        sys.exit(1)

    env = load_env(INSTALL_DIR / ".env")
    # Prefer dedicated ODS_AGENT_KEY; fall back to DASHBOARD_API_KEY for
    # existing installs that haven't generated a separate key yet.
    AGENT_API_KEY = env.get("ODS_AGENT_KEY", "") or env.get("DASHBOARD_API_KEY", "")
    if not AGENT_API_KEY:
        logger.error("Neither ODS_AGENT_KEY nor DASHBOARD_API_KEY set in .env")
        sys.exit(1)
    GPU_BACKEND = env.get("GPU_BACKEND", "nvidia")
    TIER = env.get("TIER", "1")
    GPU_COUNT = env.get("GPU_COUNT", "1")

    DATA_DIR = Path(env.get("ODS_DATA_DIR", str(INSTALL_DIR / "data")))
    USER_EXTENSIONS_DIR = Path(env.get(
        "ODS_USER_EXTENSIONS_DIR",
        str(DATA_DIR / "user-extensions"),
    ))
    EXTENSIONS_DIR = INSTALL_DIR / "extensions" / "services"
    ODS_VERSION = env.get("ODS_VERSION", VERSION)

    port = args.port
    env_port = env.get("ODS_AGENT_PORT", "")
    if port == 7710 and env_port:
        try:
            port = int(env_port)
        except ValueError:
            logger.warning("Invalid ODS_AGENT_PORT in .env: %s", env_port)

    CORE_SERVICE_IDS = load_core_service_ids(INSTALL_DIR / "config" / "core-service-ids.json")

    if args.pid_file:
        pid_path = Path(args.pid_file)
        pid_path.write_text(str(os.getpid()), encoding="utf-8")
        atexit.register(lambda: pid_path.unlink(missing_ok=True))

    # Determine bind address: explicit env override, or a platform-aware safe
    # default. Linux prefers the ods-network gateway so dashboard-api
    # containers can reach the agent without exposing it to the LAN. The bridge
    # gateway fallback keeps partial/older installs reachable until phase 11 can
    # restart the service after ods-network exists.
    bind_addr = _resolve_agent_bind_addr(env)

    server = ThreadedHTTPServer((bind_addr, port), AgentHandler)
    signal.signal(signal.SIGTERM, lambda signum, _frame: _request_server_shutdown(server, signum))
    signal.signal(signal.SIGINT, lambda signum, _frame: _request_server_shutdown(server, signum))
    logger.info("ODS Host Agent v%s listening on %s:%d", VERSION, bind_addr, port)
    if bind_addr == "0.0.0.0":
        logger.info(
            "Bound to all interfaces. Bearer-auth (ODS_AGENT_KEY) is enforced "
            "on every endpoint. To restrict to a specific interface, set "
            "ODS_AGENT_BIND=<ip> in %s/.env.",
            INSTALL_DIR,
        )
    logger.info("Install dir: %s | GPU: %s | Tier: %s", INSTALL_DIR, GPU_BACKEND, TIER)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
