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
import hashlib
import importlib
import json
import logging
import os
import platform
import re
import secrets
import shlex
import shutil
import signal
import socket
import stat as stat_mod
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib import error as urllib_error, request as urllib_request
from urllib.parse import parse_qs, unquote, urlparse

# Model Switchboard (PR 1, observe mode): stdlib-only sibling package. The
# import is fail-open — a missing/broken package disables state recording but
# never the agent itself.
_SWITCHBOARD_BIN_DIR = str(Path(__file__).resolve().parent)
if _SWITCHBOARD_BIN_DIR not in sys.path:
    sys.path.insert(0, _SWITCHBOARD_BIN_DIR)
try:
    from model_switchboard import state as _switchboard_state
except Exception:  # pragma: no cover - import environment dependent
    _switchboard_state = None
try:
    from model_switchboard import adapters as _switchboard_adapters
    from model_switchboard import reconciler as _switchboard_reconciler
except Exception:  # pragma: no cover - import environment dependent
    _switchboard_adapters = None
    _switchboard_reconciler = None

VERSION = "1.0.0"
ODS_VERSION = VERSION
SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
# backup_id is interpolated into a backup directory name by ods-update.sh
# (BACKUP_DIR/backup-<backup_id>-<ts>). Restrict it to a plain label so it can
# never contain a path separator or ".." and escape BACKUP_DIR.
BACKUP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_BODY = 16384
MAX_TELEMETRY_RESPONSE_BYTES = 1024 * 1024
SUBPROCESS_TIMEOUT_START = 600  # 10 min — image pulls can be slow
SUBPROCESS_TIMEOUT_STOP = 120   # 2 min — stop should be fast
HOOK_TIMEOUT = 120              # 2 min — hook execution timeout
VALID_HOOK_NAMES = frozenset({
    "pre_install", "post_install", "pre_start", "post_start",
    "pre_uninstall", "post_uninstall",
})
logger = logging.getLogger("ods-host-agent")

_MACOS_LLM_BRIDGE_LABEL = "com.ods.llm-bridge"
_MACOS_HOST_AGENT_BRIDGE_LABEL = "com.ods.host-agent-bridge"

# Hardcoded fallback — used when core-service-ids.json is missing or unreadable.
# Prevents fail-open: without this, a missing JSON file would allow anyone with
# the API key to stop core services like llama-server or dashboard-api.
_FALLBACK_CORE_IDS = frozenset({
    "dashboard-api", "dashboard", "llama-server", "model-router", "open-webui",
    "litellm", "langfuse", "hermes", "hermes-proxy", "n8n", "openclaw", "opencode",
    "perplexica", "searxng", "qdrant", "tts", "whisper",
    "embeddings", "token-spy", "comfyui", "ape", "privacy-shield",
})

INSTALL_DIR: Path = Path()
DATA_DIR: Path = Path()
AGENT_API_KEY: str = ""
GPU_BACKEND: str = "nvidia"
STARTUP_ODS_MODE: str | None = None
TIER: str = "1"
GPU_COUNT: str = "1"
CORE_SERVICE_IDS: set = set()
_windows_gpu_metrics_cache: tuple[float, dict | None] = (0.0, None)
_windows_dxgi_adapters_cache: tuple[float, list[dict]] = (0.0, [])
_windows_llm_status_cache: tuple[float, dict | None] = (0.0, None)
_service_health_cache: tuple[float, dict | None] = (0.0, None)
_windows_gpu_metrics_lock = threading.Lock()
_windows_llm_status_lock = threading.Lock()
_service_health_lock = threading.Lock()
WINDOWS_WHISPER_CUDA_MIN_DRIVER_MAJOR = 575
# Always-on services defined in docker-compose.base.yml — never stoppable via API.
# Distinct from CORE_SERVICE_IDS (which is the allowlist of known service IDs).
ALWAYS_ON_SERVICES: frozenset = frozenset({
    "llama-server", "model-router", "open-webui", "dashboard", "dashboard-api",
})
USER_EXTENSIONS_DIR: Path = Path()
EXTENSIONS_DIR: Path = Path()
_ODS_MODES = frozenset({"local", "cloud", "hybrid", "lemonade"})
_LOCAL_MODEL_MODES = frozenset({"local", "hybrid", "lemonade"})
_MODEL_TIER_RE = re.compile(r"^[A-Z0-9_]{1,32}$")
_MODEL_TIERS = frozenset({
    "0", "1", "2", "3", "4", "ARC", "ARC_LITE",
    "NV_ULTRA", "SH_COMPACT", "SH_LARGE",
})
_MIN_MODEL_CONTEXT = 1024
_MAX_MODEL_CONTEXT = 1048576

# Per-service locks to prevent concurrent start+stop races on the same service
_service_locks: dict[str, threading.Lock] = collections.defaultdict(threading.Lock)
_ALLOWED_CORE_RECREATE_IDS = frozenset({
    "llama-server", "open-webui", "litellm", "langfuse", "n8n",
    "hermes", "hermes-proxy", "openclaw", "opencode", "perplexica", "searxng", "qdrant",
    "tts", "whisper", "embeddings", "token-spy", "comfyui",
    "ape", "privacy-shield", "model-router",
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


def _nvidia_driver_major() -> int:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 0

    if result.returncode != 0:
        return 0
    first = (result.stdout or "").strip().splitlines()
    if not first:
        return 0
    match = re.match(r"^(\d+)", first[0].strip())
    return int(match.group(1)) if match else 0


def _windows_whisper_cuda_supported(env: dict) -> bool:
    if platform.system() != "Windows":
        return True
    gpu_backend = str(env.get("GPU_BACKEND") or GPU_BACKEND or "").lower()
    if gpu_backend != "nvidia":
        return False

    acceleration = str(env.get("WHISPER_ACCELERATION") or "").strip().lower()
    image = str(env.get("WHISPER_IMAGE") or "").strip().lower()
    if acceleration == "cpu" or (image and "cpu" in image):
        return False

    return _nvidia_driver_major() >= WINDOWS_WHISPER_CUDA_MIN_DRIVER_MAJOR


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
_model_download_cancelable = False
_model_status_lock = threading.Lock()
# Model lifecycle ownership serializes operations that read or mutate model
# artifacts, active routing, or the runtime containers. Keep the historical
# activation-lock name as an alias because env updates use the same boundary.
_model_lifecycle_lock = threading.Lock()
_model_activate_lock = _model_lifecycle_lock
_model_lifecycle_state_lock = threading.Lock()
_model_lifecycle_operation: str | None = None
_model_lifecycle_target: str | None = None
_model_activation_target: str | None = None
_model_status_verify_thread: threading.Thread | None = None
_switchboard_initial_verify_lock = threading.Lock()
_switchboard_initial_verify_thread: threading.Thread | None = None
_switchboard_initial_verify_cancel = threading.Event()
# Update lock/state: only one background ods-update run at a time.
_update_lock = threading.Lock()
_update_status_lock = threading.Lock()
_update_thread: threading.Thread | None = None
_update_usable_bash: str | bool | None = None
_usable_bash: str | bool | None = None


def _model_download_thread_alive() -> bool:
    thread = _model_download_thread
    return bool(thread is not None and thread.is_alive())


def _begin_model_lifecycle(operation: str, target: str = "") -> tuple[bool, dict]:
    """Claim the process-wide model lifecycle boundary without waiting."""
    global _model_lifecycle_operation, _model_lifecycle_target
    with _model_lifecycle_state_lock:
        if not _model_lifecycle_lock.acquire(blocking=False):
            return False, {
                "operation": _model_lifecycle_operation,
                "target": _model_lifecycle_target,
            }
        _model_lifecycle_operation = operation
        _model_lifecycle_target = target or None
        return True, {"operation": operation, "target": target or None}


def _end_model_lifecycle(operation: str) -> None:
    """Release lifecycle ownership held by ``operation``."""
    global _model_lifecycle_operation, _model_lifecycle_target
    with _model_lifecycle_state_lock:
        if _model_lifecycle_operation != operation:
            logger.error(
                "Model lifecycle release mismatch: owner=%s releaser=%s",
                _model_lifecycle_operation,
                operation,
            )
        _model_lifecycle_operation = None
        _model_lifecycle_target = None
        _model_lifecycle_lock.release()


def _model_lifecycle_conflict(requested_operation: str, active: dict) -> dict:
    active_operation = active.get("operation") or "another lifecycle operation"
    payload = {
        "error": (
            f"Cannot start {requested_operation} while {active_operation} is in progress"
        ),
        "code": "model_lifecycle_busy",
        "activeOperation": active.get("operation"),
        "activeTarget": active.get("target"),
    }
    return payload


def _model_lifecycle_status() -> dict:
    """Return the currently-owned model lifecycle operation, if any."""
    with _model_lifecycle_state_lock:
        operation = _model_lifecycle_operation
        target = _model_lifecycle_target
        activation_target = _model_activation_target
    if not operation:
        return {}
    payload = {
        "lifecycleActive": True,
        "activeOperation": operation,
        "activeTarget": target,
    }
    if operation == "model_activation":
        payload["activeModelId"] = activation_target or target
    else:
        payload["activeModelId"] = None
    return payload


def _prepare_initial_switchboard_verification() -> bool:
    """Reset route-proof cancellation only while no lifecycle owner exists."""
    with _model_lifecycle_state_lock:
        if _model_lifecycle_operation:
            _switchboard_initial_verify_cancel.set()
            return False
        _switchboard_initial_verify_cancel.clear()
        return True


def _begin_model_activation(model_id: str) -> tuple[bool, str | None]:
    """Atomically acquire activation ownership and publish its target."""
    global _model_activation_target
    acquired, active = _begin_model_lifecycle("model_activation", model_id)
    if not acquired:
        active_target = active.get("target")
        return False, active_target if active.get("operation") == "model_activation" else None
    # Initial route reconstruction is observational work. Once an explicit
    # activation owns the lifecycle it must stop probing/warming the previous
    # route, or those requests can race and starve the selected model load.
    _switchboard_initial_verify_cancel.set()
    with _model_lifecycle_state_lock:
        _model_activation_target = model_id
        return True, model_id


def _end_model_activation() -> None:
    """Clear activation ownership before making the lock available again."""
    global _model_activation_target
    with _model_lifecycle_state_lock:
        _model_activation_target = None
    _end_model_lifecycle("model_activation")


def _download_status_model_token(value: object) -> str:
    """Return the catalog filename embedded in a progress label."""
    return str(value or "").split(" (", 1)[0].strip()


def _format_curl_download_error(returncode: int | None, stderr_text: object) -> str:
    """Return a bounded user-facing curl failure with the useful server reason."""
    message = f"curl exited with code {returncode}"
    details = str(stderr_text or "").strip()
    if not details:
        return message
    details = " ".join(details.split())
    if len(details) > 500:
        details = details[:497].rstrip() + "..."
    return f"{message}: {details}"


def _parse_huggingface_resolve_url(url: object) -> tuple[str, str, str] | None:
    """Return repo_id, revision, and filename for a Hugging Face resolve URL."""
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.netloc.lower() not in {"huggingface.co", "www.huggingface.co", "hf.co"}:
        return None
    parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 5 or parts[2] != "resolve":
        return None
    repo_id = f"{parts[0]}/{parts[1]}"
    revision = parts[3]
    filename = "/".join(parts[4:])
    if not repo_id or not revision or not filename:
        return None
    return repo_id, revision, filename


def _positive_int_env(name: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < minimum:
        return minimum
    if maximum is not None and value > maximum:
        return maximum
    return value


def _download_huggingface_artifact(
    part_url: str,
    part_tmp: Path,
    cancel_event: threading.Event,
    *,
    status_path: Path | None = None,
    status_label: str = "",
    part_total: int = 0,
    status_error: str = "",
) -> tuple[bool, str]:
    """Download a Hugging Face artifact with huggingface_hub for Xet-backed files."""
    global _model_download_proc
    parsed = _parse_huggingface_resolve_url(part_url)
    if parsed is None:
        return False, "not a Hugging Face resolve URL"

    repo_id, revision, filename = parsed
    cache_dir = INSTALL_DIR / "data" / "hf-cache"
    fallback_timeout = _positive_int_env(
        "ODS_HF_HUB_FALLBACK_TIMEOUT_SECONDS",
        2700,
        minimum=30,
        maximum=14400,
    )
    heartbeat_seconds = _positive_int_env(
        "ODS_HF_HUB_FALLBACK_STATUS_SECONDS",
        10,
        minimum=2,
        maximum=120,
    )
    response_timeout = _positive_int_env(
        "ODS_HF_HUB_RESPONSE_TIMEOUT_SECONDS",
        30,
        minimum=10,
        maximum=300,
    )
    code = r'''
import shutil
import sys
from pathlib import Path

repo_id, revision, filename, dest, cache_dir = sys.argv[1:6]
try:
    from huggingface_hub import hf_hub_download
except Exception as exc:
    print(
        "huggingface_hub is not installed; install with: "
        "python -m pip install 'huggingface_hub[hf_xet]'",
        file=sys.stderr,
    )
    raise

path = hf_hub_download(
    repo_id=repo_id,
    filename=filename,
    revision=revision,
    cache_dir=cache_dir,
    local_files_only=False,
)
dest_path = Path(dest)
dest_path.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(path, dest_path)
'''
    cmd = [
        sys.executable,
        "-c",
        code,
        repo_id,
        revision,
        filename,
        str(part_tmp),
        str(cache_dir),
    ]
    try:
        child_env = os.environ.copy()
        persisted_hf_token = load_env(INSTALL_DIR / ".env").get("HF_TOKEN", "").strip()
        if persisted_hf_token and not child_env.get("HF_TOKEN"):
            child_env["HF_TOKEN"] = persisted_hf_token
        child_env.setdefault("HF_HUB_ETAG_TIMEOUT", str(response_timeout))
        child_env.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", str(response_timeout))
        logger.info(
            "Model download falling back to Hugging Face Hub for %s from %s",
            filename,
            repo_id,
        )
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=child_env,
        )
        _model_download_proc = proc
        stop_status = threading.Event()
        heartbeat_thread = None

        if status_path is not None:
            label = status_label or Path(filename).name
            status_message = (
                f"{status_error}; Hugging Face Hub fallback active"
                if status_error
                else "Hugging Face Hub fallback active"
            )

            def _heartbeat_status() -> None:
                while not stop_status.is_set():
                    if cancel_event.is_set():
                        try:
                            proc.kill()
                        except (OSError, AttributeError):
                            pass
                    try:
                        current = part_tmp.stat().st_size if part_tmp.exists() else 0
                    except OSError:
                        current = 0
                    _write_model_status(
                        status_path,
                        "downloading",
                        label,
                        current,
                        part_total,
                        status_message,
                    )
                    stop_status.wait(heartbeat_seconds)

            heartbeat_thread = threading.Thread(target=_heartbeat_status, daemon=True)
            heartbeat_thread.start()
        timed_out = False
        try:
            stdout_text, stderr_text = proc.communicate(timeout=fallback_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            timeout_error = f"Hugging Face Hub fallback timed out after {fallback_timeout}s"
            try:
                stdout_text, stderr_text = proc.communicate(timeout=5)
                stderr_text = stderr_text or timeout_error
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
                stdout_text, stderr_text = "", timeout_error
        finally:
            stop_status.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=1)
            _model_download_proc = None
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Hugging Face Hub fallback could not start: {exc}"

    if cancel_event.is_set():
        return False, "Download cancelled by user"
    if timed_out:
        logger.warning("Model download Hugging Face Hub fallback timed out for %s", filename)
        return False, stderr_text or f"Hugging Face Hub fallback timed out after {fallback_timeout}s"
    if proc.returncode == 0 and _model_file_ready(part_tmp):
        return True, ""
    details = stderr_text or stdout_text
    if proc.returncode == 0:
        return False, "Hugging Face Hub fallback finished but model file is missing or empty"
    return False, _format_curl_download_error(proc.returncode, details).replace(
        "curl exited",
        "Hugging Face Hub fallback exited",
        1,
    )


def _artifact_expected_size(metadata: dict) -> int | None:
    """Return an exact catalog byte size when one is available."""
    for key in ("size_bytes", "expected_size_bytes", "file_size_bytes"):
        raw = metadata.get(key)
        if isinstance(raw, bool) or raw in (None, ""):
            continue
        try:
            size = int(raw)
        except (TypeError, ValueError):
            continue
        if size > 0:
            return size
    return None


def _model_download_manifest(model: dict) -> dict | None:
    """Build the complete integrity manifest for one catalog model."""
    gguf_file = str(model.get("gguf_file") or "").strip()
    if not gguf_file:
        return None

    raw_parts = model.get("gguf_parts")
    artifacts = []
    if isinstance(raw_parts, list) and raw_parts:
        for raw_part in raw_parts:
            if not isinstance(raw_part, dict):
                return None
            filename = str(raw_part.get("file") or "").strip()
            url = str(raw_part.get("url") or "").strip()
            if not filename or not url:
                return None
            artifacts.append({
                "file": filename,
                "url": url,
                "sha256": str(raw_part.get("sha256") or "").strip().lower(),
                "size_bytes": _artifact_expected_size(raw_part),
            })
    else:
        url = str(model.get("gguf_url") or "").strip()
        if not url:
            return None
        artifacts.append({
            "file": gguf_file,
            "url": url,
            "sha256": str(model.get("gguf_sha256") or "").strip().lower(),
            "size_bytes": _artifact_expected_size(model),
        })

    filenames = [artifact["file"] for artifact in artifacts]
    if gguf_file not in filenames or len(filenames) != len(set(filenames)):
        return None
    return {"gguf_file": gguf_file, "artifacts": artifacts}


def _load_model_library_records() -> list[dict]:
    """Load curated models plus the dashboard's integrity-pinned Hub imports."""
    catalog_path = INSTALL_DIR / "config" / "model-library.json"
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError) as exc:
        raise RuntimeError("Model catalog unavailable") from exc
    curated = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(curated, list) or not all(isinstance(item, dict) for item in curated):
        raise RuntimeError("Model catalog unavailable")

    merged = list(curated)
    seen_ids = {str(item.get("id") or "") for item in curated}
    seen_files = {str(item.get("gguf_file") or "").lower() for item in curated}
    imported_path = INSTALL_DIR / "data" / "model-imports.json"
    if not imported_path.exists():
        return merged
    try:
        imported_payload = json.loads(imported_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError) as exc:
        raise RuntimeError(f"Model import registry unavailable: {exc}") from exc
    imported = imported_payload.get("models") if isinstance(imported_payload, dict) else None
    if not isinstance(imported, list) or not all(isinstance(item, dict) for item in imported):
        raise RuntimeError("Model import registry must contain a models array of objects")

    for item in imported:
        model_id = str(item.get("id") or "")
        filename = str(item.get("gguf_file") or "").lower()
        if item.get("source") != "huggingface" or not model_id or not filename:
            raise RuntimeError("Model import registry contains an invalid Hugging Face record")
        if model_id in seen_ids or filename in seen_files:
            raise RuntimeError("Model import registry collides with an existing model")
        if _model_download_manifest(item) is None:
            raise RuntimeError(f"Model import registry contains an invalid manifest for {model_id}")
        merged.append(item)
        seen_ids.add(model_id)
        seen_files.add(filename)
    return merged


def _safe_model_artifact_path(models_dir: Path, filename: object) -> Path | None:
    """Resolve a catalog artifact while keeping it directly in models_dir."""
    token = str(filename or "").strip()
    if (
        not token
        or "\x00" in token
        or "/" in token
        or "\\" in token
        or Path(token).name != token
    ):
        return None
    try:
        root = models_dir.resolve()
        target = (models_dir / token).resolve()
        if not target.is_relative_to(root):
            return None
    except (OSError, RuntimeError):
        return None
    return target


def _verify_model_artifact(
    path: Path,
    artifact: dict,
    cancel_event: threading.Event | None = None,
) -> tuple[bool, str]:
    """Verify one model artifact against exact catalog integrity metadata."""
    try:
        if not path.is_file():
            return False, "file is missing"
        actual_size = path.stat().st_size
    except OSError as exc:
        return False, f"file could not be inspected: {exc}"
    if actual_size <= 0:
        return False, "file is empty"

    expected_size = artifact.get("size_bytes")
    if expected_size is not None and actual_size != expected_size:
        return False, f"size mismatch: expected {expected_size} bytes, got {actual_size}"

    expected_sha = str(artifact.get("sha256") or "").strip().lower()
    if expected_sha:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            return False, "catalog SHA256 is malformed"
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1048576), b""):
                    if cancel_event is not None and cancel_event.is_set():
                        return False, "verification cancelled"
                    digest.update(chunk)
        except OSError as exc:
            return False, f"file could not be hashed: {exc}"
        actual_sha = digest.hexdigest()
        if actual_sha != expected_sha:
            return (
                False,
                f"SHA256 mismatch: expected {expected_sha[:12]}..., got {actual_sha[:12]}...",
            )
    elif expected_size is None:
        return False, "catalog has no exact size or SHA256"

    if cancel_event is not None and cancel_event.is_set():
        return False, "verification cancelled"
    return True, ""


def _verify_model_manifest(
    models_dir: Path,
    manifest: dict,
    cancel_event: threading.Event | None = None,
) -> tuple[bool, str]:
    """Verify every file in a catalog model manifest."""
    for artifact in manifest.get("artifacts", []):
        filename = artifact.get("file", "")
        target = _safe_model_artifact_path(models_dir, filename)
        if target is None:
            return False, f"unsafe catalog filename: {filename!r}"
        valid, reason = _verify_model_artifact(target, artifact, cancel_event)
        if not valid:
            return False, f"{filename}: {reason}"
    return True, ""


def _catalog_manifest_for_status(model_label: object) -> tuple[dict | None, str]:
    """Resolve a stale status label to its complete catalog manifest."""
    token = _download_status_model_token(model_label)
    try:
        models = _load_model_library_records()
    except RuntimeError as exc:
        return None, str(exc)
    for model in models:
        if not isinstance(model, dict):
            continue
        manifest = _model_download_manifest(model)
        if manifest is None:
            continue
        filenames = {artifact["file"] for artifact in manifest["artifacts"]}
        if token == manifest["gguf_file"] or token in filenames:
            return manifest, ""
    return None, f"no catalog manifest matches {token or 'the stale download'}"


def _read_model_status(path: Path) -> dict:
    with _model_status_lock:
        return json.loads(path.read_text(encoding="utf-8"))


def _normalize_model_download_status(status_path: Path, data: dict) -> dict:
    """Schedule single-flight verification for status left by a dead worker."""
    global _model_status_verify_thread
    status = str(data.get("status") or "")
    if status not in {"downloading", "verifying"}:
        return data
    if _model_download_thread_alive():
        return data

    model = _download_status_model_token(data.get("model"))
    manifest, manifest_error = _catalog_manifest_for_status(model)
    if manifest is None:
        _write_model_status(
            status_path,
            "failed",
            model,
            int(data.get("bytesDownloaded") or 0),
            int(data.get("bytesTotal") or 0),
            data.get("error")
            or (
                "Model download is not running; previous download is incomplete or corrupt: "
                f"{manifest_error}"
            ),
        )
    else:
        model = manifest["gguf_file"]
        acquired, _active = _begin_model_lifecycle("artifact_verification", model)
        if not acquired:
            return data

        downloaded = int(data.get("bytesDownloaded") or 0)
        total = int(data.get("bytesTotal") or 0)
        _write_model_status(status_path, "verifying", model, downloaded, total)

        def _verify_stale_manifest() -> None:
            try:
                models_dir = INSTALL_DIR / "data" / "models"
                manifest_valid, integrity_error = _verify_model_manifest(
                    models_dir,
                    manifest,
                )
                if manifest_valid:
                    _write_model_status(status_path, "complete", model, 0, 0)
                else:
                    _write_model_status(
                        status_path,
                        "failed",
                        model,
                        downloaded,
                        total,
                        data.get("error")
                        or (
                            "Model download is not running; previous download is "
                            f"incomplete or corrupt: {integrity_error}"
                        ),
                    )
            except Exception as exc:
                logger.exception("Stale model artifact verification failed")
                _write_model_status(
                    status_path,
                    "failed",
                    model,
                    downloaded,
                    total,
                    f"Stale model verification failed: {exc}",
                )
            finally:
                _end_model_lifecycle("artifact_verification")

        try:
            _model_status_verify_thread = threading.Thread(
                target=_verify_stale_manifest,
                daemon=True,
            )
            _model_status_verify_thread.start()
        except Exception:
            _end_model_lifecycle("artifact_verification")
            raise
    try:
        return _read_model_status(status_path)
    except (json.JSONDecodeError, OSError):
        return {"status": "idle"}


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


def _switchboard_state_path() -> Path:
    return INSTALL_DIR / "data" / "model-state.json"


def _switchboard_state_needs_initial_verification(path: Path) -> bool:
    if _switchboard_state is None:
        return False
    doc, errors = _switchboard_state.read_state(path)
    if errors or not isinstance(doc, dict):
        return False
    active = doc.get("active")
    if not isinstance(active, dict):
        return False
    proof = active.get("proof")
    return (
        active.get("reconstructed") is True
        or not isinstance(active.get("verifiedAt"), str)
        or not active.get("verifiedAt")
        or not isinstance(proof, dict)
        or proof.get("completion") is not True
    )


def _switchboard_state_needs_current_env_verification(
    path: Path,
    env: dict | None = None,
) -> bool:
    """Return True when the verified route is absent or stale versus .env."""
    if _switchboard_state is None:
        return False
    if env is None:
        env = load_env(INSTALL_DIR / ".env")
    identity = _switchboard_state.migrate_env_identity(env)
    if not identity:
        return False
    doc, errors = _switchboard_state.read_state(path)
    if errors or not isinstance(doc, dict):
        return False
    active = doc.get("active")
    if not isinstance(active, dict):
        return True
    proof = active.get("proof")
    if (
        active.get("reconstructed") is True
        or not isinstance(active.get("verifiedAt"), str)
        or not active.get("verifiedAt")
        or not isinstance(proof, dict)
        or proof.get("completion") is not True
    ):
        return True

    gguf_file = str(env.get("GGUF_FILE") or identity["runtimeModelId"])
    llm_model_name = str(env.get("LLM_MODEL") or identity["catalogId"])
    model_id, _model = _catalog_model_for_current_env(env)
    if not _runtime_model_identity_matches(
        active.get("runtimeModelId"),
        model_id=model_id or identity["catalogId"],
        gguf_file=gguf_file,
        llm_model_name=llm_model_name,
    ):
        return True

    backend_kind, endpoint_id, _native_route = _initial_switchboard_backend(env)
    backend = active.get("backend")
    return not (
        isinstance(backend, dict)
        and backend.get("kind") == backend_kind
        and backend.get("endpointId") == endpoint_id
    )


def _catalog_model_for_current_env(env: dict) -> tuple[str, dict]:
    gguf_file = str(env.get("GGUF_FILE") or "").strip()
    llm_model_name = str(env.get("LLM_MODEL") or "").strip()
    try:
        library = _load_model_library_records()
    except RuntimeError:
        library = []
    for entry in library:
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("id") or "")
        entry_gguf = str(entry.get("gguf_file") or "")
        entry_llm = str(entry.get("llm_model_name") or entry_id)
        if (
            (llm_model_name and entry_id == llm_model_name)
            or (llm_model_name and entry_llm == llm_model_name)
            or (gguf_file and entry_gguf == gguf_file)
        ):
            return entry_id or llm_model_name or gguf_file, entry
    return llm_model_name or gguf_file, {}


def _initial_switchboard_backend(env: dict) -> tuple[str, str, str | None]:
    gpu_backend = str(env.get("GPU_BACKEND") or "nvidia").lower()
    windows_native_llama = _is_windows_host_llama_server(env)
    if gpu_backend == "amd" and not windows_native_llama:
        lemonade_model_id = str(env.get("LEMONADE_MODEL") or "").strip()
        if not lemonade_model_id:
            lemonade_model_id = _resolve_lemonade_model_id(
                env,
                str(env.get("GGUF_FILE") or ""),
            )
        return "lemonade", "lemonade-default", lemonade_model_id or None
    return "llama-server", "llama-server-default", None


def _publish_verified_initial_switchboard_route(
    *,
    reason: str,
    attempts: int = 180,
    initial_delay: float = 0,
    interval: float = 10,
) -> bool:
    """Promote current .env route only after runtime proof succeeds."""
    if _switchboard_state is None:
        return False
    if not _prepare_initial_switchboard_verification():
        logger.info(
            "switchboard initial route proof deferred (%s; model lifecycle active)",
            reason,
        )
        return False
    state_path = _switchboard_state_path()
    env = load_env(INSTALL_DIR / ".env")
    if not _switchboard_state_needs_current_env_verification(state_path, env):
        return False

    identity = _switchboard_state.migrate_env_identity(env)
    if not identity:
        return False

    gguf_file = str(env.get("GGUF_FILE") or identity["runtimeModelId"])
    llm_model_name = str(env.get("LLM_MODEL") or identity["catalogId"])
    if not gguf_file:
        return False

    model_id, model = _catalog_model_for_current_env(env)
    backend_kind, endpoint_id, native_route = _initial_switchboard_backend(env)
    proof = _wait_for_model_readiness(
        env,
        model_id=model_id or llm_model_name or gguf_file,
        gguf_file=gguf_file,
        llm_model_name=llm_model_name,
        lemonade_model_id=native_route or "",
        attempts=attempts,
        initial_delay=initial_delay,
        interval=interval,
        return_proof=True,
        cancel_event=_switchboard_initial_verify_cancel,
    )
    if not isinstance(proof, dict) or not proof.get("identity"):
        logger.info("switchboard initial route proof deferred (%s)", reason)
        return False

    fresh_env = load_env(INSTALL_DIR / ".env")
    if (
        str(fresh_env.get("GGUF_FILE") or "") != str(env.get("GGUF_FILE") or "")
        or str(fresh_env.get("LLM_MODEL") or "") != str(env.get("LLM_MODEL") or "")
    ):
        logger.info("switchboard initial route proof discarded after env changed")
        return False
    if not _switchboard_state_needs_current_env_verification(state_path, fresh_env):
        return False

    runtime_identity = str(proof["identity"])
    if not _runtime_model_identity_matches(
        runtime_identity,
        model_id=model_id,
        gguf_file=gguf_file,
        llm_model_name=llm_model_name,
    ):
        logger.warning(
            "switchboard initial route proof identity %s did not match %s",
            runtime_identity,
            gguf_file,
        )
        return False

    context_length = int(proof.get("contextLength") or identity.get("contextLength") or 0)
    if backend_kind == "lemonade":
        native_route = runtime_identity
    capabilities = {
        "chat": True,
        "tools": bool(model.get("tools")),
        "vision": bool(model.get("vision")),
        "agentViable": _model_agent_viable(model, context_length),
    }
    _switchboard_state.record_verified_route(
        state_path,
        catalog_id=model_id or llm_model_name or gguf_file,
        runtime_model_id=runtime_identity,
        backend_kind=backend_kind,
        endpoint_id=endpoint_id,
        native_route=native_route,
        context_length=context_length,
        capabilities=capabilities,
        proof_identity=runtime_identity,
    )
    logger.info(
        "switchboard initial route verified (%s): %s",
        reason,
        runtime_identity,
    )
    return True


def _schedule_initial_switchboard_verification(reason: str) -> None:
    global _switchboard_initial_verify_thread
    if _switchboard_state is None:
        return
    state_path = _switchboard_state_path()
    if not _switchboard_state_needs_current_env_verification(state_path):
        return
    with _switchboard_initial_verify_lock:
        thread = _switchboard_initial_verify_thread
        if thread is not None and thread.is_alive():
            return

        def _run() -> None:
            try:
                _publish_verified_initial_switchboard_route(reason=reason)
            except Exception as exc:
                logger.warning("switchboard initial route verification failed: %s", exc)

        _switchboard_initial_verify_thread = threading.Thread(
            target=_run,
            name="ods-switchboard-initial-verify",
            daemon=True,
        )
        _switchboard_initial_verify_thread.start()


def _bootstrap_status_allows_route_proof() -> bool:
    status_path = INSTALL_DIR / "data" / "bootstrap-status.json"
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(data, dict):
        return False
    return str(data.get("status") or "").strip().casefold() in {
        "swapping",
        "complete",
    }


def _model_status_allows_route_proof(data: dict) -> bool:
    status = str(data.get("status") or "").strip().casefold()
    if status in {"already_downloaded", "complete"}:
        return True
    return _bootstrap_status_allows_route_proof()


def _verify_switchboard_route_for_status(data: dict, reason: str) -> None:
    if _switchboard_state is None:
        return
    if _model_lifecycle_status():
        _switchboard_initial_verify_cancel.set()
        return
    state_path = _switchboard_state_path()
    if not _switchboard_state_needs_current_env_verification(state_path):
        return
    if _model_status_allows_route_proof(data):
        # A read-only status request must never perform a potentially 30-second
        # Lemonade warm-up inline. The single background worker de-duplicates
        # concurrent dashboard polls and is cancelled by explicit lifecycle
        # operations.
        _schedule_initial_switchboard_verification(reason)


def _atomic_write_bytes(
    path: Path,
    content: bytes,
    mode: int | None = None,
    uid: int | None = None,
    gid: int | None = None,
) -> None:
    """Durably replace a regular file without following a leaf symlink."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = None
    existing_uid = None
    existing_gid = None
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RuntimeError(f"Could not inspect {path} before writing: {exc}") from exc
    else:
        if stat_mod.S_ISLNK(metadata.st_mode):
            raise RuntimeError(f"Refusing to replace symlinked configuration file: {path}")
        if not stat_mod.S_ISREG(metadata.st_mode):
            raise RuntimeError(f"Refusing to replace non-regular configuration file: {path}")
        existing_mode = stat_mod.S_IMODE(metadata.st_mode)
        existing_uid = metadata.st_uid
        existing_gid = metadata.st_gid

    write_mode = mode if mode is not None else (
        existing_mode if existing_mode is not None else 0o600
    )
    descriptor, raw_tmp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(raw_tmp_path)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, write_mode)
        if os.name != "nt":
            target_uid = uid if uid is not None else existing_uid
            target_gid = gid if gid is not None else existing_gid
            if target_uid is not None and target_gid is not None:
                temp_metadata = tmp_path.stat()
                if (temp_metadata.st_uid, temp_metadata.st_gid) != (
                    target_uid,
                    target_gid,
                ):
                    os.chown(tmp_path, target_uid, target_gid)
        last_error: PermissionError | None = None
        for attempt in range(10):
            try:
                os.replace(tmp_path, path)
                last_error = None
                break
            except PermissionError as exc:
                last_error = exc
                if attempt == 9:
                    break
                # Windows can briefly hold configuration files open while
                # dashboard-api polls host-agent during model activation.
                time.sleep(0.05 * (attempt + 1))
        if last_error is not None:
            raise last_error
        if os.name != "nt":
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            try:
                directory_fd = os.open(path.parent, directory_flags)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError as exc:
                logger.warning("Could not fsync configuration directory %s: %s", path.parent, exc)
    finally:
        tmp_path.unlink(missing_ok=True)


def _atomic_write_text(
    path: Path,
    text: str,
    mode: int | None = None,
    uid: int | None = None,
    gid: int | None = None,
) -> None:
    """Atomically replace a UTF-8 text file."""
    _atomic_write_bytes(path, text.encode("utf-8"), mode, uid, gid)


def _snapshot_text_file(path: Path) -> dict:
    """Capture bytes/mode/existence for exact transactional restoration."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {
            "exists": False,
            "text": None,
            "mode": None,
            "uid": None,
            "gid": None,
        }
    except OSError as exc:
        raise RuntimeError(f"Could not snapshot {path}: {exc}") from exc
    if stat_mod.S_ISLNK(metadata.st_mode):
        raise RuntimeError(f"Refusing to mutate symlinked configuration file: {path}")
    if not stat_mod.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"Refusing to mutate non-regular configuration file: {path}")
    try:
        content = path.read_bytes()
        text = content.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"Could not snapshot {path}: {exc}") from exc
    return {
        "exists": True,
        "text": text,
        "bytes": content,
        "mode": stat_mod.S_IMODE(metadata.st_mode),
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
    }


def _restore_text_file(path: Path, snapshot: dict) -> None:
    """Restore a text-file snapshot, including prior absence and mode."""
    if snapshot.get("exists"):
        content = snapshot.get("bytes")
        if not isinstance(content, bytes):
            content = str(snapshot.get("text") or "").encode("utf-8")
        _atomic_write_bytes(
            path,
            content,
            int(snapshot["mode"] if snapshot.get("mode") is not None else 0o600),
            snapshot.get("uid"),
            snapshot.get("gid"),
        )
    else:
        if path.is_symlink():
            raise RuntimeError(f"Refusing to remove unexpected symlink during rollback: {path}")
        path.unlink(missing_ok=True)


def _assert_text_file_matches_snapshot(path: Path, snapshot: dict) -> None:
    """Fail before mutation when a captured config changed concurrently."""
    current = _snapshot_text_file(path)
    if bool(current.get("exists")) != bool(snapshot.get("exists")):
        raise RuntimeError(f"Configuration changed during model activation: {path}")
    if not current.get("exists"):
        return
    expected = snapshot.get("bytes")
    if not isinstance(expected, bytes):
        expected = str(snapshot.get("text") or "").encode("utf-8")
    if (
        current.get("bytes") != expected
        or current.get("mode") != snapshot.get("mode")
        or current.get("uid") != snapshot.get("uid")
        or current.get("gid") != snapshot.get("gid")
    ):
        raise RuntimeError(f"Configuration changed during model activation: {path}")


def _upsert_env_value(env_path: Path, key: str, value: str) -> None:
    """Persist one simple ``KEY=value`` entry without disturbing other lines."""
    if any(character in value for character in "\r\n\x00"):
        raise ValueError(f"Invalid newline or NUL in {key}")
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    output = []
    written = False
    for line in lines:
        line_key = line.split("=", 1)[0] if "=" in line and not line.startswith("#") else None
        if line_key == key:
            if not written:
                output.append(f"{key}={value}")
                written = True
            continue
        output.append(line)
    if not written:
        output.append(f"{key}={value}")
    _atomic_write_text(env_path, "\n".join(output) + "\n")


def _write_activation_config_file(path: Path, content: str) -> None:
    """Write an install-owned config file, repairing directory/file confusion."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_dir():
        shutil.rmtree(path)
    tmp = path.with_name(f".{path.name}.tmp")
    if tmp.exists() and tmp.is_dir():
        shutil.rmtree(tmp)
    tmp.write_text(content, encoding="utf-8")
    os.replace(str(tmp), str(path))


def _normalize_ods_mode(value) -> str:
    """Return a supported ODS mode or ``unknown`` for missing/invalid input."""
    mode = str(value or "").strip().lower()
    return mode if mode in _ODS_MODES else "unknown"


def _model_activation_modes(persisted_env: dict) -> tuple[str, str]:
    """Return immutable startup mode and current persisted configured mode."""
    configured_mode = _normalize_ods_mode(persisted_env.get("ODS_MODE"))
    if STARTUP_ODS_MODE is None:
        # Direct unit calls predate startup-mode initialization. Keep their
        # historical local default; main() always initializes the real process.
        if configured_mode == "unknown" and not persisted_env.get("ODS_MODE"):
            configured_mode = "local"
        effective_mode = configured_mode
    else:
        effective_mode = _normalize_ods_mode(STARTUP_ODS_MODE)
    return effective_mode, configured_mode


def _model_activation_mode_denial(
    effective_mode: str,
    configured_mode: str,
) -> dict[str, str] | None:
    """Describe why this host process cannot safely perform a model swap."""
    effective_mode = _normalize_ods_mode(effective_mode)
    configured_mode = _normalize_ods_mode(configured_mode)
    if "unknown" in {effective_mode, configured_mode}:
        code = "ods_mode_unknown"
        reason = "mode_unknown"
        message = (
            "Local model activation is unavailable because the effective or "
            "configured ODS mode is unknown."
        )
    elif effective_mode != configured_mode:
        code = "ods_mode_mismatch"
        reason = "mode_mismatch"
        message = (
            f"Local model activation is unavailable because effective mode "
            f"'{effective_mode}' does not match configured mode '{configured_mode}'."
        )
    elif effective_mode not in _LOCAL_MODEL_MODES:
        code = "local_mode_required"
        reason = "effective_mode_not_local"
        message = (
            f"Local model activation is unavailable while effective ODS mode "
            f"is '{effective_mode}'."
        )
    else:
        return None

    return {
        "error": "local_mode_required",
        "code": code,
        "reason": reason,
        "message": message,
        "effectiveMode": effective_mode,
        "configuredMode": configured_mode,
    }


def _resolve_requested_tier_contract(tier: str, env: dict) -> dict[str, str]:
    """Resolve CLI tier metadata through the installed canonical tier map."""
    tier_map = INSTALL_DIR / "installers" / "lib" / "tier-map.sh"
    if not tier_map.is_file():
        raise RuntimeError(f"Installed tier map is unavailable: {tier_map}")
    bash = _find_usable_bash()
    if not bash:
        raise RuntimeError("A usable Bash executable is required to validate tier metadata")
    script = r'''
set -euo pipefail
error() { printf '%s\n' "$*" >&2; return 1; }
source "$1"
TIER="$2"
MODEL_PROFILE="$3"
HOST_ARCH="$4"
resolve_tier_config
printf 'GGUF_FILE=%s\nMAX_CONTEXT=%s\nLLM_MODEL=%s\n' \
    "$GGUF_FILE" "$MAX_CONTEXT" "$LLM_MODEL"
'''
    result = subprocess.run(
        [
            bash,
            "-c",
            script,
            "ods-tier-contract",
            str(tier_map),
            tier,
            str(env.get("MODEL_PROFILE") or "qwen"),
            str(env.get("HOST_ARCH") or platform.machine() or "amd64"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Could not resolve tier {tier}: {detail[:300]}")
    values = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    if not values.get("GGUF_FILE") or not str(values.get("MAX_CONTEXT") or "").isdigit():
        raise RuntimeError(f"Tier {tier} produced an incomplete model contract")
    return values


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
    system_name = system_name or platform.system()
    explicit = env.get("ODS_AGENT_BIND", "").strip()
    if explicit:
        if system_name == "Darwin" and explicit == "::":
            return "0.0.0.0"
        return explicit

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


def _macos_direct_bind_conflicts_with_bridge(
    env: dict,
    bind_addr: str,
    system_name: str | None = None,
) -> bool:
    """Return whether a native macOS bind supersedes the Colima bridge."""
    if (system_name or platform.system()) != "Darwin":
        return False

    bind_addr = str(bind_addr or "").strip()
    gateway_addr = str(env.get("ODS_MACOS_HOST_GATEWAY") or "").strip()
    return (
        bind_addr in {"0.0.0.0", "::"}
        or bool(gateway_addr and bind_addr == gateway_addr)
    )


def _disable_conflicting_macos_bridge(env: dict, bind_addr: str, label: str) -> bool:
    """Best-effort bootout of a bridge that would collide with a direct bind."""
    if not _macos_direct_bind_conflicts_with_bridge(env, bind_addr):
        return False

    service_target = f"gui/{os.getuid()}/{label}"
    try:
        result = subprocess.run(
            ["launchctl", "bootout", service_target],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(
            "Could not disable conflicting macOS bridge %s before binding %s; continuing: %s",
            label,
            bind_addr,
            exc,
        )
        return False

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or "no output"
        logger.warning(
            "Could not disable conflicting macOS bridge %s before binding %s "
            "(launchctl exit %d: %s); continuing",
            label,
            bind_addr,
            result.returncode,
            detail,
        )
        return False

    logger.info("Disabled conflicting macOS bridge %s before binding %s", label, bind_addr)
    return True


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
    # Contract note: every resolver launch below must include --gpu-count and
    # the persisted ODS_MODE. Extension toggles invalidate the cache while the
    # agent process keeps running, so os.environ may not reflect the install.
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
    install_env = load_env(INSTALL_DIR / ".env")
    ods_mode = install_env.get("ODS_MODE", "").strip() or "local"
    cmd = [
        bash, _to_bash_path(script),
        "--script-dir", _to_bash_path(INSTALL_DIR),
        "--tier", TIER,
        "--gpu-backend", GPU_BACKEND,
        "--gpu-count", GPU_COUNT,
        "--ods-mode", ods_mode,
    ]
    if platform.system() == "Windows" and not _windows_whisper_cuda_supported(install_env):
        cmd.extend(["--skip-gpu-overlays", "whisper"])
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
    compose_env = os.environ.copy()
    for key in ("GGUF_FILE", "LLM_MODEL", "LEMONADE_MODEL", "MAX_CONTEXT", "CTX_SIZE"):
        compose_env.pop(key, None)
    try:
        result = subprocess.run(
            cmd, cwd=str(INSTALL_DIR),
            capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_START,
            env=compose_env,
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


def _normalize_gpu_name(value: str) -> str:
    value = re.sub(r"\s*[x\u00d7]\s*\d+$", "", str(value or "").strip(), flags=re.IGNORECASE)
    value = re.sub(r"^(amd|nvidia)\s+", "", value, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _is_windows_amd_integrated_gpu_name(value: str) -> bool:
    """Mirror the installer classifier used to exclude display-only iGPUs."""
    name = str(value or "").strip()
    return any(re.search(pattern, name, re.IGNORECASE) for pattern in (
        r"\bRadeon(?:\(TM\))?\s+Graphics$",
        r"\bRadeon(?:\(TM\))?\s+\d{3,4}[MS](?:\s+Graphics)?$",
        r"\bRadeon(?:\(TM\))?\s+(?:RX\s+)?Vega\s+\d{1,2}\s+Graphics$",
        r"\bStrix\s+Halo\b",
    ))


def _is_windows_amd_discrete_gpu_name(value: str) -> bool:
    name = str(value or "").strip()
    if _is_windows_amd_integrated_gpu_name(name):
        return False
    return bool(re.search(r"\b(?:AMD\s+)?Radeon\b|\bFirePro\b", name, re.IGNORECASE))


def _select_windows_gpu_adapters(adapters: list[dict], configured_name: str = "") -> list[dict]:
    """Select the configured hardware GPU without accidentally choosing an iGPU."""
    hardware = [item for item in adapters if not item.get("software")]
    backend = str(GPU_BACKEND or "").casefold()
    vendor_id = 0x1002 if backend == "amd" else 0x10DE if backend == "nvidia" else None
    vendor_matches = [item for item in hardware if item.get("vendor_id") == vendor_id]
    candidates = vendor_matches or hardware
    if not candidates:
        return []

    wanted = _normalize_gpu_name(configured_name)
    # The Windows env historically persisted only the primary AMD name, not
    # GPU_COUNT. Treat all discrete Radeon adapters as the compute set even if
    # they are different models; the name still helps on integrated-only hosts.
    if backend == "amd":
        discrete = [
            item for item in candidates
            if _is_windows_amd_discrete_gpu_name(item.get("name", ""))
        ]
        if discrete:
            return discrete
        if wanted:
            named = [
                item for item in candidates
                if _normalize_gpu_name(item.get("name", "")) == wanted
            ]
            if named:
                return named
        return candidates

    if wanted:
        named = [item for item in candidates if _normalize_gpu_name(item.get("name", "")) == wanted]
        if named:
            try:
                expected_count = max(1, int(GPU_COUNT or "1"))
            except ValueError:
                expected_count = 1
            return sorted(named, key=lambda item: item.get("memory_total_mb", 0), reverse=True)[:expected_count]

    # The Windows env historically omitted GPU_COUNT/HOST_GPU_NAME. Infer the
    # compute set from hardware rather than silently collapsing dual Radeon
    # systems to one adapter. On hybrid laptops, integrated Radeon Graphics is
    # display hardware and must not become a peer when a discrete Radeon exists.
    try:
        expected_count = max(1, int(GPU_COUNT or "1"))
    except ValueError:
        expected_count = 1
    return sorted(
        candidates, key=lambda item: item.get("memory_total_mb", 0), reverse=True,
    )[:expected_count]


def _windows_dxgi_adapters() -> list[dict]:
    """Enumerate Windows hardware adapters with stable DXGI LUIDs."""
    if platform.system() != "Windows":
        return []
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8),
            ]

        class LUID(ctypes.Structure):
            _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

        class DXGIAdapterDesc1(ctypes.Structure):
            _fields_ = [
                ("Description", wintypes.WCHAR * 128),
                ("VendorId", wintypes.UINT), ("DeviceId", wintypes.UINT),
                ("SubSysId", wintypes.UINT), ("Revision", wintypes.UINT),
                ("DedicatedVideoMemory", ctypes.c_size_t),
                ("DedicatedSystemMemory", ctypes.c_size_t),
                ("SharedSystemMemory", ctypes.c_size_t),
                ("AdapterLuid", LUID), ("Flags", wintypes.UINT),
            ]

        def make_guid(value: str) -> GUID:
            return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)

        def com_method(obj, index, restype, *argtypes):
            vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])

        factory = ctypes.c_void_p()
        iid = make_guid("770AAE78-F26F-4DBA-A829-253C83D1B387")
        create_factory = ctypes.windll.dxgi.CreateDXGIFactory1
        create_factory.argtypes = [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
        if create_factory(ctypes.byref(iid), ctypes.byref(factory)) < 0:
            return []

        adapters: list[dict] = []
        try:
            for index in range(32):
                adapter = ctypes.c_void_p()
                result = com_method(
                    factory, 12, ctypes.c_long, wintypes.UINT,
                    ctypes.POINTER(ctypes.c_void_p),
                )(factory, index, ctypes.byref(adapter))
                if result < 0:
                    break
                try:
                    desc = DXGIAdapterDesc1()
                    if com_method(
                        adapter, 10, ctypes.c_long, ctypes.POINTER(DXGIAdapterDesc1)
                    )(adapter, ctypes.byref(desc)) >= 0:
                        adapters.append({
                            "name": desc.Description.strip(),
                            "vendor_id": int(desc.VendorId),
                            "memory_total_mb": int(desc.DedicatedVideoMemory // (1024 * 1024)),
                            "shared_memory_total_mb": int(desc.SharedSystemMemory // (1024 * 1024)),
                            "luid_high": int(desc.AdapterLuid.HighPart),
                            "luid_low": int(desc.AdapterLuid.LowPart),
                            "software": bool(desc.Flags & 2),
                        })
                finally:
                    com_method(adapter, 2, wintypes.ULONG)(adapter)
        finally:
            com_method(factory, 2, wintypes.ULONG)(factory)
        return adapters
    except (AttributeError, OSError, TypeError, ValueError):
        logger.debug("DXGI GPU enumeration failed", exc_info=True)
        return []


def _windows_gpu_metrics() -> dict | None:
    """Collect real per-adapter Windows GPU utilization and memory use."""
    global _windows_gpu_metrics_cache, _windows_dxgi_adapters_cache
    if platform.system() != "Windows":
        return None
    with _windows_gpu_metrics_lock:
        cached_at, cached = _windows_gpu_metrics_cache
        if time.monotonic() - cached_at < 4.0:
            return cached

        env = load_env(INSTALL_DIR / ".env")
        adapters_cached_at, all_adapters = _windows_dxgi_adapters_cache
        if not all_adapters or time.monotonic() - adapters_cached_at >= 300.0:
            all_adapters = _windows_dxgi_adapters()
            _windows_dxgi_adapters_cache = (time.monotonic(), all_adapters)
        adapters = _select_windows_gpu_adapters(
            all_adapters, env.get("HOST_GPU_NAME", ""),
        )
        if not adapters:
            _windows_gpu_metrics_cache = (time.monotonic(), None)
            return None

        prefixes = [
            f"luid_0x{item['luid_high'] & 0xffffffff:08x}_0x{item['luid_low'] & 0xffffffff:08x}"
            for item in adapters
        ]
        powershell_prefixes = ",".join(f"'{prefix}'" for prefix in prefixes)
        script = rf"""
$ErrorActionPreference = 'Stop'
$prefixes = @({powershell_prefixes})
$metrics = @()
foreach ($prefix in $prefixes) {{
  $engineTotals = @{{}}
  Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine |
    Where-Object {{ $_.Name -like "*$prefix*" }} |
    ForEach-Object {{
      if ($_.Name -match '_eng_([0-9]+)_engtype_(.+)$') {{
        $key = "$($Matches[1])|$($Matches[2])"
        $engineTotals[$key] = [double]($engineTotals[$key] + $_.UtilizationPercentage)
      }}
    }}
  $adapterUtil = 0
  if ($engineTotals.Count -gt 0) {{
    $adapterUtil = [Math]::Min(100, ($engineTotals.Values | Measure-Object -Maximum).Maximum)
  }}
  $memoryRows = @(Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory |
    Where-Object {{ $_.Name -like "$prefix*" }})
  $dedicated = ($memoryRows | Measure-Object -Property DedicatedUsage -Sum).Sum
  $shared = ($memoryRows | Measure-Object -Property SharedUsage -Sum).Sum
  $metrics += [pscustomobject]@{{
    prefix = $prefix
    utilization_percent = [int][Math]::Round($adapterUtil)
    utilization_available = ($engineTotals.Count -gt 0)
    dedicated_used_bytes = [int64]$(if ($null -ne $dedicated) {{ $dedicated }} else {{ 0 }})
    shared_used_bytes = [int64]$(if ($null -ne $shared) {{ $shared }} else {{ 0 }})
    memory_usage_available = ($memoryRows.Count -gt 0)
  }}
}}
[pscustomobject]@{{ adapters = @($metrics) }} |
  ConvertTo-Json -Compress
"""
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=8,
            )
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout).strip())
            counters = json.loads(result.stdout)
            counter_rows = counters.get("adapters")
            if not isinstance(counter_rows, list):
                raise ValueError("GPU counter response did not contain an adapter list")
            rows_by_prefix = {
                str(row.get("prefix") or "").casefold(): row
                for row in counter_rows if isinstance(row, dict)
            }
            try:
                system_ram_gb = max(0, int(float(
                    env.get("SYSTEM_RAM_GB") or env.get("HOST_RAM_GB") or 0
                )))
            except (TypeError, ValueError):
                system_ram_gb = 0

            gpu_rows = []
            for index, (adapter, prefix) in enumerate(zip(adapters, prefixes)):
                row = rows_by_prefix.get(prefix.casefold(), {})
                dedicated_total_mb = max(0, int(adapter.get("memory_total_mb") or 0))
                unified = (
                    _is_windows_amd_integrated_gpu_name(adapter.get("name", ""))
                    and dedicated_total_mb <= 4096
                    and system_ram_gb >= 32
                )
                memory_type = "unified" if unified else "discrete"
                total_mb = (
                    int(system_ram_gb * 0.75 * 1024)
                    if unified else dedicated_total_mb
                )
                dedicated_used = max(0, int(row.get("dedicated_used_bytes") or 0))
                shared_used = max(0, int(row.get("shared_used_bytes") or 0))
                used_bytes = dedicated_used + shared_used if unified else dedicated_used
                used_mb = min(total_mb, used_bytes // (1024 * 1024))
                gpu_rows.append({
                    "index": index,
                    "uuid": f"luid-{adapter['luid_high'] & 0xffffffff:08x}-{adapter['luid_low'] & 0xffffffff:08x}",
                    "name": str(adapter["name"]),
                    "memory_type": memory_type,
                    "memory_total_mb": total_mb,
                    "memory_used_mb": used_mb,
                    "memory_usage_available": bool(row.get("memory_usage_available", False)),
                    "utilization_percent": max(0, min(100, int(row.get("utilization_percent") or 0))),
                    "utilization_available": bool(row.get("utilization_available", False)),
                    "temperature_c": None,
                    "temperature_available": False,
                })

            total_mb = sum(item["memory_total_mb"] for item in gpu_rows)
            used_mb = sum(item["memory_used_mb"] for item in gpu_rows)
            available_util = [
                item["utilization_percent"] for item in gpu_rows
                if item["utilization_available"]
            ]
            names = [item["name"] for item in gpu_rows]
            display_name = f"{names[0]} \u00d7 {len(names)}" if len(set(names)) == 1 and len(names) > 1 else " + ".join(names)
            payload = {
                "schema_version": "ods.host-gpu-metrics.v1",
                "name": display_name,
                "gpu_count": len(gpu_rows),
                "memory_type": "unified" if all(item["memory_type"] == "unified" for item in gpu_rows) else "discrete",
                "memory_total_mb": total_mb,
                "memory_used_mb": used_mb,
                "memory_usage_available": all(item["memory_usage_available"] for item in gpu_rows),
                "utilization_percent": round(sum(available_util) / len(available_util)) if available_util else 0,
                "utilization_available": bool(available_util),
                "temperature_c": None,
                "temperature_available": False,
                "source": "windows-dxgi-performance-counters",
                "sampled_at": _iso_now(),
                "gpus": gpu_rows,
            }
        except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired,
                ValueError, TypeError, RuntimeError):
            logger.debug("Windows GPU performance counters unavailable", exc_info=True)
            payload = None
        _windows_gpu_metrics_cache = (time.monotonic(), payload)
        return payload


def _windows_llm_status() -> dict | None:
    """Read host-native Lemonade health and optional stats over loopback."""
    global _windows_llm_status_cache
    if platform.system() != "Windows":
        return None
    with _windows_llm_status_lock:
        cached_at, cached = _windows_llm_status_cache
        if time.monotonic() - cached_at < 1.0:
            return cached

        env = load_env(INSTALL_DIR / ".env")
        try:
            port = int(env.get("AMD_INFERENCE_PORT") or "8080")
        except ValueError:
            port = 8080
        if port < 1 or port > 65535:
            port = 8080
        base = f"http://127.0.0.1:{port}"
        api_key = str(env.get("LEMONADE_API_KEY") or "").strip()

        def fetch_json(name: str) -> dict:
            last_error: Exception | None = None
            # ODS currently ships the /api/v1 compatibility route; newer
            # Lemonade releases document /v1. Accept both during upgrades.
            for prefix in ("/api/v1", "/v1"):
                path = f"{prefix}/{name}"
                try:
                    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
                    request = urllib_request.Request(f"{base}{path}", headers=headers)
                    with urllib_request.urlopen(request, timeout=4) as response:
                        raw = response.read(MAX_TELEMETRY_RESPONSE_BYTES + 1)
                    if len(raw) > MAX_TELEMETRY_RESPONSE_BYTES:
                        raise ValueError(f"{path} exceeded the telemetry response limit")
                    payload = json.loads(raw.decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError(f"{path} returned non-object JSON")
                    return payload
                except (OSError, ValueError, json.JSONDecodeError, urllib_error.URLError) as exc:
                    last_error = exc
            raise OSError(f"Lemonade {name} endpoint is unavailable: {last_error}")

        try:
            raw_health = fetch_json("health")
        except (OSError, ValueError, json.JSONDecodeError, urllib_error.URLError):
            logger.debug("Windows host inference health unavailable", exc_info=True)
            _windows_llm_status_cache = (time.monotonic(), None)
            return None

        stats = None
        try:
            raw_stats = fetch_json("stats")
            stats = {
                key: raw_stats.get(key)
                for key in (
                    "time_to_first_token", "tokens_per_second", "input_tokens",
                    "output_tokens", "prompt_tokens",
                )
            }
        except (OSError, ValueError, json.JSONDecodeError, urllib_error.URLError):
            logger.debug("Windows host inference stats unavailable", exc_info=True)
        model_loaded = raw_health.get("model_loaded")
        if isinstance(model_loaded, str):
            # Runtime releases may expose an absolute checkpoint path here.
            # The dashboard needs an identity, never the host directory layout.
            model_loaded = re.split(r"[\\/]", model_loaded.strip())[-1] or None
        elif model_loaded is not None:
            model_loaded = None
        health = {
            "status": raw_health.get("status"),
            "version": raw_health.get("version"),
            "model_loaded": model_loaded,
        }
        payload = {
            "schema_version": "ods.host-llm-status.v1",
            "health": health,
            "stats": stats,
            "source": "windows-loopback",
            "sampled_at": _iso_now(),
        }
        _windows_llm_status_cache = (time.monotonic(), payload)
        return payload


def _docker_service_health_snapshot() -> dict:
    """Return a cached, read-only Docker lifecycle and healthcheck snapshot."""
    global _service_health_cache
    with _service_health_lock:
        cached_at, cached = _service_health_cache
        if cached is not None and time.monotonic() - cached_at < 3.0:
            return cached

        names_result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=8,
        )
        if names_result.returncode != 0:
            raise RuntimeError((names_result.stderr or names_result.stdout).strip())
        names = [
            name.strip() for name in names_result.stdout.splitlines()
            if name.strip().startswith("ods-")
        ]
        containers: list[dict] = []
        if names:
            inspect_result = subprocess.run(
                ["docker", "inspect", *names], capture_output=True, text=True, timeout=12,
            )
            if inspect_result.returncode != 0:
                raise RuntimeError((inspect_result.stderr or inspect_result.stdout).strip())
            inspected = json.loads(inspect_result.stdout)
            if not isinstance(inspected, list):
                raise ValueError("docker inspect returned non-list JSON")
            for item in inspected:
                if not isinstance(item, dict):
                    continue
                state = item.get("State") if isinstance(item.get("State"), dict) else {}
                health = state.get("Health") if isinstance(state.get("Health"), dict) else {}
                labels = (item.get("Config") or {}).get("Labels") or {}
                service_id = labels.get("com.docker.compose.service")
                container_name = str(item.get("Name") or "").lstrip("/")
                if not service_id and container_name.startswith("ods-"):
                    service_id = container_name.removeprefix("ods-")
                containers.append({
                    "service_id": str(service_id or ""),
                    "container_name": container_name,
                    "state": str(state.get("Status") or "unknown"),
                    "health": str(health.get("Status") or "none"),
                })
        payload = {
            "schema_version": "ods.host-service-health.v1",
            "containers": containers,
            "sampled_at": _iso_now(),
        }
        _service_health_cache = (time.monotonic(), payload)
        return payload


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
    last_error: PermissionError | None = None
    for attempt in range(6):
        try:
            os.replace(str(tmp_file), str(progress_file))
            return
        except PermissionError as exc:
            last_error = exc
            if attempt == 5:
                break
            # Windows can briefly hold the bind-mounted progress file open
            # while dashboard-api polls it; retry without changing install state.
            time.sleep(0.05 * (attempt + 1))
    if last_error is not None:
        raise last_error


def _model_file_ready(path: Path) -> bool:
    """Return True only for a final GGUF file that exists and is non-empty."""
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _local_model_name_from_gguf(gguf_file: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(gguf_file).stem).strip("-._")
    return name or "local-gguf"


def _valid_local_model_name(value: object) -> bool:
    """Return true for identities safe in .env and models.ini sections."""
    return bool(
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value)
    )


def _valid_gguf_filename(value: object) -> bool:
    """Return true for a single safe GGUF filename."""
    return bool(
        isinstance(value, str)
        and value
        and value.casefold().endswith(".gguf")
        and Path(value).name == value
        and not any(character in value for character in "\r\n\x00")
    )


def _local_gguf_filename_from_id(model_id: str) -> str | None:
    """Map a Dashboard/local model id to a safe GGUF filename candidate."""
    token = str(model_id or "").strip()
    if token.lower().startswith("extra."):
        token = token[6:]
    if not token or any(sep in token for sep in ("/", "\\", "\r", "\n", "\x00")):
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
    logical_matches: list[Path] = []
    candidate_logical = _local_model_name_from_gguf(candidate).lower()
    try:
        for path in models_dir.iterdir():
            if not path.is_file() or not path.name.lower().endswith(".gguf"):
                continue
            if path.name.lower() == candidate_lower:
                exact_matches.append(path)
            elif path.stem.lower() == candidate_stem:
                stem_matches.append(path)
            elif _local_model_name_from_gguf(path.name).lower() == candidate_logical:
                logical_matches.append(path)
    except OSError:
        return None

    matches = exact_matches or stem_matches or logical_matches
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
    if getattr(handler, "close_connection", False):
        handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(payload)
    handler.wfile.flush()


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
    # Compared as UTF-8 bytes: compare_digest raises TypeError on non-ASCII
    # str, which would turn an unauthenticated request into a 500 not a 403.
    if not secrets.compare_digest(auth[7:].encode("utf-8"), AGENT_API_KEY.encode("utf-8")):
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
        data = json.loads(handler.rfile.read(min(length, MAX_BODY)))
    except (json.JSONDecodeError, UnicodeDecodeError):
        json_response(handler, 400, {"error": "Invalid JSON"})
        return None
    if not isinstance(data, dict):
        json_response(handler, 400, {"error": "JSON body must be an object"})
        return None
    return data


def discard_request_body(handler) -> None:
    try:
        length = int(handler.headers.get("Content-Length", 0))
    except (ValueError, TypeError):
        return
    remaining = max(0, length)
    while remaining:
        chunk = handler.rfile.read(min(remaining, MAX_BODY))
        if not chunk:
            break
        remaining -= len(chunk)


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
    # Dashboard API keeps a small connection pool to avoid exhausting macOS
    # ephemeral ports when requests traverse the private Colima TCP bridge.
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        logger.info(fmt, *args)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            json_response(self, 200, {"status": "ok", "version": VERSION})
        elif path == "/v1/gpu/metrics":
            self._handle_gpu_metrics()
        elif path == "/v1/llm/status":
            self._handle_llm_status()
        elif path == "/v1/service/health":
            self._handle_service_health()
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

    def _handle_service_health(self):
        """Return Docker's lifecycle and container-health snapshot."""
        if not check_auth(self):
            return
        try:
            json_response(self, 200, _docker_service_health_snapshot())
        except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired,
                ValueError, TypeError, RuntimeError) as exc:
            json_response(self, 503, {"error": f"Docker health snapshot failed: {exc}"})

    def _handle_llm_status(self):
        """Bridge Windows host-native inference without Docker DNS/NAT."""
        if not check_auth(self):
            return
        status = _windows_llm_status()
        if status is None:
            json_response(self, 503, {"error": "Host inference telemetry is unavailable"})
            return
        json_response(self, 200, status)

    def _handle_gpu_metrics(self):
        """Return host GPU counters that Docker Desktop cannot expose."""
        if not check_auth(self):
            return
        metrics = _windows_gpu_metrics()
        if metrics is None:
            json_response(self, 503, {"error": "Host GPU telemetry is unavailable"})
            return
        json_response(self, 200, metrics)

    def do_POST(self):
        # Several legacy endpoints intentionally ignore an optional body, and
        # rejected requests may return before consuming one. Close POST
        # connections after their framed response so unread bytes can never be
        # parsed as the next request on an HTTP/1.1 keep-alive connection. GET
        # polling remains reusable, which is where connection churn matters.
        self.close_connection = True
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
        elif self.path == "/v1/runtime/lemonade/ensure":
            self._handle_windows_lemonade_runtime_ensure()
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
            elif not BACKUP_ID_RE.match(backup_id.strip()):
                json_response(self, 400, {"error": "Invalid backup_id"})
                return
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

        acquired, active = _begin_model_lifecycle("system_update")
        if not acquired:
            json_response(
                self,
                409,
                {
                    "success": False,
                    **_model_lifecycle_conflict("system update", active),
                },
            )
            return

        with _update_lock:
            if _update_thread is not None and _update_thread.is_alive():
                _end_model_lifecycle("system_update")
                json_response(self, 409, {
                    "success": False,
                    "status": "running",
                    "message": "Update already running",
                })
                return

            try:
                _write_update_status("queued", "update", started_at=_iso_now())
            except Exception:
                _end_model_lifecycle("system_update")
                raise

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
                finally:
                    _end_model_lifecycle("system_update")

            try:
                _update_thread = threading.Thread(target=_run_background_update, daemon=True)
                _update_thread.start()
            except Exception:
                _end_model_lifecycle("system_update")
                raise

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
            # .env holds service secrets (DASHBOARD_API_KEY, ODS_AGENT_KEY,
            # JWT_SECRET, OAuth secrets). The fresh tmp file is created at the
            # umask default (0644); tighten before os.replace() swaps it in so
            # the live .env keeps mode 0600 instead of inheriting 0644.
            os.chmod(tmp_path, 0o600)
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
        preserve_existing = body.get("preserve_existing", False)
        if not isinstance(preserve_existing, bool):
            json_response(self, 400, {"error": "preserve_existing must be a boolean"})
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

        target_candidate = install_config / sid
        if target_candidate.is_symlink():
            json_response(self, 400, {
                "error": f"config sync refused: target is a symlink for {sid}",
            })
            return
        target = target_candidate.resolve()
        # Path-traversal guard: target must stay under install_config. Always true
        # because sid is validated against SERVICE_ID_RE above (no slashes / dots),
        # but kept as defense-in-depth in case the regex ever loosens.
        if not target.is_relative_to(install_config):
            json_response(self, 400, {
                "error": f"config sync refused: target outside install dir for {sid}",
            })
            return

        if target.is_dir():
            for root, dirs, files in os.walk(str(target), followlinks=False):
                for name in dirs + files:
                    if (Path(root) / name).is_symlink():
                        json_response(self, 400, {
                            "error": (
                                f"config sync refused: existing target symlink {name} "
                                f"for {sid}"
                            ),
                        })
                        return

        synced: list[str] = []
        lock = _service_locks[sid]
        if not lock.acquire(blocking=False):
            json_response(self, 409, {"error": f"Operation already in progress for {sid}"})
            return
        try:
            try:
                if preserve_existing:
                    for source_path in sorted(src_svc.rglob("*")):
                        relative = source_path.relative_to(src_svc)
                        target_path = target / relative
                        if source_path.is_dir():
                            target_path.mkdir(parents=True, exist_ok=True)
                        elif source_path.is_file() and not target_path.exists():
                            target_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(source_path, target_path)
                else:
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
            # Echo the honored mode so callers can detect an agent that
            # predates preserve_existing instead of silently full-copying
            # over user config during update/rollback.
            "preserve_existing": bool(preserve_existing),
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
            env_path = INSTALL_DIR / ".env"

            try:
                library = _load_model_library_records()
            except RuntimeError as exc:
                logger.exception("Model library catalog unavailable")
                json_response(self, 500, {"error": str(exc)})
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
            data = {"status": "idle"}
            data.update(_model_lifecycle_status())
            _verify_switchboard_route_for_status(data, "model-status")
            json_response(self, 200, data)
            return
        try:
            data = _read_model_status(status_path)
            data = _normalize_model_download_status(status_path, data)
            data.update(_model_lifecycle_status())
            _verify_switchboard_route_for_status(data, "model-status")
            json_response(self, 200, data)
        except (json.JSONDecodeError, OSError):
            data = {"status": "idle"}
            data.update(_model_lifecycle_status())
            _verify_switchboard_route_for_status(data, "model-status")
            json_response(self, 200, data)

    def _handle_model_download(self):
        """Start async model download. Only one download at a time.

        Supports both single-file and split-file (gguf_parts) models.
        For split models, the caller sends gguf_parts as an array of
        {"file": ..., "url": ...} dicts.  The first part's filename is
        used as gguf_file for status tracking.
        """
        global _model_download_cancelable, _model_download_thread
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

        # Validate the complete request against the library. A split request
        # must include every catalog part; accepting a subset can otherwise
        # create a false-complete model that llama.cpp cannot load.
        allowed = False
        manifest = None
        try:
            library = _load_model_library_records()
        except RuntimeError as exc:
            logger.exception("Model library catalog unavailable")
            json_response(self, 500, {"error": str(exc)})
            return
        for m in library:
            if m.get("gguf_file") != gguf_file:
                continue
            candidate_manifest = _model_download_manifest(m)
            if candidate_manifest is None:
                break
            if gguf_parts:
                catalog_plan = [
                    (artifact["file"], artifact["url"])
                    for artifact in candidate_manifest["artifacts"]
                ]
                if download_plan == catalog_plan:
                    allowed = True
                    manifest = candidate_manifest
            elif (
                len(candidate_manifest["artifacts"]) == 1
                and candidate_manifest["artifacts"][0]["url"] == gguf_url
            ):
                allowed = True
                manifest = candidate_manifest
            break
        if not allowed:
            json_response(self, 403, {"error": "Model not in library catalog"})
            return
        if manifest is None:
            json_response(self, 500, {"error": "Model catalog manifest is invalid"})
            return

        models_dir = INSTALL_DIR / "data" / "models"
        status_path = INSTALL_DIR / "data" / "model-download-status.json"
        artifact_by_file = {
            artifact["file"]: artifact
            for artifact in manifest["artifacts"]
        }
        artifact_paths = {}
        for artifact in manifest["artifacts"]:
            target = _safe_model_artifact_path(models_dir, artifact["file"])
            if target is None:
                json_response(self, 500, {"error": "Model catalog contains an unsafe filename"})
                return
            artifact_paths[artifact["file"]] = target

        lifecycle_acquired, active = _begin_model_lifecycle("model_download", gguf_file)
        if not lifecycle_acquired:
            json_response(
                self,
                409,
                _model_lifecycle_conflict("model download", active),
            )
            return

        # Existing files are reusable only after exact catalog verification.
        # This intentionally hashes them before returning already_downloaded;
        # non-empty alone is not evidence that a prior transfer completed.
        valid_preexisting_files = set()
        invalid_existing_files = {}
        try:
            for filename, target in artifact_paths.items():
                valid, reason = _verify_model_artifact(target, artifact_by_file[filename])
                if valid:
                    valid_preexisting_files.add(filename)
                elif target.exists():
                    invalid_existing_files[filename] = reason
        except Exception:
            _end_model_lifecycle("model_download")
            raise

        if len(valid_preexisting_files) == len(download_plan):
            # A previous process can leave stale "downloading" status after the
            # final file is already on disk. Normalize that here so the
            # dashboard stops showing phantom progress.
            _write_model_status(status_path, "complete", gguf_file, 0, 0)
            _end_model_lifecycle("model_download")
            json_response(self, 200, {"status": "already_downloaded"})
            return

        for filename, reason in invalid_existing_files.items():
            logger.warning("Discarding invalid existing model artifact %s: %s", filename, reason)
            try:
                artifact_paths[filename].unlink(missing_ok=True)
            except OSError as exc:
                _end_model_lifecycle("model_download")
                json_response(
                    self,
                    500,
                    {"error": f"Invalid model artifact could not be replaced: {filename}: {exc}"},
                )
                return
        pending_download_plan = [
            (idx, fn, url)
            for idx, (fn, url) in enumerate(download_plan, 1)
            if fn not in valid_preexisting_files
        ]

        # Check for concurrent download
        with _model_download_lock:
            if _model_download_thread is not None and _model_download_thread.is_alive():
                _end_model_lifecycle("model_download")
                json_response(self, 409, {"error": "Another download is in progress"})
                return

            _model_download_cancel.clear()
            _model_download_cancelable = True

            def _download():
                global _model_download_cancelable, _model_download_proc
                created_final_paths: set[Path] = set()
                temp_paths: set[Path] = set()
                cancel_cleanup_done = False

                def _discard_cancelled_path(path: Path) -> str | None:
                    if not path.exists():
                        return None
                    try:
                        path.unlink()
                        return None
                    except OSError as unlink_error:
                        quarantine = path.with_name(
                            f".{path.name}.cancelled-{threading.get_ident()}-{time.time_ns()}"
                        )
                        try:
                            os.replace(str(path), str(quarantine))
                            logger.warning(
                                "Quarantined cancelled model artifact %s as %s after unlink failed: %s",
                                path.name,
                                quarantine.name,
                                unlink_error,
                            )
                            return None
                        except OSError as quarantine_error:
                            return (
                                f"{path.name}: unlink failed ({unlink_error}); "
                                f"quarantine failed ({quarantine_error})"
                            )

                def _finish_cancelled_download() -> None:
                    nonlocal cancel_cleanup_done
                    if cancel_cleanup_done:
                        return
                    cancel_cleanup_done = True
                    cleanup_errors = []
                    for path in sorted(temp_paths | created_final_paths, key=str):
                        error = _discard_cancelled_path(path)
                        if error:
                            cleanup_errors.append(error)
                    message = "Download cancelled by user"
                    if cleanup_errors:
                        message += "; cleanup incomplete: " + "; ".join(cleanup_errors)
                    _write_model_status(
                        status_path,
                        "cancelled" if not cleanup_errors else "failed",
                        gguf_file,
                        0,
                        0,
                        message,
                    )
                    logger.info("Model download cancelled: %s", gguf_file)

                try:
                    models_dir.mkdir(parents=True, exist_ok=True)
                    label = gguf_file if len(download_plan) == 1 else f"{gguf_file} ({len(download_plan)} parts)"
                    _write_model_status(status_path, "downloading", label, 0, 0)

                    for part_idx, part_file_name, part_url in pending_download_plan:
                        if _model_download_cancel.is_set():
                            _finish_cancelled_download()
                            return
                        part_target = artifact_paths[part_file_name]
                        part_tmp = _safe_model_artifact_path(
                            models_dir,
                            f"{part_file_name}.part",
                        )
                        if part_tmp is None:
                            raise RuntimeError(f"Unsafe temporary model filename: {part_file_name}.part")
                        temp_paths.add(part_tmp)
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
                        try:
                            for attempt in range(1, 4):
                                if _model_download_cancel.is_set():
                                    break
                                if attempt > 1:
                                    logger.info("Model download retry %d/3 for %s", attempt, part_file_name)
                                    # Use wait() instead of sleep() so cancel is honored immediately
                                    _model_download_cancel.wait(5)
                                    if _model_download_cancel.is_set():
                                        break
                                proc = subprocess.Popen(
                                    ["curl", "-fSL", "-sS", "-C", "-", "--connect-timeout", "30",
                                     "-o", str(part_tmp), part_url],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE,
                                    text=True,
                                )
                                _model_download_proc = proc
                                stderr_text = ""
                                try:
                                    _, stderr_text = proc.communicate(timeout=14400)
                                except subprocess.TimeoutExpired:
                                    proc.kill()
                                    try:
                                        _, stderr_text = proc.communicate(timeout=5)
                                    except subprocess.TimeoutExpired:
                                        proc.kill()
                                        proc.wait(timeout=5)
                                finally:
                                    _model_download_proc = None

                                if _model_download_cancel.is_set():
                                    break
                                downloaded = proc.returncode == 0
                                if not downloaded:
                                    curl_error = _format_curl_download_error(proc.returncode, stderr_text)
                                    if _parse_huggingface_resolve_url(part_url) is not None:
                                        _write_model_status(
                                            status_path,
                                            "downloading",
                                            part_label,
                                            0,
                                            part_total,
                                            f"Retry {attempt}/3: {curl_error}; trying Hugging Face Hub fallback",
                                        )
                                        hub_ok, hub_error = _download_huggingface_artifact(
                                            part_url,
                                            part_tmp,
                                            _model_download_cancel,
                                            status_path=status_path,
                                            status_label=part_label,
                                            part_total=part_total,
                                            status_error=f"Retry {attempt}/3: {curl_error}",
                                        )
                                        downloaded = hub_ok
                                        if not hub_ok:
                                            last_error = f"{curl_error}; {hub_error}"
                                    else:
                                        last_error = curl_error

                                if _model_download_cancel.is_set():
                                    break
                                if downloaded:
                                    try:
                                        part_tmp.replace(part_target)
                                        created_final_paths.add(part_target)
                                    except OSError as exc:
                                        last_error = f"Download finished but final file could not be moved into place: {exc}"
                                    else:
                                        if _model_file_ready(part_target):
                                            success = True
                                            break
                                        last_error = "Download finished but model file is missing or empty"
                                        part_target.unlink(missing_ok=True)
                                        created_final_paths.discard(part_target)
                                _write_model_status(
                                    status_path,
                                    "downloading",
                                    part_label,
                                    0,
                                    part_total,
                                    f"Retry {attempt}/3: {last_error}",
                                )
                        finally:
                            _stop_progress.set()
                            progress_thread.join(timeout=3)

                        if _model_download_cancel.is_set():
                            _finish_cancelled_download()
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

                    if _model_download_cancel.is_set():
                        _finish_cancelled_download()
                        return
                    for part_idx, artifact in enumerate(manifest["artifacts"], 1):
                        part_file_name = artifact["file"]
                        final_target = artifact_paths[part_file_name]
                        try:
                            final_size = final_target.stat().st_size
                        except OSError:
                            final_size = 0
                        verify_label = (
                            part_file_name
                            if len(download_plan) == 1
                            else f"{part_file_name} (part {part_idx}/{len(download_plan)})"
                        )
                        _write_model_status(status_path, "verifying", verify_label, final_size, final_size)
                        valid, reason = _verify_model_artifact(
                            final_target,
                            artifact,
                            _model_download_cancel,
                        )
                        if _model_download_cancel.is_set():
                            _finish_cancelled_download()
                            return
                        if not valid:
                            if final_target in created_final_paths:
                                final_target.unlink(missing_ok=True)
                                created_final_paths.discard(final_target)
                            _write_model_status(
                                status_path,
                                "failed",
                                part_file_name,
                                0,
                                0,
                                reason,
                            )
                            return

                    with _model_download_lock:
                        cancelled_before_commit = _model_download_cancel.is_set()
                        if not cancelled_before_commit:
                            _model_download_cancelable = False
                            _write_model_status(status_path, "complete", gguf_file, 0, 0)
                    if cancelled_before_commit:
                        _finish_cancelled_download()
                        return
                    logger.info("Model download complete: %s (%d parts)", gguf_file, len(download_plan))
                except Exception as exc:
                    if _model_download_cancel.is_set():
                        _finish_cancelled_download()
                    else:
                        for path in temp_paths:
                            try:
                                path.unlink(missing_ok=True)
                            except OSError:
                                logger.warning("Could not remove failed model temporary file %s", path)
                        logger.error("Model download failed: %s", exc)
                        _write_model_status(status_path, "failed", gguf_file, 0, 0, str(exc))
                finally:
                    _model_download_proc = None
                    with _model_download_lock:
                        late_cancel = (
                            _model_download_cancelable
                            and _model_download_cancel.is_set()
                            and not cancel_cleanup_done
                        )
                        _model_download_cancelable = False
                    if late_cancel:
                        _finish_cancelled_download()
                    _end_model_lifecycle("model_download")

            try:
                _model_download_thread = threading.Thread(target=_download, daemon=True)
                _model_download_thread.start()
            except Exception:
                _model_download_cancelable = False
                _end_model_lifecycle("model_download")
                raise

        json_response(self, 200, {"status": "started"})

    def _handle_model_download_cancel(self):
        """Cancel an in-progress model download."""
        if not check_auth(self):
            return
        # Consume an optional framed body before the POST connection closes.
        # Leaving request bytes unread can make Windows send a TCP reset and
        # discard the otherwise valid response before the client receives it.
        if read_optional_json_body(self) is None:
            return
        with _model_download_lock:
            if (
                _model_download_thread is None
                or not _model_download_thread.is_alive()
                or not _model_download_cancelable
            ):
                json_response(self, 200, {"status": "no_download"})
                return
            _model_download_cancel.set()
            # Capture under the same state lock; the worker may clear the
            # global process reference as soon as curl exits.
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
        if not isinstance(model_id, str) or not model_id.strip():
            json_response(self, 400, {"error": "model_id is required"})
            return
        model_id = model_id.strip()
        if any(character in model_id for character in "\r\n\x00"):
            json_response(self, 400, {"error": "model_id contains invalid characters"})
            return

        requested_context_length = body.get("context_length")
        if requested_context_length is not None:
            if (
                isinstance(requested_context_length, bool)
                or not isinstance(requested_context_length, int)
                or not _MIN_MODEL_CONTEXT <= requested_context_length <= _MAX_MODEL_CONTEXT
            ):
                json_response(
                    self,
                    400,
                    {
                        "error": (
                            f"context_length must be an integer between "
                            f"{_MIN_MODEL_CONTEXT} and {_MAX_MODEL_CONTEXT}"
                        )
                    },
                )
                return

        requested_tier = body.get("tier")
        if requested_tier is not None:
            if not isinstance(requested_tier, str):
                json_response(self, 400, {"error": "tier must be a string"})
                return
            requested_tier = requested_tier.strip().upper()
            if (
                not _MODEL_TIER_RE.fullmatch(requested_tier)
                or requested_tier not in _MODEL_TIERS
            ):
                json_response(self, 400, {"error": "tier is not supported"})
                return

        acquired, active_model_id = _begin_model_activation(model_id)
        if not acquired:
            with _model_lifecycle_state_lock:
                active_operation = _model_lifecycle_operation
            json_response(
                self,
                409,
                {
                    "error": (
                        "Another model activation is in progress"
                        if active_operation == "model_activation"
                        else f"Cannot activate a model while {active_operation or 'another operation'} is in progress"
                    ),
                    "code": "model_lifecycle_busy",
                    "activeOperation": active_operation,
                    "activeModelId": active_model_id,
                },
            )
            return

        try:
            activation_options = {}
            if requested_context_length is not None:
                activation_options["requested_context_length"] = requested_context_length
            if requested_tier is not None:
                activation_options["requested_tier"] = requested_tier
            self._do_model_activate(model_id, **activation_options)
        finally:
            _end_model_activation()

    def _handle_windows_lemonade_runtime_ensure(self):
        """Ensure the configured Windows Lemonade runtime is alive and loaded."""
        if not check_auth(self):
            return
        body = read_json_body(self)
        if body is None:
            return

        model_id = body.get("model_id", "")
        gguf_file = body.get("gguf_file", "")
        if model_id is not None and not isinstance(model_id, str):
            json_response(self, 400, {"error": "model_id must be a string"})
            return
        if gguf_file is not None and not isinstance(gguf_file, str):
            json_response(self, 400, {"error": "gguf_file must be a string"})
            return

        active_id = str(model_id or gguf_file or "configured-lemonade")
        acquired, active_model_id = _begin_model_activation(active_id)
        if not acquired:
            with _model_lifecycle_state_lock:
                active_operation = _model_lifecycle_operation
            json_response(
                self,
                409,
                {
                    "error": (
                        "Another model activation is in progress"
                        if active_operation == "model_activation"
                        else f"Cannot ensure Lemonade runtime while {active_operation or 'another operation'} is in progress"
                    ),
                    "code": "model_lifecycle_busy",
                    "activeOperation": active_operation,
                    "activeModelId": active_model_id,
                },
            )
            return

        try:
            self._do_windows_lemonade_runtime_ensure(
                str(model_id or ""),
                str(gguf_file or ""),
            )
        finally:
            _end_model_activation()

    def _do_windows_lemonade_runtime_ensure(self, model_id: str = "", gguf_file: str = ""):
        env_path = INSTALL_DIR / ".env"
        try:
            env = load_env(env_path)
        except (OSError, UnicodeError) as exc:
            logger.exception("Windows Lemonade runtime ensure could not read .env")
            json_response(self, 500, {"error": f"Windows Lemonade runtime ensure failed: {exc}"})
            return

        if not _is_windows_host_lemonade(env):
            json_response(
                self,
                409,
                {
                    "error": "Windows Lemonade runtime ensure is only available for host-managed Windows Lemonade installs",
                    "code": "unsupported_runtime",
                },
            )
            return
        if not _windows_lemonade_is_managed(env):
            json_response(
                self,
                409,
                {
                    "error": "Refusing to manage externally configured Windows Lemonade",
                    "code": "external_runtime",
                },
            )
            return

        target_gguf = (gguf_file or str(env.get("GGUF_FILE") or "")).strip()
        if not target_gguf:
            json_response(self, 400, {"error": "gguf_file is required"})
            return
        target = _safe_model_artifact_path(INSTALL_DIR / "data" / "models", target_gguf)
        if target is None:
            json_response(self, 400, {"error": "Invalid model file path"})
            return
        if not _model_file_ready(target):
            json_response(self, 400, {"error": f"Model file not downloaded or empty: {target_gguf}"})
            return

        llm_model_name = str(model_id or _local_model_name_from_gguf(target_gguf)).strip()
        if not llm_model_name:
            json_response(self, 400, {"error": "model_id could not be resolved"})
            return
        env["GGUF_FILE"] = target_gguf
        env["LLM_MODEL"] = llm_model_name
        _upsert_env_value(env_path, "GGUF_FILE", target_gguf)
        _upsert_env_value(env_path, "LLM_MODEL", llm_model_name)

        if _live_runtime_has_model(env, target_gguf) is not True:
            _restart_windows_lemonade(env)

        lemonade_host, lemonade_port = _lemonade_runtime_address(env)
        lemonade_model_id = _resolve_lemonade_model_id(
            env,
            target_gguf,
            host=lemonade_host,
            port=lemonade_port,
        )
        if not lemonade_model_id:
            json_response(
                self,
                500,
                {"error": f"Could not resolve Lemonade model ID for {target_gguf}"},
            )
            return

        env["LEMONADE_MODEL"] = lemonade_model_id
        _upsert_env_value(env_path, "LEMONADE_MODEL", lemonade_model_id)
        _write_lemonade_config(INSTALL_DIR, target_gguf, lemonade_model_id)
        json_response(
            self,
            200,
            {
                "status": "configured",
                "model_id": model_id or llm_model_name,
                "gguf_file": target_gguf,
                "lemonade_model_id": lemonade_model_id,
            },
        )

    def _do_model_activate(
        self,
        model_id: str,
        *,
        requested_context_length: int | None = None,
        requested_tier: str | None = None,
    ):
        """Inner activate logic — called with _model_activate_lock held."""
        env_path = INSTALL_DIR / ".env"
        if not env_path.exists():
            json_response(
                self,
                500,
                {"error": f"Model activation requires the persisted environment: {env_path}"},
            )
            return
        try:
            persisted_env = load_env(env_path)
        except (OSError, UnicodeError) as exc:
            logger.exception("Model activation could not read persisted mode")
            json_response(self, 500, {"error": f"Model activation failed: {exc}"})
            return
        effective_mode, configured_mode = _model_activation_modes(persisted_env)
        mode_denial = _model_activation_mode_denial(effective_mode, configured_mode)
        if mode_denial is not None:
            json_response(
                self,
                409,
                {
                    **mode_denial,
                    "mode": configured_mode,
                    "requestedModelId": model_id,
                    "activeModelId": (
                        persisted_env.get("LLM_MODEL")
                        or persisted_env.get("GGUF_FILE")
                        or None
                    ),
                },
            )
            return

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
        model = None
        model_from_catalog = False
        try:
            library = _load_model_library_records()
        except RuntimeError as exc:
            json_response(
                self,
                500,
                {"error": f"Model library is unavailable or malformed: {exc}"},
            )
            return
        for entry in library:
            if entry.get("id") == model_id:
                model = entry
                model_from_catalog = True
                break
        if model is None:
            model = local_gguf_model_from_id(model_id)
            if model is None:
                json_response(self, 404, {"error": f"Model '{model_id}' not found in library or local GGUF files"})
                return

        gguf_file = model.get("gguf_file", "")
        llm_model_name = model.get("llm_model_name", model_id)
        if not _valid_gguf_filename(gguf_file):
            json_response(self, 400, {"error": "Model has an invalid GGUF filename"})
            return
        if not _valid_local_model_name(llm_model_name):
            json_response(self, 400, {"error": "Model has an invalid local runtime identity"})
            return
        try:
            catalog_context_length = int(model.get("context_length") or 32768)
        except (TypeError, ValueError):
            catalog_context_length = 32768
        context_length = catalog_context_length
        if requested_context_length is not None:
            if model_from_catalog and requested_context_length > catalog_context_length:
                json_response(
                    self,
                    400,
                    {
                        "error": (
                            f"Requested context {requested_context_length} exceeds the "
                            f"catalog limit {catalog_context_length} for {model_id}"
                        )
                    },
                )
                return
            context_length = requested_context_length
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
        if model_from_catalog:
            activation_manifest = _model_download_manifest(model)
            if activation_manifest is None:
                json_response(
                    self,
                    500,
                    {"error": f"Model catalog integrity manifest is invalid: {model_id}"},
                )
                return
            manifest_valid, integrity_error = _verify_model_manifest(
                models_dir,
                activation_manifest,
            )
            if not manifest_valid:
                json_response(
                    self,
                    400,
                    {
                        "error": (
                            f"Model artifacts failed catalog verification: {integrity_error}"
                        )
                    },
                )
                return

        tier_context_limit: int | None = None
        if requested_tier is not None:
            try:
                tier_contract = _resolve_requested_tier_contract(requested_tier, persisted_env)
            except RuntimeError as exc:
                json_response(self, 500, {"error": f"Tier validation failed: {exc}"})
                return
            expected_gguf = str(tier_contract.get("GGUF_FILE") or "")
            if expected_gguf.casefold() != str(gguf_file).casefold():
                json_response(
                    self,
                    400,
                    {
                        "error": (
                            f"Tier {requested_tier} resolves to {expected_gguf}, "
                            f"not {gguf_file}"
                        ),
                        "code": "tier_model_mismatch",
                    },
                )
                return
            expected_model = str(tier_contract.get("LLM_MODEL") or "")
            if expected_model and expected_model.casefold() != str(llm_model_name).casefold():
                json_response(
                    self,
                    400,
                    {
                        "error": (
                            f"Tier {requested_tier} resolves to model {expected_model}, "
                            f"not {llm_model_name}"
                        ),
                        "code": "tier_model_mismatch",
                    },
                )
                return
            tier_context_limit = int(tier_contract["MAX_CONTEXT"])
            if requested_context_length is not None and requested_context_length > tier_context_limit:
                json_response(
                    self,
                    400,
                    {
                        "error": (
                            f"Requested context {requested_context_length} exceeds tier "
                            f"{requested_tier} limit {tier_context_limit}"
                        ),
                        "code": "tier_context_mismatch",
                    },
                )
                return
            context_length = min(context_length, tier_context_limit)

        models_ini = INSTALL_DIR / "config" / "llama-server" / "models.ini"
        lemonade_yaml = INSTALL_DIR / "config" / "litellm" / "lemonade.yaml"
        litellm_local_yaml = INSTALL_DIR / "config" / "litellm" / "local.yaml"
        litellm_switchboard_yaml = INSTALL_DIR / "config" / "litellm" / "switchboard.yaml"
        model_router_endpoints = INSTALL_DIR / "config" / "model-router" / "endpoints.json"
        activation_receipt = INSTALL_DIR / "data" / "model-activation-receipt.json"
        hermes_live_config = INSTALL_DIR / "data" / "hermes" / "config.yaml"
        hermes_template_config = INSTALL_DIR / "extensions" / "services" / "hermes" / "cli-config.yaml.template"

        # Hoisted so the outer except's rollback can reference them safely.
        # None means the snapshot was not captured, so rollback must skip it.
        env_snapshot: dict | None = None
        ini_snapshot: dict | None = None
        lemonade_snapshot: dict | None = None
        litellm_local_snapshot: dict | None = None
        litellm_switchboard_snapshot: dict | None = None
        model_router_endpoints_snapshot: dict | None = None
        activation_receipt_snapshot: dict | None = None
        hermes_live_snapshot: dict | None = None
        hermes_template_snapshot: dict | None = None
        opencode_snapshot: dict | None = None
        perplexica_snapshot: dict | None = None
        container_states: dict[str, dict[str, bool]] = {}
        opencode_runtime_state: dict | None = None
        committed = False
        mutation_started = False
        rollback_attempted = False
        runtime_restart_strategy: str | None = None
        opencode_restarted = False
        opencode_config_mutated = False
        litellm_restart_attempted = False
        hermes_config_mutated = False
        hermes_restart_attempted = False
        openclaw_recreate_attempted = False
        perplexica_mutated = False
        apple_llama_bin: Path | None = None
        apple_llama_log: Path | None = None
        apple_pid_file: Path | None = None
        switchboard_run: dict | None = None
        final_runtime_proof: dict[str, object] | None = None

        def restore_backups():
            if env_snapshot is not None:
                _restore_text_file(env_path, env_snapshot)
            if ini_snapshot is not None:
                _restore_text_file(models_ini, ini_snapshot)
            if lemonade_snapshot is not None:
                _restore_text_file(lemonade_yaml, lemonade_snapshot)
            if litellm_local_snapshot is not None:
                _restore_text_file(litellm_local_yaml, litellm_local_snapshot)
            if litellm_switchboard_snapshot is not None:
                _restore_text_file(litellm_switchboard_yaml, litellm_switchboard_snapshot)
            if model_router_endpoints_snapshot is not None:
                _restore_text_file(model_router_endpoints, model_router_endpoints_snapshot)
            if activation_receipt_snapshot is not None:
                _restore_text_file(activation_receipt, activation_receipt_snapshot)
            if hermes_template_snapshot is not None:
                _restore_text_file(hermes_template_config, hermes_template_snapshot)
            if hermes_live_snapshot and hermes_live_snapshot.get("exists"):
                if hermes_live_snapshot.get("source") == "host":
                    _restore_text_file(hermes_live_config, hermes_live_snapshot)
                else:
                    _write_hermes_live_config(
                        hermes_live_config,
                        str(hermes_live_snapshot.get("text") or ""),
                        hermes_live_snapshot.get("source"),
                    )
            elif hermes_live_snapshot is not None:
                _remove_hermes_live_config(hermes_live_config)
            if opencode_snapshot is not None:
                _restore_opencode_config(opencode_snapshot)

        def restore_previous_runtime():
            rollback_env = load_env(env_path)
            if runtime_restart_strategy == "windows-lemonade":
                _restart_windows_lemonade(rollback_env)
            elif runtime_restart_strategy == "windows-native-llama":
                _restart_windows_native_llama_server(env_path, rollback_env)
            elif runtime_restart_strategy == "macos-native-llama":
                if not all((apple_llama_bin, apple_llama_log, apple_pid_file)):
                    raise RuntimeError("macOS native llama rollback paths are unavailable")
                _restart_macos_native_llama_server(
                    env_path,
                    apple_llama_bin,
                    apple_llama_log,
                    apple_pid_file,
                )
            elif runtime_restart_strategy == "container-llama":
                _recreate_llama_server(
                    rollback_env,
                    override_image=str(rollback_env.get("LLAMA_SERVER_IMAGE") or ""),
                )
            elif runtime_restart_strategy == "compose-llama":
                _compose_restart_llama_server(rollback_env)
            elif runtime_restart_strategy is not None:
                raise RuntimeError(
                    f"Unknown model activation restart strategy: {runtime_restart_strategy}"
                )

        def rollback_and_prove() -> tuple[bool, str]:
            """Restore config/runtime/dependents and prove the prior route."""
            nonlocal rollback_attempted
            rollback_attempted = True
            try:
                restore_backups()
                restore_previous_runtime()
                rollback_env = load_env(env_path)
                litellm_restarted = False
                if litellm_restart_attempted:
                    litellm_restarted = _restore_container_state(
                        "ods-litellm", container_states["ods-litellm"]
                    )
                hermes_restarted = False
                if hermes_restart_attempted or hermes_config_mutated:
                    hermes_restarted = _restore_container_state(
                        "ods-hermes", container_states["ods-hermes"]
                    )
                openclaw_recreated = False
                if openclaw_recreate_attempted:
                    openclaw_recreated = _restore_container_state(
                        "ods-openclaw",
                        container_states["ods-openclaw"],
                        recreate=True,
                    )
                if perplexica_mutated and perplexica_snapshot is not None:
                    _restore_perplexica_config(perplexica_snapshot)
                if opencode_config_mutated and opencode_runtime_state and opencode_runtime_state.get("active"):
                    if not _restart_managed_opencode(opencode_runtime_state):
                        raise RuntimeError("managed OpenCode disappeared during rollback")
                previous_gguf = str(rollback_env.get("GGUF_FILE") or "")
                previous_model = str(
                    rollback_env.get("LLM_MODEL")
                    or _local_model_name_from_gguf(previous_gguf)
                )
                previous_windows_native = _is_windows_host_llama_server(rollback_env)
                previous_hermes_model = previous_gguf
                if (
                    not previous_windows_native
                    and str(rollback_env.get("GPU_BACKEND") or "").lower() == "amd"
                ):
                    previous_hermes_model = str(
                        rollback_env.get("LEMONADE_MODEL")
                        or f"extra.{previous_gguf}"
                    )
                if hermes_restarted and hermes_live_snapshot and hermes_live_snapshot.get("exists"):
                    try:
                        previous_context = int(
                            rollback_env.get("MAX_CONTEXT")
                            or rollback_env.get("CTX_SIZE")
                            or 32768
                        )
                    except (TypeError, ValueError):
                        previous_context = 32768
                    previous_base_url = rollback_env.get("HERMES_LLM_BASE_URL") or (
                        "http://litellm:4000/v1"
                        if _is_windows_host_lemonade(rollback_env)
                        else None
                    )
                    _verify_running_hermes_route(
                        previous_hermes_model,
                        previous_base_url,
                        previous_context,
                    )
                    _wait_for_container_health("ods-hermes")
                if not previous_gguf:
                    raise RuntimeError("previous GGUF identity is empty")
                if not _wait_for_model_readiness(
                    rollback_env,
                    model_id=previous_model,
                    gguf_file=previous_gguf,
                    llm_model_name=previous_model,
                    lemonade_model_id=str(rollback_env.get("LEMONADE_MODEL") or ""),
                ):
                    raise RuntimeError(
                        f"previous model {previous_gguf} did not pass identity and completion readiness"
                    )
                if litellm_restarted:
                    _verify_litellm_route(rollback_env)
                if openclaw_recreated:
                    _verify_openclaw_model_env(previous_hermes_model)
                    _wait_for_container_health("ods-openclaw")
                return True, ""
            except Exception as rollback_exc:
                logger.exception("Failed to prove previous model route during rollback")
                return False, str(rollback_exc)

        try:
            # Read current env BEFORE modification — needed for gpu_backend guard
            env_pre = load_env(env_path)
            gpu_backend = env_pre.get("GPU_BACKEND", "nvidia")
            windows_host_lemonade = _is_windows_host_lemonade(env_pre)
            windows_lemonade_managed = _windows_lemonade_is_managed(env_pre)
            windows_native_llama = _is_windows_host_llama_server(env_pre)
            lemonade_runtime = str(gpu_backend).lower() == "amd" and not windows_native_llama
            same_lemonade_target = _runtime_model_identity_matches(
                env_pre.get("GGUF_FILE"),
                gguf_file=gguf_file,
            )
            lemonade_model_id = ""
            windows_lemonade_already_serving = False
            if windows_host_lemonade and same_lemonade_target:
                lemonade_port = env_pre.get("AMD_INFERENCE_PORT", "8080") or "8080"
                lemonade_model_id = _resolve_lemonade_model_id(
                    env_pre,
                    gguf_file,
                    host="127.0.0.1",
                    port=str(lemonade_port),
                )
                windows_lemonade_already_serving = _lemonade_completion_ready(
                    "127.0.0.1",
                    str(lemonade_port),
                    gguf_file,
                    lemonade_model_id,
                )
                if windows_lemonade_already_serving:
                    logger.info(
                        "Windows Lemonade is already serving %s; refreshing configs "
                        "without restarting native Lemonade",
                        gguf_file,
                    )
            if lemonade_runtime and not lemonade_model_id:
                # Keep the persisted route non-empty while a Lemonade activation
                # is still proving readiness. A slow or interrupted restore must
                # never strand dependents with LEMONADE_MODEL=.
                lemonade_model_id = _resolve_lemonade_model_id(env_pre, gguf_file)
            runtime_profile = _select_runtime_profile(model, env_pre)
            runtime_env = {}
            if runtime_profile:
                try:
                    context_length = int(runtime_profile.get("context_length") or context_length)
                except (TypeError, ValueError):
                    pass
                llama_server_image = runtime_profile.get("llama_server_image") or llama_server_image
                runtime_env = runtime_profile.get("env") if isinstance(runtime_profile.get("env"), dict) else {}
            recommended_context = _recommended_activation_context(model_id, model, env_pre)
            if recommended_context is not None:
                context_length = recommended_context
            if requested_context_length is not None:
                context_length = min(int(context_length), requested_context_length)
            if tier_context_limit is not None:
                context_length = min(int(context_length), tier_context_limit)

            if gpu_backend == "apple":
                apple_pid_file = INSTALL_DIR / "data" / ".llama-server.pid"
                apple_llama_bin = INSTALL_DIR / "bin" / "llama-server"
                apple_llama_log = INSTALL_DIR / "data" / "llama-server.log"
                if not apple_llama_bin.is_file():
                    raise RuntimeError(
                        "llama-server binary not found - re-run installer"
                    )

            # Capture every mutable file and service state before the first write.
            env_snapshot = _snapshot_text_file(env_path)
            # A malformed install can leave models.ini as a directory; repair it
            # before snapshotting so activation heals rather than refusing.
            if models_ini.is_dir():
                shutil.rmtree(models_ini)
            ini_snapshot = _snapshot_text_file(models_ini)
            lemonade_snapshot = _snapshot_text_file(lemonade_yaml)
            litellm_local_snapshot = _snapshot_text_file(litellm_local_yaml)
            litellm_switchboard_snapshot = _snapshot_text_file(litellm_switchboard_yaml)
            model_router_endpoints_snapshot = _snapshot_text_file(model_router_endpoints)
            activation_receipt_snapshot = _snapshot_text_file(activation_receipt)
            # Persisted Hermes state is commonly UID-10000-owned. Capture it
            # through the running container when host permissions deny access;
            # activation must never claim success with an unpatched live route.
            hermes_live_snapshot = _capture_hermes_live_config(hermes_live_config)
            hermes_template_snapshot = _snapshot_text_file(hermes_template_config)
            opencode_snapshot = _capture_opencode_config()
            if opencode_snapshot is not None:
                opencode_runtime_state = _capture_managed_opencode_state()
            container_states = {
                name: _capture_container_state(name)
                for name in (
                    "ods-litellm",
                    "ods-hermes",
                    "ods-openclaw",
                    "ods-perplexica",
                )
            }
            perplexica_snapshot = _capture_perplexica_config(
                env_pre,
                container_states["ods-perplexica"],
            )
            active_litellm_consumers = [
                name
                for name in ("ods-hermes", "ods-openclaw", "ods-perplexica")
                if container_states[name]["running"]
            ]
            if (
                opencode_runtime_state
                and opencode_runtime_state.get("active")
                and lemonade_runtime
                and not windows_host_lemonade
            ):
                active_litellm_consumers.append("OpenCode")
            if (
                lemonade_runtime
                and active_litellm_consumers
                and not container_states["ods-litellm"]["running"]
            ):
                raise RuntimeError(
                    "Active Lemonade consumers require LiteLLM, but ods-litellm is "
                    f"stopped: {', '.join(active_litellm_consumers)}"
                )

            # Fail before the first write if a config changed while the other
            # transaction snapshots and runtime states were being captured.
            for path, snapshot in (
                (env_path, env_snapshot),
                (models_ini, ini_snapshot),
                (lemonade_yaml, lemonade_snapshot),
                (litellm_local_yaml, litellm_local_snapshot),
                (litellm_switchboard_yaml, litellm_switchboard_snapshot),
                (model_router_endpoints, model_router_endpoints_snapshot),
                (hermes_template_config, hermes_template_snapshot),
            ):
                _assert_text_file_matches_snapshot(path, snapshot)
            if hermes_live_snapshot.get("source") == "host":
                _assert_text_file_matches_snapshot(
                    hermes_live_config,
                    hermes_live_snapshot,
                )
            if opencode_snapshot is not None:
                for path, snapshot in opencode_snapshot["files"].items():
                    _assert_text_file_matches_snapshot(path, snapshot)

            # Update .env
            mutation_started = True
            if env_path.exists():
                lines = str(env_snapshot.get("text") or "").splitlines()
                updates = {
                    "GGUF_FILE": gguf_file,
                    "GGUF_URL": str(model.get("gguf_url") or ""),
                    "GGUF_SHA256": str(model.get("gguf_sha256") or ""),
                    "LLM_MODEL": llm_model_name,
                    "CTX_SIZE": str(context_length),
                    "MAX_CONTEXT": str(context_length),
                    "MODEL_RUNTIME_PROFILE": runtime_profile.get("id", "") if runtime_profile else "",
                    "MODEL_RUNTIME_PROFILE_LABEL": runtime_profile.get("label", "") if runtime_profile else "",
                    "MODEL_RUNTIME_PROFILE_SOURCE": runtime_profile.get("source_url", "") if runtime_profile else "",
                }
                if requested_tier:
                    updates["TIER"] = requested_tier
                if lemonade_runtime:
                    updates["LEMONADE_MODEL"] = lemonade_model_id
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
                _atomic_write_text(env_path, "\n".join(new_lines) + "\n")

            # Update models.ini
            models_ini.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(
                models_ini,
                f"[{llm_model_name}]\n"
                f"filename = {gguf_file}\n"
                f"load-on-startup = true\n"
                f"n-ctx = {context_length}\n",
            )

            # Switchboard runtime adapter (PR 2A): the non-Lemonade container
            # llama paths stage+verify through one reconciler sequence. All
            # other strategies keep their inline flow until PR 2B/2C.
            switchboard_adapter = None
            switchboard_capabilities = {
                "chat": True,
                "tools": bool(model.get("tools")),
                "vision": bool(model.get("vision")),
                "agentViable": _model_agent_viable(model, int(context_length)),
            }

            def _sb_wait_ready(_env, _gguf, _ctx, lemonade_model_id=""):
                return _wait_for_model_readiness(
                    _env,
                    model_id=model_id,
                    gguf_file=_gguf,
                    llm_model_name=llm_model_name,
                    lemonade_model_id=lemonade_model_id,
                    return_proof=True,
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
                if windows_lemonade_managed and not windows_lemonade_already_serving:
                    runtime_restart_strategy = "windows-lemonade"
                    _restart_windows_lemonade(env)
                elif not windows_lemonade_managed:
                    logger.info(
                        "Using externally managed Windows Lemonade without process restart"
                    )
            elif windows_native_llama:
                runtime_restart_strategy = "windows-native-llama"
                if _switchboard_adapters is not None:
                    switchboard_adapter = _switchboard_adapters.NativeLlamaAdapter(
                        restart=lambda _e: _restart_windows_native_llama_server(
                            env_path, _e
                        ),
                        wait_ready=_sb_wait_ready,
                        expected_gguf=gguf_file,
                        context_length=int(context_length),
                        capabilities=switchboard_capabilities,
                    )
                else:
                    _restart_windows_native_llama_server(env_path, env)
            elif gpu_backend == "apple":
                # macOS: manage native llama-server process via PID file
                if not all((apple_llama_bin, apple_llama_log, apple_pid_file)):
                    raise RuntimeError("macOS native runtime preflight state is unavailable")

                runtime_restart_strategy = "macos-native-llama"
                if _switchboard_adapters is not None:
                    switchboard_adapter = _switchboard_adapters.NativeLlamaAdapter(
                        restart=lambda _e: _restart_macos_native_llama_server(
                            env_path,
                            apple_llama_bin,
                            apple_llama_log,
                            apple_pid_file,
                        ),
                        wait_ready=_sb_wait_ready,
                        expected_gguf=gguf_file,
                        context_length=int(context_length),
                        capabilities=switchboard_capabilities,
                    )
                else:
                    _restart_macos_native_llama_server(
                        env_path,
                        apple_llama_bin,
                        apple_llama_log,
                        apple_pid_file,
                    )
            elif _in_container:
                override_image = (
                    llama_server_image
                    or env.get("LLAMA_SERVER_IMAGE")
                    or (
                        "ghcr.io/ggml-org/llama.cpp:server-cuda-b9014"
                        if gpu_backend == "nvidia"
                        else ""
                    )
                )
                runtime_restart_strategy = "container-llama"
                if _switchboard_adapters is not None and not lemonade_runtime:
                    _sb_override = override_image
                    switchboard_adapter = _switchboard_adapters.ContainerLlamaAdapter(
                        restart=lambda _e, _img=_sb_override: _recreate_llama_server(
                            _e, override_image=_img
                        ),
                        wait_ready=_sb_wait_ready,
                        expected_gguf=gguf_file,
                        context_length=int(context_length),
                        capabilities=switchboard_capabilities,
                    )
                else:
                    _recreate_llama_server(env, override_image=override_image)
            else:
                runtime_restart_strategy = "compose-llama"
                if _switchboard_adapters is not None and not lemonade_runtime:
                    switchboard_adapter = _switchboard_adapters.ContainerLlamaAdapter(
                        restart=_compose_restart_llama_server,
                        wait_ready=_sb_wait_ready,
                        expected_gguf=gguf_file,
                        context_length=int(context_length),
                        capabilities=switchboard_capabilities,
                    )
                else:
                    _compose_restart_llama_server(env)

            if lemonade_runtime:
                lemonade_host, lemonade_port = _lemonade_runtime_address(env)
                lemonade_model_id = _resolve_lemonade_model_id(
                    env,
                    gguf_file,
                    host=lemonade_host,
                    port=lemonade_port,
                )
                if not lemonade_model_id:
                    raise RuntimeError(
                        f"Could not resolve Lemonade model ID for {gguf_file}"
                    )
                if _switchboard_adapters is not None:
                    switchboard_adapter = _switchboard_adapters.LemonadeAdapter(
                        wait_ready=_sb_wait_ready,
                        expected_gguf=gguf_file,
                        context_length=int(context_length),
                        lemonade_model_id=lemonade_model_id,
                        capabilities=switchboard_capabilities,
                    )

            hermes_model_name = (
                gguf_file
                if windows_native_llama
                else lemonade_model_id if lemonade_runtime else gguf_file
            )
            hermes_base_url = env_pre.get("HERMES_LLM_BASE_URL") or (
                "http://litellm:4000/v1" if windows_host_lemonade else None
            )

            if switchboard_adapter is not None:
                switchboard_run = _switchboard_reconciler.run_runtime_activation(
                    switchboard_adapter, env
                )
                healthy = bool(switchboard_run["ok"])
                if not healthy:
                    logger.error(
                        "switchboard runtime activation failed at %s: %s",
                        switchboard_run.get("phase"),
                        switchboard_run.get("detail"),
                    )
                    if switchboard_run.get("phase") == "stage":
                        raise RuntimeError(
                            str(switchboard_run.get("detail") or "runtime stage failed")
                        )
            else:
                runtime_identity = _wait_for_model_readiness(
                    env,
                    model_id=model_id,
                    gguf_file=gguf_file,
                    llm_model_name=llm_model_name,
                    lemonade_model_id=lemonade_model_id,
                    return_identity=True,
                )
                healthy = bool(runtime_identity)

            if healthy:
                if lemonade_runtime:
                    _upsert_env_value(env_path, "LEMONADE_MODEL", lemonade_model_id)
                    env["LEMONADE_MODEL"] = lemonade_model_id
                    if lemonade_yaml.exists() or env.get("ODS_MODE") == "lemonade":
                        _write_lemonade_config(
                            INSTALL_DIR,
                            gguf_file,
                            lemonade_model_id,
                        )

                if windows_native_llama:
                    _write_windows_native_litellm_config(INSTALL_DIR, gguf_file, env)

                _render_model_router_runtime_configs(
                    INSTALL_DIR,
                    env,
                    model=llm_model_name,
                    gguf_file=gguf_file,
                    lemonade_model_id=lemonade_model_id,
                    context_length=int(context_length),
                )

                hermes_live_exists = bool(
                    hermes_live_snapshot and hermes_live_snapshot.get("exists")
                )
                hermes_live_patched = False
                hermes_live_verified = False
                if hermes_live_exists:
                    patched_live, hermes_live_patched = _patch_hermes_config_text(
                        str(hermes_live_snapshot.get("text") or ""),
                        hermes_model_name,
                        base_url=hermes_base_url,
                        context_length=context_length,
                    )
                    if hermes_live_patched:
                        _write_hermes_live_config(
                            hermes_live_config,
                            patched_live,
                            hermes_live_snapshot.get("source"),
                            hermes_live_snapshot.get("mode"),
                        )
                    verified_live = _capture_hermes_live_config(hermes_live_config)
                    hermes_live_verified = _hermes_config_matches(
                        str(verified_live.get("text") or ""),
                        hermes_model_name,
                        hermes_base_url,
                        int(context_length),
                    )
                    if not hermes_live_verified:
                        raise RuntimeError(
                            "Hermes persisted model route could not be verified"
                        )
                hermes_template_patched = _patch_hermes_model_config(
                    hermes_template_config,
                    hermes_model_name,
                    base_url=hermes_base_url,
                    context_length=context_length,
                )
                # A missing live file can be seeded from the patched template
                # on the next Hermes start. An existing file was verified above.
                hermes_patched = hermes_live_patched or (
                    hermes_template_patched and not hermes_live_exists
                )
                hermes_config_mutated = hermes_live_patched or hermes_template_patched

                if opencode_snapshot is not None:
                    opencode_model_id = (
                        lemonade_model_id
                        if windows_host_lemonade
                        else gguf_file if windows_native_llama else llm_model_name
                    )
                    opencode_config_mutated = True
                    _update_opencode_config(
                        env,
                        opencode_snapshot,
                        opencode_model_id,
                        int(context_length),
                        display_name=llm_model_name,
                    )

                # Restart dependent services so they pick up the new model
                litellm_restart_attempted = container_states["ods-litellm"]["running"]
                litellm_restarted = _restart_existing_container(
                    "ods-litellm", container_states["ods-litellm"]
                )
                if litellm_restarted:
                    _verify_litellm_route(env)
                if hermes_patched:
                    hermes_restart_attempted = container_states["ods-hermes"]["running"]
                if hermes_patched and _restart_existing_container(
                    "ods-hermes", container_states["ods-hermes"]
                ):
                    _verify_running_hermes_route(
                        hermes_model_name,
                        hermes_base_url,
                        int(context_length),
                    )
                    _wait_for_container_health("ods-hermes")
                openclaw_recreate_attempted = container_states["ods-openclaw"]["running"]
                openclaw_recreated = _recreate_openclaw_if_present(
                    container_states["ods-openclaw"]
                )
                if perplexica_snapshot is not None:
                    perplexica_mutated = True
                    _update_perplexica_model(
                        env,
                        perplexica_snapshot,
                        gguf_file=gguf_file,
                        lemonade_model_id=lemonade_model_id,
                    )
                if openclaw_recreated:
                    _verify_openclaw_model_env(hermes_model_name)
                    _wait_for_container_health("ods-openclaw")
                if opencode_snapshot is not None and opencode_runtime_state is not None:
                    opencode_restarted = _restart_managed_opencode(opencode_runtime_state)

                final_runtime_proof = _wait_for_model_readiness(
                    env,
                    model_id=model_id,
                    gguf_file=gguf_file,
                    llm_model_name=llm_model_name,
                    lemonade_model_id=lemonade_model_id,
                    attempts=6,
                    initial_delay=0,
                    interval=5,
                    return_proof=True,
                )
                if not final_runtime_proof:
                    raise RuntimeError(
                        "Final runtime proof failed for activated model "
                        f"{gguf_file}; rolling back to previous model"
                    )
                consumers = {
                    "open-webui": "dynamic_route",
                    "dashboard": "live_env",
                    "litellm": (
                        "restarted"
                        if litellm_restarted
                        else "stopped"
                        if container_states["ods-litellm"]["exists"]
                        else "not_installed"
                    ),
                    "hermes": (
                        "restarted"
                        if hermes_restart_attempted
                        else "updated_for_next_start"
                        if hermes_config_mutated
                        else "unchanged"
                    ),
                    "openclaw": (
                        "recreated"
                        if openclaw_recreated
                        else "stopped"
                        if container_states["ods-openclaw"]["exists"]
                        else "not_installed"
                    ),
                    "opencode": (
                        "restarted"
                        if opencode_restarted
                        else "updated_for_next_start"
                        if opencode_config_mutated
                        else "not_installed"
                    ),
                    "perplexica": (
                        "updated"
                        if perplexica_mutated
                        else "stopped"
                        if container_states["ods-perplexica"]["exists"]
                        else "not_installed"
                    ),
                }
                _atomic_write_json(
                    activation_receipt,
                    {
                        "schema": "ods.model-activation-receipt.v1",
                        "status": "complete",
                        "modelId": str(model_id),
                        "llmModel": str(llm_model_name),
                        "ggufFile": str(gguf_file),
                        "runtimeModelId": str(final_runtime_proof.get("identity") or ""),
                        "contextLength": int(final_runtime_proof.get("contextLength") or context_length),
                        "contextVerified": final_runtime_proof.get("contextVerified") is True,
                        "consumers": consumers,
                        "verifiedAt": str(final_runtime_proof.get("verifiedAt") or _iso_now()),
                    },
                )
                committed = True  # system state is committed before the response write
                if (
                    _switchboard_state is not None
                    and final_runtime_proof
                    and final_runtime_proof.get("contextVerified") is True
                ):
                    # Observe mode: record the proven route after the existing
                    # transaction committed. Failures are logged, never fatal,
                    # and never alter activation behavior.
                    try:
                        verified_runtime_identity = str(
                            final_runtime_proof.get("identity") or ""
                        )
                        verified_context_length = int(
                            final_runtime_proof.get("contextLength") or 0
                        )
                        verified_capabilities = (
                            (switchboard_run or {}).get("capabilities") or {}
                        )
                        _switchboard_state.record_verified_route(
                            INSTALL_DIR / "data" / "model-state.json",
                            catalog_id=str(model_id),
                            runtime_model_id=verified_runtime_identity,
                            backend_kind=(
                                "lemonade" if lemonade_model_id else "llama-server"
                            ),
                            endpoint_id=(
                                "lemonade-default"
                                if lemonade_model_id
                                else "llama-server-default"
                            ),
                            native_route=(lemonade_model_id or None),
                            context_length=verified_context_length,
                            capabilities=verified_capabilities,
                            proof_identity=verified_runtime_identity,
                        )
                    except Exception as exc:
                        logger.warning("switchboard state record failed: %s", exc)
                elif _switchboard_state is not None:
                    logger.info(
                        "switchboard verified-state publication deferred for runtime %s",
                        "lemonade" if lemonade_model_id else runtime_restart_strategy,
                    )
                json_response(
                    self,
                    200,
                    {
                        "status": "activated",
                        "model_id": model_id,
                        "llm_model": llm_model_name,
                        "gguf_file": gguf_file,
                        "tier": requested_tier,
                        "context_length": int(context_length),
                        "consumers": consumers,
                    },
                )
            else:
                logger.warning("Model activation failed — rolling back")
                rolled_back, rollback_error = rollback_and_prove()
                error = (
                    "Health check failed — rolled back to previous model"
                    if rolled_back
                    else (
                        "Health check failed; previous model restoration could not be proved: "
                        f"{rollback_error}"
                    )
                )
                payload = {"error": error, "rolled_back": rolled_back}
                if switchboard_run and not switchboard_run.get("ok"):
                    payload["failure_phase"] = switchboard_run.get("phase")
                    payload["failure_detail"] = switchboard_run.get("detail")
                json_response(
                    self,
                    500,
                    payload,
                )

        except Exception as exc:
            rolled_back = False
            rollback_error = ""
            if not committed and mutation_started and not rollback_attempted:
                rolled_back, rollback_error = rollback_and_prove()
            logger.exception("Model activation failed")
            error = f"Model activation failed: {exc}"
            if rollback_error:
                error += f"; rollback could not be proved: {rollback_error}"
            payload = {"error": error}
            if mutation_started:
                payload["rolled_back"] = rolled_back
            if switchboard_run and not switchboard_run.get("ok"):
                payload["failure_phase"] = switchboard_run.get("phase")
                payload["failure_detail"] = switchboard_run.get("detail")
            json_response(self, 500, payload)

    def _handle_model_delete(self):
        """Delete a downloaded GGUF model file."""
        if not check_auth(self):
            return
        body = read_json_body(self)
        if body is None:
            return

        gguf_file = body.get("gguf_file", "")
        if not isinstance(gguf_file, str) or not gguf_file:
            json_response(self, 400, {"error": "gguf_file is required"})
            return

        models_dir = INSTALL_DIR / "data" / "models"
        target = _safe_model_artifact_path(models_dir, gguf_file)
        if target is None:
            json_response(self, 400, {"error": "Invalid file path"})
            return

        acquired, active = _begin_model_lifecycle("model_delete", gguf_file)
        if not acquired:
            json_response(
                self,
                409,
                _model_lifecycle_conflict("model deletion", active),
            )
            return

        try:
            if not target.exists():
                json_response(self, 404, {"error": f"File not found: {gguf_file}"})
                return
            parts_to_delete = [target]
            try:
                library = _load_model_library_records()
            except RuntimeError:
                library = []
            for entry in library:
                if entry.get("gguf_file") == gguf_file and entry.get("gguf_parts"):
                    parts_to_delete = []
                    for part in entry["gguf_parts"]:
                        part_file = _safe_model_artifact_path(models_dir, part.get("file"))
                        if part_file is not None and part_file.exists():
                            parts_to_delete.append(part_file)
                    break

            deleted_names = {path.name for path in parts_to_delete}
            deleted_names.add(gguf_file)
            env = load_env(INSTALL_DIR / ".env")
            if str(env.get("GGUF_FILE") or "") in deleted_names:
                json_response(
                    self,
                    409,
                    {"error": "Cannot delete the currently active model"},
                )
                return
            live_active = _live_runtime_has_model(env, gguf_file)
            if live_active is True:
                json_response(
                    self,
                    409,
                    {"error": "Cannot delete a model still active in the live runtime"},
                )
                return
            if live_active is None:
                json_response(
                    self,
                    503,
                    {
                        "error": (
                            "Cannot prove the model is inactive because live runtime identity "
                            "is unavailable"
                        )
                    },
                )
                return

            for pf in parts_to_delete:
                pf.unlink()

            status_path = INSTALL_DIR / "data" / "model-download-status.json"
            if status_path.exists():
                try:
                    status_data = json.loads(status_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    status_path.unlink(missing_ok=True)
                else:
                    status_model = _download_status_model_token(status_data.get("model"))
                    if status_model in deleted_names:
                        _write_model_status(status_path, "idle", "", 0, 0)
            json_response(self, 200, {"status": "deleted", "gguf_file": gguf_file})
        except OSError as exc:
            json_response(self, 500, {"error": f"Failed to delete: {exc}"})
        finally:
            _end_model_lifecycle("model_delete")


def _runtime_model_identity_tokens(value: object) -> set[str]:
    """Return exact, known runtime aliases for one model identity value."""
    if not isinstance(value, str):
        return set()
    raw = value.strip()
    if not raw:
        return set()

    variants = {raw}
    lowered = raw.casefold()
    for prefix in ("extra.", "user."):
        if lowered.startswith(prefix):
            variants.add(raw[len(prefix):])

    tokens = set()
    for variant in variants:
        normalized = variant.strip().replace("\\", "/").rstrip("/")
        if not normalized:
            continue
        basename = normalized.rsplit("/", 1)[-1]
        for candidate in (normalized, basename):
            folded = candidate.casefold()
            tokens.add(folded)
            if folded.endswith(".gguf"):
                tokens.add(folded[:-5])
    return tokens


def _runtime_model_identity_matches(
    value: object,
    *,
    model_id: str = "",
    gguf_file: str = "",
    llm_model_name: str = "",
) -> bool:
    """Match a runtime identity to exact supported aliases, never substrings."""
    actual = _runtime_model_identity_tokens(value)
    if not actual:
        return False
    expected = set()
    for candidate in (model_id, gguf_file, llm_model_name):
        expected.update(_runtime_model_identity_tokens(candidate))
    return bool(expected and actual.intersection(expected))


def _lemonade_runtime_address(env: dict) -> tuple[str, str]:
    """Return the Lemonade address reachable from this host-agent process."""
    location = str(env.get("AMD_INFERENCE_LOCATION") or "").lower()
    if _is_windows_host_lemonade(env) or location == "host":
        return (
            "127.0.0.1",
            str(env.get("AMD_INFERENCE_PORT") or env.get("OLLAMA_PORT") or "8080"),
        )
    if os.environ.get("ODS_HOST_INSTALL_DIR"):
        return "ods-llama-server", "8080"
    return "127.0.0.1", str(env.get("OLLAMA_PORT") or "8080")


def _lemonade_catalog_values(value: object):
    """Yield string leaves from Lemonade checkpoint metadata."""
    if isinstance(value, str):
        if value.strip():
            yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _lemonade_catalog_values(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _lemonade_catalog_values(nested)


def _lemonade_catalog_model_id(body: str, gguf_file: str) -> str:
    """Return the exact catalog ID whose ID/checkpoint matches ``gguf_file``."""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return ""
    models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return ""
    for entry in models:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id")
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        candidates = [model_id]
        candidates.extend(_lemonade_catalog_values(entry.get("checkpoint")))
        candidates.extend(_lemonade_catalog_values(entry.get("checkpoints")))
        for candidate in candidates:
            normalized = candidate.strip().replace("\\", "/").rstrip("/")
            leaf = normalized.rsplit("/", 1)[-1]
            if ":" in leaf:
                leaf = leaf.rsplit(":", 1)[-1]
            if _runtime_model_identity_matches(candidate, gguf_file=gguf_file) or (
                leaf != candidate
                and _runtime_model_identity_matches(leaf, gguf_file=gguf_file)
            ):
                return model_id.strip()
    return ""


def _lemonade_uses_stem_ids(version: object) -> bool:
    """Return whether ``version`` is Lemonade 10.7 or newer."""
    match = re.search(r"\d+(?:\.\d+){1,3}", str(version or ""))
    if not match:
        return False
    try:
        parts = tuple(int(part) for part in match.group(0).split("."))
    except ValueError:
        return False
    return (parts + (0, 0, 0, 0))[:4] >= (10, 7, 0, 0)


def _resolve_lemonade_model_id(
    env: dict,
    gguf_file: str,
    *,
    host: str | None = None,
    port: str | None = None,
) -> str:
    """Resolve the exact request ID Lemonade assigned to a local GGUF.

    Prefer the live model catalog, whose checkpoint metadata survives naming
    changes. A persisted ID is a fallback only when it belongs to the requested
    configured GGUF. Lemonade 10.7 changed
    extra-directory IDs to filename stems, so the health version determines
    the fallback when the catalog is not ready yet. An absent/older version
    deliberately keeps the legacy Linux ``extra.<file>.gguf`` behavior.
    """
    normalized = str(gguf_file or "").strip().replace("\\", "/").rstrip("/")
    filename = normalized.rsplit("/", 1)[-1]
    if not filename:
        return ""
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    persisted = str(env.get("LEMONADE_MODEL") or "").strip()
    persisted_matches_target = bool(
        persisted
        and _runtime_model_identity_matches(
            persisted,
            gguf_file=filename,
            llm_model_name=stem,
        )
    )
    if host is None or port is None:
        resolved_host, resolved_port = _lemonade_runtime_address(env)
        host = host or resolved_host
        port = port or resolved_port

    version = ""
    for path, timeout in (("/api/v1/models", 5), ("/api/v1/health", 5)):
        try:
            result = subprocess.run(
                [
                    "curl", "-sf", "--max-time", str(timeout),
                    f"http://{host}:{port}{path}",
                ],
                capture_output=True,
                text=True,
                timeout=timeout + 5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0:
            continue
        if path.endswith("/models"):
            live_id = _lemonade_catalog_model_id(result.stdout, filename)
            if live_id:
                return live_id
            continue
        try:
            health = json.loads(result.stdout or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(health, dict):
            version = health.get("version") or ""

    if persisted_matches_target:
        return persisted
    return stem if _lemonade_uses_stem_ids(version) else f"extra.{filename}"


def _check_llama_model_identity(
    body: str,
    *,
    model_id: str,
    gguf_file: str,
    llm_model_name: str,
) -> bool:
    """Return True only when llama.cpp reports the requested model loaded."""
    return bool(
        _llama_loaded_model_identity(
            body,
            model_id=model_id,
            gguf_file=gguf_file,
            llm_model_name=llm_model_name,
        )
    )


def _llama_loaded_model_identity(
    body: str,
    *,
    model_id: str,
    gguf_file: str,
    llm_model_name: str,
) -> str:
    """Return the concrete identity carried by llama.cpp's loaded-model row."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return ""
    models = data.get("data") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return ""
    for model in models:
        if not isinstance(model, dict):
            continue
        status = model.get("status")
        if isinstance(status, dict):
            status = status.get("value")
        if status is not None and str(status).strip().casefold() != "loaded":
            continue
        if _runtime_model_identity_matches(
            model.get("id"),
            model_id=model_id,
            gguf_file=gguf_file,
            llm_model_name=llm_model_name,
        ):
            return str(model["id"]).strip()
    return ""


def _live_runtime_has_model(env: dict, gguf_file: str) -> bool | None:
    """Return whether the live local runtime reports ``gguf_file`` active."""
    if str(env.get("ODS_MODE") or "local").lower() == "cloud":
        return False
    gpu_backend = str(env.get("GPU_BACKEND") or "nvidia").lower()
    windows_native_llama = _is_windows_host_llama_server(env)
    is_lemonade = gpu_backend == "amd" and not windows_native_llama
    if is_lemonade:
        host, port = _lemonade_runtime_address(env)
    elif windows_native_llama:
        host = "127.0.0.1"
        port = str(env.get("AMD_INFERENCE_PORT") or env.get("OLLAMA_PORT") or "8080")
    elif gpu_backend == "apple":
        host = _native_llama_health_host(env)
        port = str(env.get("ODS_NATIVE_LLAMA_PORT") or env.get("OLLAMA_PORT") or "8080")
    elif os.environ.get("ODS_HOST_INSTALL_DIR"):
        host = "ods-llama-server"
        port = "8080"
    else:
        host = "127.0.0.1"
        port = str(env.get("OLLAMA_PORT") or "8080")
    path = "/api/v1/health" if is_lemonade else "/v1/models"
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "5", f"http://{host}:{port}{path}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout or "{}")
    except (json.JSONDecodeError, OSError, subprocess.TimeoutExpired):
        return None
    body = json.dumps(data)
    if is_lemonade:
        if not isinstance(data, dict) or "model_loaded" not in data:
            return None
        lemonade_model_id = _resolve_lemonade_model_id(
            env,
            gguf_file,
            host=host,
            port=port,
        )
        return _check_lemonade_health(body, gguf_file, lemonade_model_id)
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        return None
    local_name = _local_model_name_from_gguf(gguf_file)
    return _check_llama_model_identity(
        body,
        model_id=local_name,
        gguf_file=gguf_file,
        llm_model_name=local_name,
    )


def _check_lemonade_health(
    body: str,
    expected_gguf_file: str | None = None,
    expected_model_id: str = "",
    expected_context: int | None = None,
) -> bool:
    """Check if Lemonade health response indicates a model is loaded.

    Lemonade returns {"status": "ok", "model_loaded": null} when healthy
    but no model is loaded yet. Activation callers pass expected_gguf_file so
    success requires that exact target. The optional generic form still
    requires a non-empty string identity; false and empty values are never
    treated as a loaded model.
    """
    return bool(
        _lemonade_loaded_model_identity(
            body,
            expected_gguf_file=expected_gguf_file,
            expected_model_id=expected_model_id,
            expected_context=expected_context,
        )
    )


def _lemonade_loaded_model_identity(
    body: str,
    expected_gguf_file: str | None = None,
    expected_model_id: str = "",
    expected_context: int | None = None,
) -> str:
    """Return the concrete identity carried by a valid Lemonade health row."""
    try:
        data = json.loads(body)
        if expected_gguf_file is not None or expected_model_id:
            if not isinstance(data, dict) or str(data.get("status") or "").casefold() != "ok":
                return ""
            if not _runtime_model_identity_matches(
                data.get("model_loaded"),
                model_id=expected_model_id,
                gguf_file=expected_gguf_file or "",
            ):
                return ""
            if not _lemonade_loaded_context_is_sufficient(
                data,
                expected_gguf_file=expected_gguf_file or "",
                expected_model_id=expected_model_id,
                expected_context=expected_context,
            ):
                return ""
            return str(data.get("model_loaded") or "").strip()
        loaded = data.get("model_loaded")
        return loaded.strip() if isinstance(loaded, str) else ""
    except (AttributeError, json.JSONDecodeError, TypeError):
        return ""


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _recommended_activation_context(model_id: str, model: dict, env: dict) -> int | None:
    """Return installer-persisted context when activating that recommendation."""
    context = _positive_int(env.get("MODEL_RECOMMENDED_CONTEXT"))
    if context is None:
        return None

    gguf_file = str(model.get("gguf_file") or "")
    llm_model_name = str(model.get("llm_model_name") or model_id or "")
    recommended_values = (
        env.get("MODEL_RECOMMENDED_GGUF"),
        env.get("MODEL_RECOMMENDED_MODEL"),
    )
    for value in recommended_values:
        if _runtime_model_identity_matches(
            value,
            model_id=model_id,
            gguf_file=gguf_file,
            llm_model_name=llm_model_name,
        ):
            return context
    return None


def _model_agent_viable(model: dict, context_length: int) -> bool:
    compatibility = model.get("app_compatibility")
    if not isinstance(compatibility, dict):
        compatibility = {}
    viability = compatibility.get("agent_viability")
    if not isinstance(viability, dict):
        viability = {}
    return (
        int(context_length) >= 65536
        and viability.get("status") != "not_agent_viable"
    )


def _lemonade_version_at_least(value: object, major: int, minor: int) -> bool:
    match = re.match(r"^\s*(\d+)\.(\d+)", str(value or ""))
    if not match:
        return False
    return (int(match.group(1)), int(match.group(2))) >= (major, minor)


def _lemonade_loaded_context_is_sufficient(
    data: dict,
    *,
    expected_gguf_file: str,
    expected_model_id: str,
    expected_context: int | None,
) -> bool:
    expected_context = _positive_int(expected_context)
    if not expected_context:
        return True
    loaded = data.get("all_models_loaded")
    if not isinstance(loaded, list):
        return not _lemonade_version_at_least(data.get("version"), 10, 7)
    loaded_entry = _lemonade_loaded_model_entry(
        loaded,
        expected_gguf_file=expected_gguf_file,
        expected_model_id=expected_model_id,
    )
    if loaded_entry is None:
        return False
    recipe_options = loaded_entry.get("recipe_options")
    if not isinstance(recipe_options, dict):
        recipe_options = {}
    actual_context = _positive_int(
        recipe_options.get("ctx_size") or loaded_entry.get("ctx_size")
    )
    if actual_context is not None:
        return actual_context >= expected_context
    # Lemonade before 10.7 reports the exact loaded checkpoint but does not
    # expose its context size. Preserve compatibility for those versions while
    # requiring modern runtimes to carry the context proof they advertise.
    return not _lemonade_version_at_least(data.get("version"), 10, 7)


def _lemonade_loaded_model_entry(
    loaded: list,
    *,
    expected_gguf_file: str,
    expected_model_id: str,
) -> dict | None:
    """Return the exact loaded Lemonade row for the requested model."""
    for entry in loaded:
        if not isinstance(entry, dict):
            continue
        if (
            _runtime_model_identity_matches(
                entry.get("model_name"),
                model_id=expected_model_id,
                gguf_file=expected_gguf_file,
            )
            or _runtime_model_identity_matches(
                entry.get("checkpoint"),
                model_id=expected_model_id,
                gguf_file=expected_gguf_file,
            )
        ):
            return entry
    return None


def _lemonade_loaded_context_length(
    body_or_data: str | dict,
    *,
    expected_gguf_file: str,
    expected_model_id: str,
) -> int | None:
    """Return the context reported for the matching loaded Lemonade model."""
    try:
        data = json.loads(body_or_data) if isinstance(body_or_data, str) else body_or_data
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    loaded = data.get("all_models_loaded")
    if not isinstance(loaded, list):
        return None
    entry = _lemonade_loaded_model_entry(
        loaded,
        expected_gguf_file=expected_gguf_file,
        expected_model_id=expected_model_id,
    )
    if entry is None:
        return None
    recipe_options = entry.get("recipe_options")
    if not isinstance(recipe_options, dict):
        recipe_options = {}
    return _positive_int(recipe_options.get("ctx_size") or entry.get("ctx_size"))


def _llama_runtime_context_length(host: str, port: str) -> int:
    """Return llama.cpp's actual context from its /props endpoint."""
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "5", f"http://{host}:{port}/props"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return 0
        data = json.loads(result.stdout or "{}")
        settings = data.get("default_generation_settings")
        if not isinstance(settings, dict):
            return 0
        return _positive_int(settings.get("n_ctx")) or 0
    except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError, TypeError):
        return 0


def _completion_text(data: object) -> str:
    """Extract bounded OpenAI-compatible assistant text from one response."""
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    choice = choices[0]
    if not isinstance(choice, dict):
        return ""
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else choice.get("text")
    if isinstance(content, str) and content.strip():
        return content[:4096]
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        text = "".join(parts)
        if text.strip():
            return text[:4096]
    reasoning_content = (
        message.get("reasoning_content")
        if isinstance(message, dict)
        else choice.get("reasoning_content")
    )
    if isinstance(reasoning_content, str):
        return reasoning_content[:4096]
    if isinstance(content, str):
        return content[:4096]
    return ""


def _meaningful_completion(data: object) -> bool:
    """Reject empty, punctuation-only, and pathological all-question output."""
    text = _completion_text(data).strip()
    if not text or not any(character.isalnum() for character in text):
        return False
    non_space = "".join(character for character in text if not character.isspace())
    return bool(non_space) and set(non_space) != {"?"}


def _chat_completion_ready(
    host: str,
    port: str,
    model_name: str,
    api_prefix: str = "/v1",
    api_key: str = "",
    *,
    expected_model_id: str = "",
    expected_gguf_file: str = "",
    expected_llm_model_name: str = "",
) -> bool:
    """Require a meaningful completion and, when requested, its model identity."""
    prefix = "/" + api_prefix.strip("/")
    url = f"http://{host}:{port}{prefix}/chat/completions"
    payload = json.dumps({
        "model": model_name,
        "messages": [{
            "role": "user",
            "content": "Reply with the single word READY.",
        }],
        "max_tokens": 8,
        "temperature": 0,
    })
    try:
        command = [
            "curl", "-sf", "--max-time", "30", "--max-filesize", "65536",
            "-X", "POST", url,
            "-H", "Content-Type: application/json",
        ]
        if api_key:
            command.extend(["-H", f"Authorization: Bearer {api_key}"])
        command.extend(["-d", payload])
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=35,
        )
        if result.returncode != 0:
            return False
        response = json.loads(result.stdout or "{}")
        if not _meaningful_completion(response):
            return False
        if expected_model_id or expected_gguf_file or expected_llm_model_name:
            if not isinstance(response, dict) or not _runtime_model_identity_matches(
                response.get("model"),
                model_id=expected_model_id,
                gguf_file=expected_gguf_file,
                llm_model_name=expected_llm_model_name,
            ):
                return False
        return True
    except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError):
        return False


def _native_llama_health_host(env: dict) -> str:
    """Return a URL-safe host reachable through the native llama bind."""
    bind_addr = str(env.get("BIND_ADDRESS") or "").strip() or "127.0.0.1"
    if bind_addr == "0.0.0.0":
        return "127.0.0.1"
    if bind_addr == "::":
        return "[::1]"
    if ":" in bind_addr and not bind_addr.startswith("["):
        return f"[{bind_addr}]"
    return bind_addr


def _require_macos_bridge_manager(env_path: Path) -> tuple[Path, Path]:
    """Return installed bridge lifecycle files or fail before listener shutdown."""
    candidates = (
        (
            INSTALL_DIR / "lib" / "constants.sh",
            INSTALL_DIR / "lib" / "bridge-manager.sh",
        ),
        (
            INSTALL_DIR / "installers" / "macos" / "lib" / "constants.sh",
            INSTALL_DIR / "installers" / "macos" / "lib" / "bridge-manager.sh",
        ),
    )
    for constants_path, manager_path in candidates:
        if constants_path.is_file() and manager_path.is_file():
            break
    else:
        expected = "; ".join(
            f"{constants_path}, {manager_path}"
            for constants_path, manager_path in candidates
        )
        raise RuntimeError(
            "macOS bridge lifecycle files are missing from installed and source layouts: "
            f"{expected}; re-run the installer"
        )
    if not env_path.is_file():
        raise RuntimeError(f"macOS bridge configuration requires {env_path}")
    return constants_path, manager_path


def _configure_macos_llm_bridge(env_path: Path) -> None:
    """Apply the installed shared macOS LLM bridge manager to current .env."""
    constants_path, manager_path = _require_macos_bridge_manager(env_path)

    bash = _find_usable_bash()
    if not bash:
        raise RuntimeError("A usable Bash executable is required for macOS bridge management")

    bridge_adapter = r'''
set -euo pipefail
install_dir="$1"
env_file="$2"
constants_file="$3"
bridge_manager_file="$4"
export ODS_HOME="$install_dir"
export ODS_SCRIPT_HINT="$install_dir"

source "$constants_file"
source "$bridge_manager_file"

ai_err() { printf '%s\n' "$*" >&2; }
ai_ok() { printf '%s\n' "$*" >&2; }

read_env_value() {
    local source_file="$1" key="$2"
    awk -v key="$key" '
        index($0, key "=") == 1 {
            sub(/^[^=]*=/, "")
            sub(/\r$/, "")
            print
            exit
        }
    ' "$source_file"
}

upsert_env_value() {
    local target_file="$1" key="$2" value="$3" tmp_file
    tmp_file="${target_file}.bridge.$$"
    if ! cp -p "$target_file" "$tmp_file"; then
        rm -f "$tmp_file"
        return 1
    fi
    if ! awk -v key="$key" -v value="$value" '
        BEGIN { found = 0 }
        index($0, key "=") == 1 {
            if (!found) {
                print key "=" value
                found = 1
            }
            next
        }
        { print }
        END {
            if (!found) print key "=" value
        }
    ' "$target_file" > "$tmp_file"; then
        rm -f "$tmp_file"
        return 1
    fi
    mv -f "$tmp_file" "$target_file"
}

macos_configure_llm_bridge_from_env "$env_file" "$install_dir"
'''
    result = subprocess.run(
        [
            bash,
            "-c",
            bridge_adapter,
            "ods-host-agent",
            str(INSTALL_DIR),
            str(env_path),
            str(constants_path),
            str(manager_path),
        ],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or "no output"
        raise RuntimeError(
            f"macOS LLM bridge configuration failed (exit {result.returncode}): "
            f"{detail[-1000:]}"
        )


def _send_lemonade_warmup(host: str, port: str, model_id: str, attempt: int) -> bool:
    """Send a warm-up chat completion to trigger Lemonade on-demand model load.

    Lemonade discovers models from its configured extra_models_dir but only
    loads them when a request arrives for that model ID. Returns True if the
    request was accepted (model is loading). Mirrors bootstrap-upgrade.sh.
    """
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


def _lemonade_completion_ready(
    host: str,
    port: str,
    gguf_file: str,
    lemonade_model_id: str = "",
) -> bool:
    """Return True when Lemonade can complete against the requested GGUF."""
    return _chat_completion_ready(
        host,
        port,
        lemonade_model_id or f"extra.{gguf_file}",
        api_prefix="/api/v1",
    )


def _wait_for_model_readiness(
    env: dict,
    *,
    model_id: str,
    gguf_file: str,
    llm_model_name: str,
    lemonade_model_id: str = "",
    attempts: int = 60,
    initial_delay: float = 5,
    interval: float = 5,
    return_identity: bool = False,
    return_proof: bool = False,
    cancel_event: threading.Event | None = None,
) -> bool | str | dict[str, object]:
    """Prove exact runtime identity and one matching meaningful completion.

    Legacy callers receive a boolean. Identity callers receive the concrete
    runtime identity. Adapters receive identity, actual context, and proof time.
    """
    gpu_backend = str(env.get("GPU_BACKEND") or "nvidia").lower()
    windows_native_llama = _is_windows_host_llama_server(env)
    is_lemonade = gpu_backend == "amd" and not windows_native_llama
    if is_lemonade:
        host, port = _lemonade_runtime_address(env)
    elif windows_native_llama:
        host = "127.0.0.1"
        port = str(env.get("AMD_INFERENCE_PORT") or env.get("OLLAMA_PORT") or "8080")
    elif gpu_backend == "apple":
        host = _native_llama_health_host(env)
        port = str(env.get("ODS_NATIVE_LLAMA_PORT") or env.get("OLLAMA_PORT") or "8080")
    elif os.environ.get("ODS_HOST_INSTALL_DIR"):
        host = "ods-llama-server"
        port = "8080"
    else:
        host = "127.0.0.1"
        port = str(env.get("OLLAMA_PORT") or "8080")

    identity_path = "/api/v1/health" if is_lemonade else "/v1/models"
    identity_url = f"http://{host}:{port}{identity_path}"
    completion_model = llm_model_name or gguf_file
    completion_prefix = "/v1"
    if is_lemonade:
        lemonade_model_id = lemonade_model_id or _resolve_lemonade_model_id(
            env,
            gguf_file,
            host=host,
            port=port,
        )
        completion_model = lemonade_model_id
        completion_prefix = str(env.get("LEMONADE_API_BASE_PATH") or "/api/v1")
    expected_context = _positive_int(env.get("CTX_SIZE") or env.get("MAX_CONTEXT"))

    logger.info("Waiting for requested model identity %s at %s", gguf_file, identity_url)
    warmup_sent = False
    if cancel_event is not None and cancel_event.is_set():
        logger.info("Model readiness cancelled before probing %s", gguf_file)
        return {} if return_proof else "" if return_identity else False
    if initial_delay > 0:
        if cancel_event is not None:
            if cancel_event.wait(initial_delay):
                logger.info("Model readiness cancelled before probing %s", gguf_file)
                return {} if return_proof else "" if return_identity else False
        else:
            time.sleep(initial_delay)
    for attempt in range(max(1, attempts)):
        if cancel_event is not None and cancel_event.is_set():
            logger.info("Model readiness cancelled while probing %s", gguf_file)
            return {} if return_proof else "" if return_identity else False
        runtime_identity = ""
        runtime_context = 0
        try:
            result = subprocess.run(
                ["curl", "-s", "--max-time", "5", identity_url],
                capture_output=True,
                text=True,
                timeout=10,
            )
            body = result.stdout.strip()
            if is_lemonade:
                runtime_identity = _lemonade_loaded_model_identity(
                    body,
                    gguf_file,
                    lemonade_model_id,
                    expected_context,
                )
                if runtime_identity:
                    runtime_context = _lemonade_loaded_context_length(
                        body,
                        expected_gguf_file=gguf_file,
                        expected_model_id=lemonade_model_id,
                    ) or 0
                if not runtime_identity and body and (not warmup_sent or attempt % 3 == 0):
                    warmup_sent = _send_lemonade_warmup(
                        host,
                        port,
                        lemonade_model_id,
                        attempt,
                    )
            else:
                runtime_identity = _llama_loaded_model_identity(
                    body,
                    model_id=model_id,
                    gguf_file=gguf_file,
                    llm_model_name=llm_model_name,
                )
                if runtime_identity:
                    runtime_context = _llama_runtime_context_length(host, port)
                    if expected_context and runtime_context < expected_context:
                        runtime_identity = ""
            completion_request_model = (
                str(runtime_identity)
                if is_lemonade and runtime_identity
                else str(completion_model)
            )
            if runtime_identity and _chat_completion_ready(
                host,
                port,
                completion_request_model,
                completion_prefix,
                expected_model_id=str(runtime_identity),
                expected_gguf_file=gguf_file,
                expected_llm_model_name=llm_model_name,
            ):
                logger.info("Model %s ready after %d attempts", gguf_file, attempt + 1)
                if return_proof:
                    reported_context = runtime_context or expected_context or 0
                    if reported_context <= 0:
                        return {}
                    return {
                        "identity": runtime_identity,
                        "contextLength": reported_context,
                        "contextVerified": runtime_context > 0,
                        "verifiedAt": _iso_now(),
                    }
                return runtime_identity if return_identity else True
            if attempt % 6 == 0:
                logger.info(
                    "Model %s readiness incomplete (attempt %d, identity=%s)",
                    gguf_file,
                    attempt + 1,
                    bool(runtime_identity),
                )
        except subprocess.TimeoutExpired:
            if attempt % 6 == 0:
                logger.info("Model readiness attempt %d timed out", attempt + 1)
        if attempt + 1 < attempts and interval > 0:
            if cancel_event is not None:
                if cancel_event.wait(interval):
                    logger.info("Model readiness cancelled while probing %s", gguf_file)
                    return {} if return_proof else "" if return_identity else False
            else:
                time.sleep(interval)
    if return_proof:
        return {}
    return "" if return_identity else False


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


def _windows_lemonade_is_managed(env: dict) -> bool:
    managed = str(env.get("AMD_INFERENCE_MANAGED") or "true").lower()
    runtime_mode = str(env.get("AMD_INFERENCE_RUNTIME_MODE") or "").lower()
    external = str(env.get("LEMONADE_EXTERNAL") or "false").lower()
    return managed != "false" and runtime_mode != "external-lemonade" and external != "true"


def _is_windows_host_llama_server(env: dict) -> bool:
    runtime = env.get("AMD_INFERENCE_RUNTIME", "").lower()
    runtime_mode = env.get("AMD_INFERENCE_RUNTIME_MODE", "").lower()
    backend = env.get("LLM_BACKEND", "").lower()
    location = env.get("AMD_INFERENCE_LOCATION", "").lower()
    managed = env.get("AMD_INFERENCE_MANAGED", "true").lower()
    return (
        platform.system().lower() == "windows"
        and env.get("GPU_BACKEND", "").lower() == "amd"
        and location == "host"
        and managed != "false"
        and (
            runtime_mode == "windows-llama-server-fallback"
            or runtime == "llama-server"
            or backend == "llama-server"
        )
    )


def _restart_windows_native_llama_server(env_path: Path, env: dict):
    """Restart managed native Windows llama-server.exe with the active .env."""
    llama_bin = INSTALL_DIR / "llama-server" / "llama-server.exe"
    llama_log = INSTALL_DIR / "data" / "llama-server.log"
    pid_file = INSTALL_DIR / "data" / "llama-server.pid"
    gguf_file = env.get("GGUF_FILE", "")
    model_path = INSTALL_DIR / "data" / "models" / gguf_file
    port = env.get("AMD_INFERENCE_PORT") or env.get("OLLAMA_PORT") or "8080"

    if not llama_bin.exists():
        raise RuntimeError(f"llama-server.exe not found at {llama_bin}")
    if not _model_file_ready(model_path):
        raise RuntimeError(f"Model file not ready for native llama-server: {model_path}")

    ps_env = os.environ.copy()
    ps_env.update({
        "ODS_WIN_LLAMA_EXE": str(llama_bin),
        "ODS_WIN_LLAMA_PID_FILE": str(pid_file),
        "ODS_WIN_LLAMA_PORT": str(port),
    })
    script = r'''
$ErrorActionPreference = "Stop"
$llamaExe = $env:ODS_WIN_LLAMA_EXE
$pidPath = $env:ODS_WIN_LLAMA_PID_FILE
$port = [int]$env:ODS_WIN_LLAMA_PORT

function Test-ODSLlamaProcess {
    param($Proc)
    if (-not $Proc) { return $false }
    if ($Proc.Name -like "llama-server*") { return $true }
    if ($Proc.ExecutablePath -and $Proc.ExecutablePath.Equals($llamaExe, [StringComparison]::OrdinalIgnoreCase)) { return $true }
    if ($Proc.CommandLine -and $Proc.CommandLine.IndexOf("llama-server", [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
    return $false
}

function Stop-ODSLlamaProcessId {
    param([int]$ProcId)
    $proc = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $ProcId) -ErrorAction SilentlyContinue
    if (-not (Test-ODSLlamaProcess $proc)) { return }
    Stop-Process -Id $ProcId -Force -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 30; $i++) {
        if (-not (Get-Process -Id $ProcId -ErrorAction SilentlyContinue)) { return }
        Start-Sleep -Milliseconds 500
    }
    & taskkill.exe /PID $ProcId /F | Out-Null
    for ($i = 0; $i -lt 30; $i++) {
        if (-not (Get-Process -Id $ProcId -ErrorAction SilentlyContinue)) { return }
        Start-Sleep -Milliseconds 500
    }
    throw "Could not stop native llama-server process $ProcId"
}

if (Test-Path $pidPath) {
    $rawPid = (Get-Content -LiteralPath $pidPath -Raw).Trim()
    if ($rawPid -match "^\d+$") {
        Stop-ODSLlamaProcessId -ProcId ([int]$rawPid)
    }
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}

foreach ($listener in @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Ignore)) {
    if ($listener.OwningProcess -gt 0) {
        Stop-ODSLlamaProcessId -ProcId ([int]$listener.OwningProcess)
    }
}
exit 0
'''
    ps_cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
    try:
        result = subprocess.run(ps_cmd, capture_output=True, text=True, timeout=90, env=ps_env)
    except FileNotFoundError:
        result = subprocess.run(
            ["pwsh.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            timeout=90,
            env=ps_env,
        )
    if result.returncode != 0:
        raise RuntimeError(
            "Windows native llama-server stop failed: "
            f"{(result.stderr or result.stdout).strip()[:500]}"
        )

    _launch_native_llama_server(env_path, llama_bin, llama_log, pid_file)


def _restart_windows_lemonade(env: dict):
    """Restart managed Windows Lemonade from the host-agent process.

    Dashboard model activation runs through the persistent host-agent. Launching
    Lemonade directly from that process avoids Task Scheduler hangs seen from
    remote management sessions while still keeping cleanup bounded.
    """
    if not _windows_lemonade_is_managed(env):
        raise RuntimeError("Refusing to restart externally managed Windows Lemonade")
    exe = None
    executable_names = ("LemonadeServer.exe", "lemonade-server.exe")
    install_folders = ("Lemonade Server", "lemonade_server", "LemonadeServer")
    install_roots = (
        os.environ.get("LOCALAPPDATA"),
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
    )
    for root in install_roots:
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
        raise RuntimeError("Lemonade server executable not found under supported Windows install roots")

    ps_env = os.environ.copy()
    ps_env.update({
        "ODS_WIN_LEMONADE_EXE": str(exe),
        "ODS_WIN_LEMONADE_HELPER": str(
            INSTALL_DIR / "installers" / "windows" / "lib" / "backend-contract.ps1"
        ),
        "ODS_WIN_ENV_PATH": str(INSTALL_DIR / ".env"),
        "ODS_WIN_LEMONADE_DIAGNOSTIC_LOG": str(
            INSTALL_DIR / "logs" / "lemonade-launch.log"
        ),
        "ODS_WIN_MODELS_DIR": str(INSTALL_DIR / "data" / "models"),
        "ODS_WIN_PID_FILE": str(INSTALL_DIR / "data" / "llama-server.pid"),
        "ODS_WIN_LEMONADE_PORT": env.get("AMD_INFERENCE_PORT", "8080") or "8080",
        "ODS_WIN_BIND_ADDR": env.get("BIND_ADDRESS", "127.0.0.1") or "127.0.0.1",
        "ODS_WIN_CONTEXT_SIZE": str(env.get("CTX_SIZE") or env.get("MAX_CONTEXT") or "0"),
    })
    script = r'''
$ErrorActionPreference = "Stop"
$exe = $env:ODS_WIN_LEMONADE_EXE
$helperPath = $env:ODS_WIN_LEMONADE_HELPER
$envPath = $env:ODS_WIN_ENV_PATH
$diagnosticLog = $env:ODS_WIN_LEMONADE_DIAGNOSTIC_LOG
$modelsDir = $env:ODS_WIN_MODELS_DIR
$pidPath = $env:ODS_WIN_PID_FILE
$port = [int]$env:ODS_WIN_LEMONADE_PORT
$bindAddr = $env:ODS_WIN_BIND_ADDR
$contextSize = 0
$null = [int]::TryParse([string]$env:ODS_WIN_CONTEXT_SIZE, [ref]$contextSize)
if (-not (Test-Path -LiteralPath $helperPath -PathType Leaf)) {
    throw "Windows Lemonade launch helper not found: $helperPath"
}
. $helperPath
$adminApiKey = Get-ODSLemonadeAdminApiKey -EnvPath $envPath
$launchContract = Get-ODSLemonadeLaunchContract `
    -ExecutablePath $exe -Port $port -BindAddress $bindAddr `
    -ModelsDir $modelsDir -AdminApiKey $adminApiKey
$bindAddr = $launchContract.BindAddress
$binDir = Split-Path -Parent $exe
$userProfile = [Environment]::GetFolderPath("UserProfile")
$cacheBin = if ($userProfile) { Join-Path (Join-Path (Join-Path $userProfile ".cache") "lemonade") "bin" } else { $null }
$binPrefix = $binDir.TrimEnd('\') + '\'
$cachePrefix = if ($cacheBin) { $cacheBin.TrimEnd('\') + '\' } else { $null }
$knownProcessNames = @("LemonadeServer.exe", "lemonade-server.exe", "lemonade-router.exe", "lemonade.exe")

function Get-ODSPortOwners {
    $owners = @{}
    foreach ($listener in @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
        if ($listener.OwningProcess -gt 0) {
            $owners[[int]$listener.OwningProcess] = $true
        }
    }
    return $owners
}

function Test-ODSLemonadeProcess {
    param($Proc, [hashtable]$PortOwners = $null)
    if (-not $Proc) { return $false }
    $portOwned = ($PortOwners -and $Proc.ProcessId -and $PortOwners.ContainsKey([int]$Proc.ProcessId))
    $pathOwned = (
        ($Proc.ExecutablePath -and $Proc.ExecutablePath.Equals($exe, [StringComparison]::OrdinalIgnoreCase)) -or
        ($Proc.ExecutablePath -and $Proc.ExecutablePath.StartsWith($binPrefix, [StringComparison]::OrdinalIgnoreCase)) -or
        ($cachePrefix -and $Proc.ExecutablePath -and $Proc.ExecutablePath.StartsWith($cachePrefix, [StringComparison]::OrdinalIgnoreCase))
    )
    $nameOwned = $false
    if ($portOwned -and $Proc.Name) {
        foreach ($knownName in $knownProcessNames) {
            if ($Proc.Name.Equals($knownName, [StringComparison]::OrdinalIgnoreCase)) {
                $nameOwned = $true
                break
            }
        }
    }
    $commandOwned = (
        $Proc.CommandLine -and
        $Proc.CommandLine.IndexOf($modelsDir, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $Proc.CommandLine.IndexOf("lemonade", [StringComparison]::OrdinalIgnoreCase) -ge 0
    )
    return (
        $pathOwned -or $nameOwned -or $commandOwned
    )
}

function Stop-ODSProcessId {
    param([int]$ProcId)
    $owned = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $ProcId) -ErrorAction SilentlyContinue
    $portOwners = Get-ODSPortOwners
    if (-not (Test-ODSLemonadeProcess $owned $portOwners)) {
        throw "Refusing to stop unowned process $ProcId on configured Lemonade port $port"
    }

    function Wait-ODSProcessExit {
        param([int]$TargetPid)
        for ($i = 0; $i -lt 30; $i++) {
            if (-not (Get-Process -Id $TargetPid -ErrorAction SilentlyContinue)) { return $true }
            Start-Sleep -Milliseconds 500
        }
        return $false
    }

    function Invoke-ODSTaskkillViaWmi {
        param([int]$TargetPid)
        try {
            $result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create `
                -Arguments @{ CommandLine = ("cmd.exe /c taskkill.exe /PID {0} /T /F" -f $TargetPid) } `
                -ErrorAction Stop
            return ([int]$result.ReturnValue -eq 0)
        } catch {
            return $false
        }
    }

    Stop-Process -Id $ProcId -Force -ErrorAction SilentlyContinue
    if (Wait-ODSProcessExit -TargetPid $ProcId) { return }
    [void](Invoke-ODSTaskkillViaWmi -TargetPid $ProcId)
    if (Wait-ODSProcessExit -TargetPid $ProcId) { return }
    throw "Could not stop process $ProcId"
}

function Get-ODSLemonadeProcesses {
    $portOwners = Get-ODSPortOwners
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        Test-ODSLemonadeProcess $_ $portOwners
    }
}

function Get-ODSHealthyRouter {
    foreach ($listener in @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
        if ($listener.OwningProcess -le 0) { continue }
        $candidate = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $listener.OwningProcess) -ErrorAction SilentlyContinue
        $portOwners = @{}
        $portOwners[[int]$listener.OwningProcess] = $true
        if (-not (Test-ODSLemonadeProcess $candidate $portOwners)) { continue }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:{0}/api/v1/health" -f $port) -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) { return $candidate }
        } catch {}
    }
    return $null
}

if (Test-Path $pidPath) {
    $rawPid = (Get-Content -LiteralPath $pidPath -Raw).Trim()
    if ($rawPid -match '^\d+$') { Stop-ODSProcessId -ProcId ([int]$rawPid) }
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}
foreach ($listener in @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
    if ($listener.OwningProcess -gt 0) { Stop-ODSProcessId -ProcId ([int]$listener.OwningProcess) }
}
foreach ($child in @(Get-ODSLemonadeProcesses)) {
    Stop-ODSProcessId -ProcId ([int]$child.ProcessId)
}
$remaining = @(Get-ODSLemonadeProcesses)
if ($remaining.Count -gt 0) {
    $ids = ($remaining | ForEach-Object { "$($_.ProcessId):$($_.Name)" }) -join ", "
    throw "Could not stop existing Lemonade processes: $ids"
}

$launchMethod = "direct process"
$directProcess = Start-ODSLemonadeDirectProcess -Contract $launchContract -DiagnosticLogPath $diagnosticLog
$proc = $null
for ($i = 0; $i -lt 75; $i++) {
    Start-Sleep -Seconds 1
    $proc = Get-ODSHealthyRouter
    if ($proc) { break }
}
if (-not $proc) {
    $launchDiagnostics = Get-ODSLemonadeLaunchDiagnostics `
        -ChildProcess $directProcess
    throw "Lemonade $launchMethod started but no healthy owned router was found. $(Format-ODSLemonadeLaunchDiagnostics -Diagnostics $launchDiagnostics)"
}
if ($launchContract.RequiresRuntimeConfiguration) {
    try {
        $null = Set-ODSLemonadeModernRuntimeConfig `
            -Port $port -ModelsDir $modelsDir `
            -AdminApiKey $adminApiKey -ContextSize $contextSize
    } catch {
        $configDiagnostics = Get-ODSLemonadeLaunchDiagnostics `
            -ChildProcess $directProcess
        throw "Lemonade 10.7+ runtime configuration failed: $_. $(Format-ODSLemonadeLaunchDiagnostics -Diagnostics $configDiagnostics)"
    }
}
New-Item -ItemType Directory -Path (Split-Path -Parent $pidPath) -Force | Out-Null
Set-Content -LiteralPath $pidPath -Value $proc.ProcessId
'''
    log_dir = INSTALL_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_stamp = time.strftime("%Y%m%d-%H%M%S")
    wrapper_stdout = log_dir / f"lemonade-restart-{log_stamp}.stdout.log"
    wrapper_stderr = log_dir / f"lemonade-restart-{log_stamp}.stderr.log"

    def summarize_powershell_output(result=None) -> str:
        parts = []
        for path in (wrapper_stderr, wrapper_stdout):
            try:
                text = path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                text = ""
            if text:
                parts.append(text)
        if result is not None:
            parts.extend(
                part.strip()
                for part in (getattr(result, "stderr", ""), getattr(result, "stdout", ""))
                if part and part.strip()
            )
        output = "\n".join(parts).strip()
        output = re.sub(
            r"(?i)(Authorization\s*[:=]\s*Bearer\s+|Bearer\s+)[^\s'\";]+",
            r"\1[redacted]",
            output,
        )
        output = re.sub(
            r"(?i)((?:LEMONADE_ADMIN_API_KEY|LITELLM_LEMONADE_API_KEY|api[-_]?key)\s*[=:]\s*)[^\s'\";]+",
            r"\1[redacted]",
            output,
        )
        return output[-1200:] if output else "no PowerShell output captured"

    try:
        with wrapper_stdout.open("w", encoding="utf-8") as stdout_file, \
                wrapper_stderr.open("w", encoding="utf-8") as stderr_file:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
                timeout=120,
                env=ps_env,
            )
    except subprocess.TimeoutExpired as exc:
        details = summarize_powershell_output()
        raise RuntimeError(
            f"Windows Lemonade restart timed out after {exc.timeout} seconds: {details}"
        ) from exc
    if result.returncode != 0:
        details = summarize_powershell_output(result)
        logger.error(
            "Windows Lemonade restart failed with exit code %s: %s",
            result.returncode,
            details,
        )
        raise RuntimeError(
            "Windows Lemonade restart failed with exit code "
            f"{result.returncode}: {details}"
        )
    logger.info("Windows Lemonade direct process started")


def _render_runtime_config(
    install_dir: Path,
    surface: str,
    *,
    model: str = "",
    gguf_file: str,
    lemonade_model_id: str,
    lemonade_api_key: str,
    lemonade_api_base: str,
    llm_base_url: str = "",
    ods_mode: str,
    gpu_backend: str,
    context_length: int | None = None,
    switchboard_mode: str = "observe",
) -> bool:
    renderer = install_dir / "scripts" / "render-runtime-configs.py"
    if not renderer.exists():
        return False
    cmd = [
        sys.executable,
        str(renderer),
        "--surface",
        surface,
        "--switchboard-mode",
        switchboard_mode,
        "--ods-mode",
        ods_mode,
        "--gpu-backend",
        gpu_backend,
        "--model",
        model or _local_model_name_from_gguf(gguf_file),
        "--gguf-file",
        gguf_file,
        "--lemonade-model-id",
        lemonade_model_id,
        "--lemonade-api-base",
        lemonade_api_base,
        "--llm-base-url",
        llm_base_url or "http://llama-server:8080/v1",
        "--litellm-key",
        lemonade_api_key,
        "--output-root",
        str(install_dir),
        "--write",
    ]
    if context_length is not None:
        cmd.extend(["--context-length", str(context_length)])
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


def _normal_switchboard_mode(env: dict) -> str:
    value = str(env.get("ODS_MODEL_SWITCHBOARD") or "observe").strip().lower()
    return value if value in {"legacy", "observe", "enabled"} else "observe"


def _runtime_lemonade_api_base(env: dict) -> str:
    base = "http://llama-server:8080/api/v1"
    if str(env.get("AMD_INFERENCE_LOCATION") or "").lower() == "host":
        lemonade_port = env.get("AMD_INFERENCE_PORT", "8080") or "8080"
        base = f"http://host.docker.internal:{lemonade_port}/api/v1"
    return base


def _runtime_llama_api_base(env: dict) -> str:
    if _is_windows_host_llama_server(env):
        port = env.get("AMD_INFERENCE_PORT") or env.get("OLLAMA_PORT") or "8080"
        return f"http://host.docker.internal:{port}/v1"
    configured = str(env.get("LLM_API_URL") or "").strip()
    if configured and "litellm" not in configured.lower():
        return configured
    return "http://llama-server:8080/v1"


def _render_model_router_runtime_configs(
    install_dir: Path,
    env: dict,
    *,
    model: str,
    gguf_file: str,
    lemonade_model_id: str,
    context_length: int,
) -> None:
    """Render router/LiteLLM switchboard inputs before dependent restarts."""
    switchboard_mode = _normal_switchboard_mode(env)
    enabled = switchboard_mode == "enabled"
    api_key = (
        env.get("LITELLM_LEMONADE_API_KEY")
        or env.get("LITELLM_KEY")
        or env.get("LITELLM_MASTER_KEY")
        or "sk-lemonade"
    )
    common = {
        "model": model,
        "gguf_file": gguf_file,
        "lemonade_model_id": lemonade_model_id,
        "lemonade_api_key": api_key,
        "lemonade_api_base": _runtime_lemonade_api_base(env),
        "llm_base_url": _runtime_llama_api_base(env),
        "ods_mode": env.get("ODS_MODE", "local"),
        "gpu_backend": env.get("GPU_BACKEND", "nvidia"),
        "context_length": int(context_length),
        "switchboard_mode": switchboard_mode,
    }
    for surface in ("model-router-endpoints", "litellm-switchboard"):
        if _render_runtime_config(install_dir, surface, **common):
            continue
        message = f"Failed to render required {surface} config"
        if enabled:
            raise RuntimeError(message)
        logger.warning("%s; switchboard mode is %s", message, switchboard_mode)


def _write_lemonade_config(
    install_dir: Path,
    gguf_file: str,
    lemonade_model_id: str = "",
):
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
    lemonade_model_id = (
        str(lemonade_model_id or "").strip()
        or str(env.get("LEMONADE_MODEL") or "").strip()
        or f"extra.{gguf_file}"
    )
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
        lemonade_model_id=lemonade_model_id,
        lemonade_api_key=lemonade_api_key,
        lemonade_api_base=lemonade_api_base,
        ods_mode=ods_mode,
        gpu_backend=gpu_backend,
    ):
        logger.info(
            "Wrote lemonade.yaml via runtime renderer for model: %s",
            lemonade_model_id,
        )
        return

    content = (
        "model_list:\n"
        "  - model_name: \"*\"\n"
        "    litellm_params:\n"
        f"      model: openai/{lemonade_model_id}\n"
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
    _atomic_write_text(config_path, content)
    logger.info("Wrote lemonade.yaml for model: %s", lemonade_model_id)


def _write_windows_native_litellm_config(install_dir: Path, gguf_file: str, env: dict):
    """Regenerate LiteLLM local.yaml for native Windows llama-server."""
    config_path = install_dir / "config" / "litellm" / "local.yaml"
    port = env.get("AMD_INFERENCE_PORT") or env.get("OLLAMA_PORT") or "8080"
    api_base = f"http://host.docker.internal:{port}/v1"
    content = (
        "model_list:\n"
        "  - model_name: default\n"
        "    litellm_params:\n"
        f"      model: openai/{gguf_file}\n"
        f"      api_base: {api_base}\n"
        "      api_key: not-needed\n"
        "      extra_body:\n"
        "        chat_template_kwargs:\n"
        "          enable_thinking: false\n"
        "\n"
        "  - model_name: \"*\"\n"
        "    litellm_params:\n"
        "      model: openai/*\n"
        f"      api_base: {api_base}\n"
        "      api_key: not-needed\n"
        "      extra_body:\n"
        "        chat_template_kwargs:\n"
        "          enable_thinking: false\n"
        "\n"
        "general_settings:\n"
        "  master_key: os.environ/LITELLM_MASTER_KEY\n"
        "\n"
        "litellm_settings:\n"
        "  drop_params: true\n"
        "  set_verbose: false\n"
        "  request_timeout: 900\n"
        "  stream_timeout: 900\n"
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(config_path, content)
    logger.info("Wrote native Windows LiteLLM local.yaml for model: %s", gguf_file)


def _patch_hermes_config_text(
    text: str,
    model_name: str,
    base_url: str | None = None,
    context_length: int | None = None,
) -> tuple[str, bool]:
    """Return Hermes YAML with its routing fields updated line-for-line."""
    lines = text.splitlines()
    in_model_block = False
    model_block_found = False
    model_indent = "  "
    model_fields = set()
    changed = False
    new_lines = []

    def add_missing_model_fields() -> None:
        nonlocal changed
        if "default" not in model_fields:
            new_lines.append(f'{model_indent}default: "{model_name}"')
            changed = True
        if base_url and "base_url" not in model_fields:
            new_lines.append(f'{model_indent}base_url: "{base_url}"')
            changed = True
        if context_length and "context_length" not in model_fields:
            new_lines.append(f"{model_indent}context_length: {int(context_length)}")
            changed = True

    for line in lines:
        if re.match(r"^model:\s*(?:#.*)?$", line):
            in_model_block = True
            model_block_found = True
            model_fields = set()
            new_lines.append(line)
            continue
        if in_model_block and line and not line.startswith((" ", "\t", "#")):
            add_missing_model_fields()
            in_model_block = False
        if in_model_block and re.match(r"^\s+default:\s*", line):
            model_fields.add("default")
            model_indent = line[:len(line) - len(line.lstrip())]
            indent = line[:len(line) - len(line.lstrip())]
            new_line = f'{indent}default: "{model_name}"'
            new_lines.append(new_line)
            changed = changed or new_line != line
            continue
        if base_url and in_model_block and re.match(r"^\s+base_url:\s*", line):
            model_fields.add("base_url")
            model_indent = line[:len(line) - len(line.lstrip())]
            indent = line[:len(line) - len(line.lstrip())]
            new_line = f'{indent}base_url: "{base_url}"'
            new_lines.append(new_line)
            changed = changed or new_line != line
            continue
        if context_length and in_model_block and re.match(r"^\s+context_length:\s*", line):
            model_fields.add("context_length")
            model_indent = line[:len(line) - len(line.lstrip())]
            indent = line[:len(line) - len(line.lstrip())]
            new_line = f"{indent}context_length: {int(context_length)}"
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

    if in_model_block:
        add_missing_model_fields()
    elif not model_block_found:
        if new_lines and new_lines[-1]:
            new_lines.append("")
        new_lines.extend([
            "model:",
            f'{model_indent}default: "{model_name}"',
        ])
        if base_url:
            new_lines.append(f'{model_indent}base_url: "{base_url}"')
        if context_length:
            new_lines.append(f"{model_indent}context_length: {int(context_length)}")
        changed = True

    return "\n".join(new_lines) + "\n", changed


def _patch_hermes_model_config(
    path: Path,
    model_name: str,
    base_url: str | None = None,
    context_length: int | None = None,
) -> bool:
    """Patch a host-writable Hermes config file."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    except OSError:
        logger.warning("Could not read Hermes config for model patch: %s", path)
        return False
    patched, changed = _patch_hermes_config_text(
        text,
        model_name,
        base_url=base_url,
        context_length=context_length,
    )
    if not changed:
        return False
    try:
        _atomic_write_text(path, patched)
        logger.info("Patched Hermes model.default in %s to %s", path, model_name)
        return True
    except OSError:
        logger.warning("Could not write Hermes config model patch: %s", path)
        return False


def _container_exists(container: str) -> bool:
    try:
        result = subprocess.run(
            ["docker", "inspect", "--type", "container", "--format", "{{.Id}}", container],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Could not inspect optional container {container}: {exc}") from exc
    if result.returncode == 0:
        return bool(result.stdout.strip())
    detail = (result.stderr or result.stdout or "").strip()
    if "no such" in detail.casefold() or "not found" in detail.casefold():
        return False
    raise RuntimeError(f"Could not inspect optional container {container}: {detail[:300]}")


def _container_running(container: str) -> bool:
    try:
        result = subprocess.run(
            [
                "docker", "inspect", "--type", "container", "--format",
                "{{.State.Running}}", container,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip().casefold() == "true"


def _capture_container_state(container: str) -> dict[str, bool]:
    """Capture optional-container existence/running state without ambiguity."""
    if not _container_exists(container):
        return {"exists": False, "running": False}
    try:
        result = subprocess.run(
            [
                "docker", "inspect", "--type", "container", "--format",
                "{{.State.Running}}", container,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Could not capture runtime state for {container}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Could not capture runtime state for {container}: {detail[:300]}")
    value = result.stdout.strip().casefold()
    if value not in {"true", "false"}:
        raise RuntimeError(f"Docker returned an invalid running state for {container}: {value!r}")
    return {"exists": True, "running": value == "true"}


def _wait_for_container_health(container: str, attempts: int = 60) -> None:
    """Wait until a restarted dependent is healthy, failing on terminal states."""
    for attempt in range(attempts):
        try:
            result = subprocess.run(
                [
                    "docker", "inspect", "--type", "container", "--format",
                    "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
                    container,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Could not inspect health for {container}: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"Could not inspect health for {container}: {detail[:300]}")
        status = result.stdout.strip().casefold()
        if status in {"healthy", "none"}:
            if _capture_container_state(container).get("running"):
                return
            raise RuntimeError(f"{container} exited while waiting for health")
        if status == "unhealthy":
            raise RuntimeError(f"{container} became unhealthy after model activation")
        if status != "starting":
            raise RuntimeError(f"Docker returned invalid health state for {container}: {status!r}")
        if attempt + 1 < attempts:
            time.sleep(2)
    raise RuntimeError(f"{container} did not become healthy after model activation")


def _restart_existing_container(
    container: str,
    expected_state: dict[str, bool] | None = None,
) -> bool:
    """Restart a dependent only when it was already running."""
    state = expected_state or _capture_container_state(container)
    if not state["exists"]:
        logger.info("Skipping restart for optional missing container %s", container)
        return False
    if not state["running"]:
        logger.info("Preserving stopped optional container %s", container)
        return False
    current = _capture_container_state(container)
    if not current["exists"] or not current["running"]:
        raise RuntimeError(f"{container} stopped during model activation")
    result = subprocess.run(
        ["docker", "restart", container],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"docker restart {container} failed (exit {result.returncode}): {detail[:300]}"
        )
    return True


def _restore_container_state(
    container: str,
    previous: dict[str, bool],
    *,
    recreate: bool = False,
) -> bool:
    """Restore a captured optional-container running state during rollback."""
    if not previous.get("exists"):
        return False
    current = _capture_container_state(container)
    if previous.get("running"):
        if recreate:
            ok, error = docker_compose_recreate([container.removeprefix("ods-")])
            if not ok:
                raise RuntimeError(f"Could not restore {container}: {error}")
        else:
            result = subprocess.run(
                ["docker", "restart", container],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                raise RuntimeError(f"Could not restore {container}: {detail[:300]}")
        return True
    if current.get("running"):
        result = subprocess.run(
            ["docker", "stop", container],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"Could not restore stopped state for {container}: {detail[:300]}")
    return False


def _opencode_config_paths() -> tuple[Path, ...]:
    """Return current, ODS-compatibility, and legacy global config paths."""
    config_dir = Path.home() / ".config" / "opencode"
    legacy_dir = Path.home() / ".local" / "share" / "opencode"
    return (
        config_dir / "opencode.json",
        config_dir / "config.json",
        config_dir / "opencode.jsonc",
        legacy_dir / "opencode.json",
        legacy_dir / "opencode.jsonc",
    )


def _parse_jsonc(text: str) -> dict:
    """Parse JSONC comments/trailing commas without corrupting string content."""
    output = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        character = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
            output.append(character)
            index += 1
            continue
        if character == "/" and following == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        if character == "/" and following == "*":
            index += 2
            while index + 1 < len(text) and text[index:index + 2] != "*/":
                index += 1
            if index + 1 >= len(text):
                raise ValueError("unterminated block comment")
            index += 2
            continue
        output.append(character)
        index += 1

    without_comments = "".join(output)
    output = []
    index = 0
    in_string = False
    escaped = False
    while index < len(without_comments):
        character = without_comments[index]
        if in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
            output.append(character)
            index += 1
            continue
        if character == ",":
            lookahead = index + 1
            while lookahead < len(without_comments) and without_comments[lookahead].isspace():
                lookahead += 1
            if lookahead < len(without_comments) and without_comments[lookahead] in "}]":
                index += 1
                continue
        output.append(character)
        index += 1

    parsed = json.loads("".join(output))
    if not isinstance(parsed, dict):
        raise ValueError("root value is not an object")
    return parsed


def _merge_config_objects(target: dict, source: dict) -> dict:
    """Deep-merge OpenCode config objects in increasing precedence order."""
    result = json.loads(json.dumps(target))
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_config_objects(result[key], value)
        else:
            result[key] = json.loads(json.dumps(value))
    return result


def _opencode_config_precedence(path: Path) -> tuple[int, int]:
    """Sort OpenCode global configs from legacy/compatibility to current."""
    normalized = path.as_posix().casefold()
    legacy = "/.local/share/opencode/" in normalized
    if legacy:
        generation = 0
    elif path.name.casefold() == "config.json":
        generation = 1
    else:
        generation = 2
    jsonc_priority = 1 if path.suffix.casefold() == ".jsonc" else 0
    return generation, jsonc_priority


def _opencode_installed() -> bool:
    executable = "opencode.exe" if platform.system() == "Windows" else "opencode"
    return bool(
        shutil.which("opencode")
        or (Path.home() / ".opencode" / "bin" / executable).is_file()
    )


def _capture_opencode_config() -> dict | None:
    """Snapshot OpenCode config, using either compatibility file as a source."""
    paths = _opencode_config_paths()
    files: dict[Path, dict] = {}
    parsed_sources: list[tuple[Path, dict]] = []
    parse_errors = []
    for position, path in enumerate(paths):
        snapshot = _snapshot_text_file(path)
        if not snapshot["exists"]:
            files[path] = {
                **snapshot,
                "parsed": None,
                "write": position < 2,
            }
            continue
        text = str(snapshot["text"] or "")
        parsed = None
        try:
            parsed = _parse_jsonc(text) if path.suffix.casefold() == ".jsonc" else json.loads(text)
            if not isinstance(parsed, dict):
                raise ValueError("root value is not an object")
            parsed_sources.append((path, parsed))
        except (json.JSONDecodeError, ValueError) as exc:
            parse_errors.append(f"{path}: {exc}")
        files[path] = {
            **snapshot,
            "parsed": parsed,
            "write": True,
        }

    if parse_errors:
        raise RuntimeError(
            "OpenCode config is malformed and cannot be updated safely: "
            + "; ".join(parse_errors)
        )
    if not any(item["exists"] for item in files.values()) and not _opencode_installed():
        return None
    source: dict = {}
    for _path, parsed in sorted(
        parsed_sources,
        key=lambda item: _opencode_config_precedence(item[0]),
    ):
        source = _merge_config_objects(source, parsed)
    return {"files": files, "source": source}


def _atomic_write_json(path: Path, value: dict, mode: int = 0o600) -> None:
    """Atomically replace a JSON file with explicit owner-only permissions."""
    _atomic_write_text(path, json.dumps(value, indent=2) + "\n", mode)


def _opencode_route(env: dict) -> tuple[str, str]:
    """Return the host-visible OpenAI-compatible endpoint and API key."""
    if _normal_switchboard_mode(env) == "enabled":
        port = str(env.get("LITELLM_PORT") or "4000")
        api_key = str(env.get("LITELLM_KEY") or "")
        if not api_key:
            raise RuntimeError("LITELLM_KEY is required to update the OpenCode switchboard route")
        return f"http://127.0.0.1:{port}/v1", api_key

    gpu_backend = str(env.get("GPU_BACKEND") or "nvidia").lower()
    if _is_windows_host_lemonade(env):
        port = str(env.get("AMD_INFERENCE_PORT") or env.get("OLLAMA_PORT") or "8080")
        return f"http://127.0.0.1:{port}/api/v1", "no-key"
    windows_native = _is_windows_host_llama_server(env)
    if gpu_backend == "amd" and not windows_native:
        port = str(env.get("LITELLM_PORT") or "4000")
        api_key = str(env.get("LITELLM_KEY") or "")
        if not api_key:
            raise RuntimeError("LITELLM_KEY is required to update the OpenCode Lemonade route")
        return f"http://127.0.0.1:{port}/v1", api_key

    if gpu_backend == "apple":
        host = _native_llama_health_host(env)
        port = str(env.get("ODS_NATIVE_LLAMA_PORT") or env.get("OLLAMA_PORT") or "8080")
    elif windows_native:
        host = "127.0.0.1"
        port = str(env.get("AMD_INFERENCE_PORT") or env.get("OLLAMA_PORT") or "8080")
    else:
        host = "127.0.0.1"
        port = str(env.get("OLLAMA_PORT") or "8080")
    return f"http://{host}:{port}/v1", "no-key"


def _opencode_model_route(env: dict, model_id: str) -> tuple[str, str, str]:
    provider_id = "llama-server"
    if _normal_switchboard_mode(env) == "enabled":
        return provider_id, "ods/current", "ods/current"
    return provider_id, model_id, model_id


def _opencode_config_matches(
    config: object,
    provider_id: str,
    model_id: str,
    base_url: str,
    api_key: str,
    context_length: int,
) -> bool:
    if not isinstance(config, dict):
        return False
    provider = config.get("provider")
    llama_provider = provider.get(provider_id) if isinstance(provider, dict) else None
    options = llama_provider.get("options") if isinstance(llama_provider, dict) else None
    models = llama_provider.get("models") if isinstance(llama_provider, dict) else None
    model = models.get(model_id) if isinstance(models, dict) else None
    limit = model.get("limit") if isinstance(model, dict) else None
    model_ref = f"{provider_id}/{model_id}"
    return bool(
        config.get("model") == model_ref
        and config.get("small_model") == model_ref
        and isinstance(options, dict)
        and options.get("baseURL") == base_url
        and options.get("apiKey") == api_key
        and isinstance(limit, dict)
        and limit.get("context") == context_length
    )


def _update_opencode_config(
    env: dict,
    snapshot: dict,
    model_id: str,
    context_length: int,
    display_name: str | None = None,
) -> None:
    """Update both OpenCode compatibility files and verify persisted routing."""
    base_url, api_key = _opencode_route(env)
    provider_id, route_model_id, route_display_name = _opencode_model_route(env, model_id)
    display_name = route_display_name if route_model_id != model_id else (display_name or model_id)
    model_ref = f"{provider_id}/{route_model_id}"

    for path, previous in snapshot["files"].items():
        if not previous.get("write"):
            continue
        source = previous.get("parsed") or snapshot["source"]
        config = json.loads(json.dumps(source))
        previous_model_ref = config.get("model")
        config["model"] = model_ref
        config["small_model"] = model_ref
        config.setdefault("$schema", "https://opencode.ai/config.json")

        providers = config.setdefault("provider", {})
        if not isinstance(providers, dict):
            providers = {}
            config["provider"] = providers
        provider = providers.setdefault(provider_id, {})
        if not isinstance(provider, dict):
            provider = {}
            providers[provider_id] = provider
        provider["npm"] = "@ai-sdk/openai-compatible"
        provider["name"] = (
            "ODS switchboard" if route_model_id == "ods/current" else "llama-server (local)"
        )
        options = provider.setdefault("options", {})
        if not isinstance(options, dict):
            options = {}
            provider["options"] = options
        options["baseURL"] = base_url
        options["apiKey"] = api_key
        models = provider.setdefault("models", {})
        if not isinstance(models, dict):
            models = {}
            provider["models"] = models
        if (
            isinstance(previous_model_ref, str)
            and previous_model_ref.startswith(f"{provider_id}/")
        ):
            previous_model_id = previous_model_ref.split("/", 1)[1]
            if previous_model_id != route_model_id:
                models.pop(previous_model_id, None)
        model = models.setdefault(route_model_id, {})
        if not isinstance(model, dict):
            model = {}
            models[route_model_id] = model
        model["name"] = display_name
        limit = model.setdefault("limit", {})
        if not isinstance(limit, dict):
            limit = {}
            model["limit"] = limit
        limit["context"] = context_length
        limit["output"] = min(32768, context_length)

        _atomic_write_json(path, config, 0o600)
        try:
            persisted = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not verify OpenCode config {path}: {exc}") from exc
        if not _opencode_config_matches(
            persisted, provider_id, route_model_id, base_url, api_key, context_length
        ):
            raise RuntimeError(f"OpenCode persisted model route is stale in {path}")


def _restore_opencode_config(snapshot: dict) -> None:
    """Restore exact OpenCode files after a failed downstream activation."""
    for path, previous in snapshot["files"].items():
        if previous.get("write") or previous.get("exists"):
            _restore_text_file(path, previous)


def _opencode_port() -> int:
    raw = str(load_env(INSTALL_DIR / ".env").get("OPENCODE_PORT") or "3003")
    try:
        port = int(raw)
    except ValueError:
        port = 3003
    return port if 1 <= port <= 65535 else 3003


def _opencode_user_service_env() -> dict[str, str]:
    getuid = getattr(os, "getuid", None)
    if not callable(getuid):
        raise RuntimeError("The current platform does not expose a user id for OpenCode")
    uid = getuid()
    user_env = os.environ.copy()
    user_env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    user_env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
    return user_env


def _run_windows_opencode_control(action: str) -> bool:
    """Inspect or restart only the ODS-owned Windows OpenCode web process."""
    executable = Path.home() / ".opencode" / "bin" / "opencode.exe"
    port = _opencode_port()
    ps_env = os.environ.copy()
    ps_env.update({
        "ODS_OPENCODE_ACTION": action,
        "ODS_OPENCODE_EXE": str(executable),
        "ODS_OPENCODE_PORT": str(port),
    })
    script = r'''
$ErrorActionPreference = "Stop"
$action = $env:ODS_OPENCODE_ACTION
$exe = $env:ODS_OPENCODE_EXE
$port = [int]$env:ODS_OPENCODE_PORT

function Get-ODSOpenCodeProcesses {
    @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.ExecutablePath -and
        $_.ExecutablePath.Equals($exe, [StringComparison]::OrdinalIgnoreCase) -and
        $_.CommandLine -match '(?i)\b(web|serve)\b' -and
        $_.CommandLine -match ('(?i)--port\s+' + [regex]::Escape([string]$port))
    })
}

$owned = @(Get-ODSOpenCodeProcesses)
if ($action -eq 'inspect') {
    if ($owned.Count -gt 0) { 'true' } else { 'false' }
    exit 0
}
if ($action -ne 'restart') { throw "Unsupported OpenCode action: $action" }
if ($owned.Count -eq 0) { 'false'; exit 0 }
foreach ($process in $owned) {
    Stop-Process -Id ([int]$process.ProcessId) -Force -ErrorAction Stop
}
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    if (@(Get-ODSOpenCodeProcesses).Count -eq 0) { break }
    Start-Sleep -Milliseconds 500
}
if (@(Get-ODSOpenCodeProcesses).Count -ne 0) { throw 'Could not stop ODS OpenCode' }
Start-Process -FilePath $exe `
    -ArgumentList @('web', '--port', [string]$port, '--hostname', '127.0.0.1') `
    -WindowStyle Hidden | Out-Null
'true'
'''
    command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=90,
            env=ps_env,
        )
    except FileNotFoundError:
        command[0] = "pwsh.exe"
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=90,
            env=ps_env,
        )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Could not {action} managed Windows OpenCode: {detail[:500]}")
    value = result.stdout.strip().splitlines()[-1].casefold() if result.stdout.strip() else "false"
    if value not in {"true", "false"}:
        raise RuntimeError(f"Windows OpenCode control returned invalid state: {value!r}")
    return value == "true"


def _capture_managed_opencode_state() -> dict:
    """Capture whether the ODS-managed OpenCode web process is active."""
    system = platform.system()
    if system == "Darwin":
        getuid = getattr(os, "getuid", None)
        if not callable(getuid):
            return {"system": system, "active": False}
        target = f"gui/{getuid()}/com.ods.opencode-web"
        status = subprocess.run(
            ["launchctl", "print", target],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if status.returncode != 0:
            detail = (status.stderr or status.stdout or "").strip().casefold()
            if "could not find service" in detail or "not found" in detail:
                return {"system": system, "active": False, "target": target}
            raise RuntimeError(f"Could not inspect managed OpenCode: {detail[:300]}")
        return {"system": system, "active": True, "target": target}
    elif system == "Linux":
        user_env = _opencode_user_service_env()
        status = subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", "opencode-web.service"],
            capture_output=True,
            text=True,
            timeout=15,
            env=user_env,
        )
        if status.returncode == 0:
            return {"system": system, "active": True, "env": user_env}
        if status.returncode in {3, 4}:
            return {"system": system, "active": False, "env": user_env}
        detail = (status.stderr or status.stdout or "").strip()
        raise RuntimeError(f"Could not inspect managed OpenCode: {detail[:300]}")
    elif system == "Windows":
        return {"system": system, "active": _run_windows_opencode_control("inspect")}
    return {"system": system, "active": False}


def _wait_for_opencode_health(attempts: int = 30) -> None:
    url = f"http://127.0.0.1:{_opencode_port()}/"
    for attempt in range(attempts):
        try:
            request = urllib_request.Request(url, method="GET")
            with urllib_request.urlopen(request, timeout=3) as response:
                if 200 <= response.status < 500:
                    return
        except (OSError, urllib_error.URLError):
            pass
        if attempt + 1 < attempts:
            time.sleep(1)
    raise RuntimeError(f"Managed OpenCode did not become healthy at {url}")


def _restart_managed_opencode(state: dict | None = None) -> bool:
    """Restart and prove the ODS-managed OpenCode Web UI when active."""
    state = state or _capture_managed_opencode_state()
    if not state.get("active"):
        return False
    system = str(state.get("system") or platform.system())
    if system == "Darwin":
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", str(state["target"])],
            capture_output=True,
            text=True,
            timeout=30,
        )
    elif system == "Linux":
        result = subprocess.run(
            ["systemctl", "--user", "restart", "opencode-web.service"],
            capture_output=True,
            text=True,
            timeout=30,
            env=state.get("env") or _opencode_user_service_env(),
        )
    elif system == "Windows":
        if not _run_windows_opencode_control("restart"):
            raise RuntimeError("Managed Windows OpenCode disappeared before restart")
        _wait_for_opencode_health()
        return True
    else:
        return False

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Could not restart managed OpenCode: {detail[:300]}")
    _wait_for_opencode_health()
    return True


def _perplexica_config_url(env: dict) -> str:
    """Return the Perplexica config endpoint reachable from this process."""
    if os.environ.get("ODS_HOST_INSTALL_DIR"):
        return "http://ods-perplexica:3000/api/config"
    port = str(env.get("PERPLEXICA_PORT") or "3004").strip()
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        port = "3004"
    return f"http://127.0.0.1:{port}/api/config"


def _perplexica_http_json(url: str, payload: dict | None = None) -> dict:
    """Read or update Perplexica's config API using only the stdlib."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib_request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    with urllib_request.urlopen(request, timeout=5) as response:
        body = response.read().decode("utf-8")
    if not body.strip():
        return {}
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise RuntimeError("Perplexica config API returned a non-object response")
    return parsed


def _capture_perplexica_config(
    env: dict,
    state: dict[str, bool] | None = None,
) -> dict | None:
    """Snapshot mutable Perplexica routing state when the service is running."""
    state = state or _capture_container_state("ods-perplexica")
    if not state["exists"]:
        return None
    if not state["running"]:
        logger.info(
            "Perplexica exists but is stopped; its next compose activation will "
            "reconcile the model from the updated .env"
        )
        return None
    url = _perplexica_config_url(env)
    payload = _perplexica_http_json(url)
    values = payload.get("values")
    if not isinstance(values, dict):
        raise RuntimeError("Perplexica config response is missing values")
    required = ("modelProviders", "preferences")
    if any(key not in values for key in required):
        raise RuntimeError("Perplexica config is missing model provider preferences")
    return {
        "url": url,
        "values": {key: values[key] for key in required},
    }


def _perplexica_model_route(
    env: dict,
    gguf_file: str,
    lemonade_model_id: str = "",
) -> tuple[str, str, str]:
    """Return model, container-visible base URL, and key for Perplexica."""
    if _normal_switchboard_mode(env) == "enabled":
        api_key = str(env.get("LITELLM_KEY") or env.get("OPENAI_API_KEY") or "no-key")
        return "ods/current", "http://litellm:4000/v1", api_key

    runtime = str(
        env.get("AMD_INFERENCE_RUNTIME")
        or env.get("LLM_BACKEND")
        or env.get("ODS_MODE")
        or ""
    ).strip().lower()
    lemonade = runtime == "lemonade" or _is_windows_host_lemonade(env)
    model = str(
        lemonade_model_id
        or (env.get("LEMONADE_MODEL") if lemonade else "")
        or (f"extra.{gguf_file}" if lemonade else gguf_file)
    ).strip()
    if not model:
        raise RuntimeError("Perplexica model route has an empty model ID")

    if lemonade:
        base_url = str(
            env.get("HERMES_LLM_BASE_URL") or "http://litellm:4000/v1"
        ).strip()
    else:
        base_url = str(env.get("LLM_API_URL") or "http://llama-server:8080").strip()
    if not re.search(r"/(?:api/)?v1/?$", base_url, re.IGNORECASE):
        base_url = f"{base_url.rstrip('/')}/v1"
    api_key = str(env.get("LITELLM_KEY") or env.get("OPENAI_API_KEY") or "no-key")
    return model, base_url, api_key


def _post_perplexica_config(url: str, key: str, value: object) -> None:
    _perplexica_http_json(url, {"key": key, "value": value})


def _perplexica_config_matches(
    values: dict,
    model: str,
    base_url: str,
    api_key: str,
) -> bool:
    providers = values.get("modelProviders")
    preferences = values.get("preferences")
    if not isinstance(providers, list) or not isinstance(preferences, dict):
        return False
    provider = next(
        (entry for entry in providers if isinstance(entry, dict) and entry.get("type") == "openai"),
        None,
    )
    if provider is None:
        return False
    chat_models = provider.get("chatModels")
    config = provider.get("config")
    return bool(
        isinstance(chat_models, list)
        and any(
            isinstance(entry, dict)
            and (entry.get("key") == model or entry.get("name") == model)
            for entry in chat_models
        )
        and isinstance(config, dict)
        and config.get("baseURL") == base_url
        and config.get("apiKey") == api_key
        and preferences.get("defaultChatModel") == model
        and preferences.get("defaultChatProvider") == provider.get("id")
    )


def _update_perplexica_model(
    env: dict,
    snapshot: dict,
    *,
    gguf_file: str,
    lemonade_model_id: str = "",
) -> None:
    """Update and verify Perplexica after a successful runtime model swap."""
    url = str(snapshot["url"])
    values = json.loads(json.dumps(snapshot["values"]))
    providers = values.get("modelProviders")
    preferences = values.get("preferences")
    if not isinstance(providers, list) or not isinstance(preferences, dict):
        raise RuntimeError("Perplexica snapshot is missing routing state")
    provider = next(
        (entry for entry in providers if isinstance(entry, dict) and entry.get("type") == "openai"),
        None,
    )
    if provider is None or not provider.get("id"):
        raise RuntimeError("Perplexica has no configured OpenAI provider")

    model, base_url, api_key = _perplexica_model_route(
        env,
        gguf_file,
        lemonade_model_id,
    )
    provider["chatModels"] = [{"key": model, "name": model}]
    provider_config = provider.get("config")
    if not isinstance(provider_config, dict):
        provider_config = {}
        provider["config"] = provider_config
    provider_config["baseURL"] = base_url
    provider_config["apiKey"] = api_key
    preferences["defaultChatModel"] = model
    preferences["defaultChatProvider"] = provider["id"]

    _post_perplexica_config(url, "modelProviders", providers)
    _post_perplexica_config(url, "preferences", preferences)
    verified = _perplexica_http_json(url).get("values")
    if not isinstance(verified, dict) or not _perplexica_config_matches(
        verified,
        model,
        base_url,
        api_key,
    ):
        raise RuntimeError("Perplexica did not persist the active model route")


def _perplexica_restored_snapshot_matches(verified: dict, expected: dict) -> bool:
    """Return True when Perplexica's restored route still matches the snapshot."""
    if all(verified.get(key) == expected.get(key) for key in ("modelProviders", "preferences")):
        return True

    preferences = expected.get("preferences")
    providers = expected.get("modelProviders")
    verified_preferences = verified.get("preferences")
    verified_providers = verified.get("modelProviders")
    if (
        not isinstance(preferences, dict)
        or not isinstance(providers, list)
        or not isinstance(verified_preferences, dict)
        or not isinstance(verified_providers, list)
    ):
        return False

    expected_model = preferences.get("defaultChatModel")
    expected_provider_id = preferences.get("defaultChatProvider")
    if not expected_model or not expected_provider_id:
        return False
    if (
        verified_preferences.get("defaultChatModel") != expected_model
        or verified_preferences.get("defaultChatProvider") != expected_provider_id
    ):
        return False

    expected_provider = next(
        (
            entry
            for entry in providers
            if isinstance(entry, dict)
            and (
                entry.get("id") == expected_provider_id
                or (entry.get("type") == "openai" and entry.get("id") == expected_provider_id)
            )
        ),
        None,
    )
    verified_provider = next(
        (
            entry
            for entry in verified_providers
            if isinstance(entry, dict) and entry.get("id") == expected_provider_id
        ),
        None,
    )
    if not isinstance(expected_provider, dict) or not isinstance(verified_provider, dict):
        return False

    expected_config = expected_provider.get("config") if isinstance(expected_provider.get("config"), dict) else {}
    verified_config = verified_provider.get("config") if isinstance(verified_provider.get("config"), dict) else {}
    for key in ("baseURL", "apiKey"):
        if expected_config.get(key) != verified_config.get(key):
            return False

    verified_chat_models = verified_provider.get("chatModels")
    if not isinstance(verified_chat_models, list):
        return False
    return any(
        isinstance(entry, dict)
        and (entry.get("key") == expected_model or entry.get("name") == expected_model)
        for entry in verified_chat_models
    )


def _restore_perplexica_config(snapshot: dict) -> None:
    """Restore the Perplexica routing keys captured before model activation."""
    url = str(snapshot["url"])
    values = snapshot.get("values")
    if not isinstance(values, dict):
        raise RuntimeError("Perplexica rollback snapshot is invalid")
    for key in ("modelProviders", "preferences"):
        if key not in values:
            raise RuntimeError(f"Perplexica rollback snapshot is missing {key}")
        _post_perplexica_config(url, key, values[key])
    verified = _perplexica_http_json(url).get("values")
    if not isinstance(verified, dict) or not _perplexica_restored_snapshot_matches(verified, values):
        raise RuntimeError("Perplexica rollback could not be verified")


def _recreate_openclaw_if_present(
    expected_state: dict[str, bool] | None = None,
) -> bool:
    """Recreate OpenClaw only when the optional service is already running."""
    state = expected_state or _capture_container_state("ods-openclaw")
    if not state["exists"]:
        return False
    if not state["running"]:
        logger.info("Preserving stopped optional container ods-openclaw")
        return False
    current = _capture_container_state("ods-openclaw")
    if not current["exists"] or not current["running"]:
        raise RuntimeError("ods-openclaw stopped during model activation")
    ok, error = docker_compose_recreate(["openclaw"])
    if not ok:
        raise RuntimeError(f"Could not recreate OpenClaw after model change: {error}")
    return True


def _verify_litellm_route(env: dict) -> None:
    """Prove the active LiteLLM default route can serve a completion."""
    host = "ods-litellm" if os.environ.get("ODS_HOST_INSTALL_DIR") else "127.0.0.1"
    port = str(env.get("LITELLM_PORT") or "4000")
    api_key = str(env.get("LITELLM_KEY") or env.get("LITELLM_MASTER_KEY") or "")
    for attempt in range(12):
        if _chat_completion_ready(host, port, "default", "/v1", api_key):
            return
        if attempt < 11:
            time.sleep(2)
    raise RuntimeError("LiteLLM did not serve a completion through the active model route")


def _verify_openclaw_model_env(expected_model: str) -> None:
    """Verify recreated OpenClaw received the active persisted model identity."""
    result = subprocess.run(
        [
            "docker", "inspect", "--type", "container", "--format",
            "{{range .Config.Env}}{{println .}}{{end}}", "ods-openclaw",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Could not verify OpenClaw model environment: {detail[:300]}")
    values = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    actual_model = (
        values.get("LEMONADE_MODEL")
        or values.get("GGUF_FILE")
        or values.get("LLM_MODEL")
        or ""
    )
    if not _runtime_model_identity_matches(
        actual_model,
        model_id=expected_model,
        gguf_file=expected_model,
    ):
        raise RuntimeError(
            f"OpenClaw recreated with model {actual_model or '<empty>'}, expected {expected_model}"
        )


def _read_hermes_container_config() -> str:
    if not _container_running("ods-hermes"):
        raise RuntimeError("Hermes live config is host-inaccessible and ods-hermes is not running")
    result = subprocess.run(
        ["docker", "exec", "ods-hermes", "cat", "/opt/data/config.yaml"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Could not read Hermes live config in container: {detail[:300]}")
    return result.stdout


def _write_hermes_container_config(text: str) -> None:
    script = (
        "set -eu; target=/opt/data/config.yaml; tmp=/opt/data/.config.yaml.ods-$$; "
        "owner=$(stat -c '%u:%g' \"$target\" 2>/dev/null || printf '10000:10000'); "
        "mode=$(stat -c '%a' \"$target\" 2>/dev/null || printf '600'); "
        "trap 'rm -f \"$tmp\"' EXIT; cat > \"$tmp\"; chown \"$owner\" \"$tmp\"; "
        "chmod \"$mode\" \"$tmp\"; mv -f \"$tmp\" \"$target\"; trap - EXIT"
    )
    result = subprocess.run(
        ["docker", "exec", "-i", "--user", "0:0", "ods-hermes", "sh", "-c", script],
        input=text,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Could not write Hermes live config in container: {detail[:300]}")


def _capture_hermes_live_config(path: Path) -> dict:
    """Capture persisted Hermes config, falling back through its running container."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {
            "exists": False,
            "text": None,
            "bytes": None,
            "mode": None,
            "source": None,
        }
    except PermissionError as exc:
        logger.info("Inspecting container-owned Hermes config through ods-hermes: %s", exc)
        return {
            "exists": True,
            "text": _read_hermes_container_config(),
            "bytes": None,
            "mode": None,
            "uid": None,
            "gid": None,
            "source": "container",
        }
    except OSError as exc:
        raise RuntimeError(f"Could not inspect Hermes config {path}: {exc}") from exc
    if stat_mod.S_ISLNK(metadata.st_mode):
        raise RuntimeError(f"Refusing to mutate symlinked Hermes config: {path}")
    if not stat_mod.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"Refusing to mutate non-regular Hermes config: {path}")
    try:
        content = path.read_bytes()
        return {
            "exists": True,
            "text": content.decode("utf-8"),
            "bytes": content,
            "mode": stat_mod.S_IMODE(metadata.st_mode),
            "uid": metadata.st_uid,
            "gid": metadata.st_gid,
            "source": "host",
        }
    except PermissionError as exc:
        logger.info("Reading container-owned Hermes config through ods-hermes: %s", exc)
        return {
            "exists": True,
            "text": _read_hermes_container_config(),
            "bytes": None,
            "mode": None,
            "uid": None,
            "gid": None,
            "source": "container",
        }
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"Could not read Hermes config {path}: {exc}") from exc


def _write_hermes_live_config(
    path: Path,
    text: str,
    source: str | None,
    mode: int | None = None,
) -> None:
    if source != "container":
        try:
            _atomic_write_text(path, text, mode)
            return
        except PermissionError as exc:
            logger.info("Writing container-owned Hermes config through ods-hermes: %s", exc)
    _write_hermes_container_config(text)


def _remove_hermes_live_config(path: Path) -> None:
    if path.is_symlink():
        raise RuntimeError(f"Refusing to remove symlinked Hermes config: {path}")
    try:
        path.unlink(missing_ok=True)
        return
    except OSError as exc:
        logger.info("Removing container-owned Hermes config through ods-hermes: %s", exc)
    if not _container_running("ods-hermes"):
        raise RuntimeError(
            "Hermes live config was created during activation and cannot be removed safely"
        )
    result = subprocess.run(
        [
            "docker", "exec", "--user", "0:0", "ods-hermes",
            "rm", "-f", "/opt/data/config.yaml",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Could not remove Hermes live config in container: {detail[:300]}")


def _hermes_config_matches(
    text: str,
    model_name: str,
    base_url: str | None,
    context_length: int,
) -> bool:
    values = {}
    in_model_block = False
    for line in text.splitlines():
        if re.match(r"^model:\s*(?:#.*)?$", line):
            in_model_block = True
            continue
        if in_model_block and line and not line.startswith((" ", "\t", "#")):
            break
        if not in_model_block:
            continue
        match = re.match(r"^\s+(default|base_url|context_length):\s*(.*?)\s*$", line)
        if not match:
            continue
        value = match.group(2).split(" #", 1)[0].strip().strip("'\"")
        values[match.group(1)] = value

    if values.get("default") != str(model_name):
        return False
    if base_url is not None and values.get("base_url") != str(base_url):
        return False
    try:
        return int(values.get("context_length", "")) == int(context_length)
    except (TypeError, ValueError):
        return False


def _verify_running_hermes_route(
    model_name: str,
    base_url: str | None,
    context_length: int,
) -> None:
    text = _read_hermes_container_config()
    if not _hermes_config_matches(text, model_name, base_url, context_length):
        raise RuntimeError("Hermes restarted without the requested persisted model route")


_HERMES_DASHBOARD_TOKEN_PROBE = r"""
import re
import sys
import urllib.request

try:
    with urllib.request.urlopen("http://ods-hermes:9119", timeout=3) as response:
        status = getattr(response, "status", 200)
        body = response.read(8192).decode("utf-8", "replace")
except Exception as exc:
    print(str(exc), file=sys.stderr)
    sys.exit(1)

if status >= 400:
    print(f"Hermes dashboard returned HTTP {status}", file=sys.stderr)
    sys.exit(2)

if not re.search(r'window\.__HERMES_SESSION_TOKEN__\s*=\s*"[^"]+"', body):
    print("Hermes dashboard token was not found", file=sys.stderr)
    sys.exit(3)
"""


def _verify_hermes_dashboard_ready(
    timeout_seconds: int = 90,
    interval_seconds: int = 2,
) -> None:
    """Prove dashboard-api can reach Hermes's browser dashboard/token."""
    interval_seconds = max(1, int(interval_seconds or 1))
    attempts = max(1, int(timeout_seconds / interval_seconds))
    last_detail = ""
    for attempt in range(attempts):
        try:
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    "ods-dashboard-api",
                    "python",
                    "-c",
                    _HERMES_DASHBOARD_TOKEN_PROBE,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_detail = str(exc)
        else:
            if result.returncode == 0:
                return
            last_detail = (result.stderr or result.stdout or "").strip()
        if attempt < attempts - 1:
            time.sleep(interval_seconds)
    detail = f": {last_detail[:300]}" if last_detail else ""
    raise RuntimeError(f"Hermes dashboard did not become reachable after restart{detail}")


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


def _stop_macos_native_llama_server(pid_file: Path) -> None:
    """Stop only the PID-file-owned native llama-server process."""
    if not pid_file.exists():
        return
    try:
        old_pid = int(pid_file.read_text(encoding="utf-8").strip())
        if old_pid <= 1:
            raise OSError("invalid llama-server PID")
        try:
            ps_result = subprocess.run(
                ["ps", "-p", str(old_pid), "-o", "comm="],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if ps_result.returncode != 0 or "llama" not in ps_result.stdout.lower():
                raise OSError("PID is not llama-server")
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise OSError("stale llama-server PID") from exc

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
    finally:
        pid_file.unlink(missing_ok=True)


def _restart_macos_native_llama_server(
    env_path: Path,
    llama_bin: Path,
    llama_log: Path,
    pid_file: Path,
) -> None:
    """Restart native inference with bridge lifecycle ordering preserved."""
    # Validate the installed shared manager before taking down a healthy
    # listener. The actual bridge mutation must happen after shutdown so a
    # direct-bound listener cannot collide with a newly recreated bridge.
    _require_macos_bridge_manager(env_path)
    _stop_macos_native_llama_server(pid_file)
    _configure_macos_llm_bridge(env_path)
    _launch_native_llama_server(env_path, llama_bin, llama_log, pid_file)


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
    _disable_conflicting_macos_bridge(env, bind_addr, _MACOS_LLM_BRIDGE_LABEL)
    port = (
        env.get("ODS_NATIVE_LLAMA_PORT")
        or env.get("AMD_INFERENCE_PORT")
        or env.get("OLLAMA_PORT")
        or "8080"
    )
    args = [
        str(llama_bin),
        "--host", bind_addr, "--port", str(port),
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
    llama_log.parent.mkdir(parents=True, exist_ok=True)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    popen_kwargs = {}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if platform.system().lower() == "windows" and creationflags:
        popen_kwargs["creationflags"] = creationflags
    with open(llama_log, "a") as log_f:
        proc = subprocess.Popen(
            args,
            stdout=log_f, stderr=log_f,
            cwd=str(INSTALL_DIR),
            **popen_kwargs,
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


def _as_argv(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value:
        return [value]
    return []


def _refresh_llama_cmd(command: list[str], env: dict) -> list[str]:
    replacements = {
        "--model": f"/models/{env.get('GGUF_FILE', '')}",
        "--ctx-size": str(env.get("CTX_SIZE") or env.get("MAX_CONTEXT") or "32768"),
        "--parallel": str(env.get("LLAMA_PARALLEL") or "1"),
    }
    refreshed = []
    index = 0
    while index < len(command):
        argument = command[index]
        matched = False
        for flag, replacement in replacements.items():
            if argument == flag and index + 1 < len(command):
                refreshed.extend([flag, replacement])
                index += 2
                matched = True
                break
            if argument.startswith(f"{flag}="):
                refreshed.append(f"{flag}={replacement}")
                index += 1
                matched = True
                break
        if not matched:
            refreshed.append(argument)
            index += 1
    return refreshed


def _device_request_cli_value(request: dict) -> str | None:
    capabilities = request.get("Capabilities") or []
    flat_capabilities = [
        str(capability)
        for capability_set in capabilities
        if isinstance(capability_set, list)
        for capability in capability_set
    ]
    if request.get("Driver") not in {None, "", "nvidia"} and "gpu" not in flat_capabilities:
        return None

    device_ids = [str(device_id) for device_id in (request.get("DeviceIDs") or [])]
    count = request.get("Count")
    options = request.get("Options") or {}
    only_default_capability = not flat_capabilities or flat_capabilities == ["gpu"]
    if not device_ids and count == -1 and not options and only_default_capability:
        return "all"
    if not device_ids and isinstance(count, int) and count >= 0 and not options and only_default_capability:
        return str(count)

    fields = []
    driver = str(request.get("Driver") or "").strip()
    if driver:
        fields.append(f"driver={driver}")
    if device_ids:
        fields.append(f"device={','.join(device_ids)}")
    elif isinstance(count, int):
        fields.append(f"count={'all' if count == -1 else count}")
    if flat_capabilities:
        fields.append(f"capabilities={','.join(flat_capabilities)}")
    for key, value in sorted(options.items()):
        fields.append(f"{key}={value}")
    return f'"{",".join(fields)}"' if fields else None


def _append_network_settings(
    argv: list[str],
    network_name: str,
    network: dict,
    container: str,
    hostname: str,
) -> None:
    argv.extend(["--network", network_name])
    aliases = []
    for alias in network.get("Aliases") or []:
        if alias and alias not in {container, hostname} and alias not in aliases:
            aliases.append(alias)
    if "llama-server" not in aliases:
        aliases.append("llama-server")
    for alias in aliases:
        argv.extend(["--network-alias", str(alias)])


def _llama_recreate_argv(
    inspect_config: dict,
    env: dict,
    image: str,
    container: str,
) -> tuple[list[str], list[list[str]]]:
    """Translate runtime-relevant inspect state into docker CLI argv."""
    container_config = inspect_config.get("Config") or {}
    host_config = inspect_config.get("HostConfig") or {}
    run_cmd = ["docker", "run", "-d", "--name", container]

    restart = host_config.get("RestartPolicy") or {}
    restart_name = str(restart.get("Name") or "")
    if restart_name:
        maximum_retry = int(restart.get("MaximumRetryCount") or 0)
        restart_value = (
            f"{restart_name}:{maximum_retry}"
            if restart_name == "on-failure" and maximum_retry > 0
            else restart_name
        )
        run_cmd.extend(["--restart", restart_value])

    networks = (inspect_config.get("NetworkSettings") or {}).get("Networks") or {}
    network_items = list(networks.items())
    hostname = str(container_config.get("Hostname") or "")
    if network_items:
        first_name, first_network = network_items[0]
        _append_network_settings(
            run_cmd,
            first_name,
            first_network or {},
            container,
            hostname,
        )
    else:
        network_mode = str(host_config.get("NetworkMode") or "")
        if network_mode and network_mode not in {"default", "bridge"}:
            run_cmd.extend(["--network", network_mode])

    for container_port, bindings in (host_config.get("PortBindings") or {}).items():
        for binding in bindings or []:
            host_ip = str(binding.get("HostIp") or "")
            host_port = str(binding.get("HostPort") or "")
            if ":" in host_ip and not host_ip.startswith("["):
                host_ip = f"[{host_ip}]"
            published = ":".join(
                part for part in (host_ip, host_port, str(container_port)) if part
            )
            run_cmd.extend(["-p", published])
    for container_port in (container_config.get("ExposedPorts") or {}):
        run_cmd.extend(["--expose", str(container_port)])

    binds = host_config.get("Binds") or []
    if binds:
        for binding in binds:
            run_cmd.extend(["-v", str(binding)])
    else:
        for mount in inspect_config.get("Mounts") or []:
            source = mount.get("Name") if mount.get("Type") == "volume" else mount.get("Source")
            destination = mount.get("Destination")
            if source and destination:
                mode = "ro" if mount.get("RW") is False else "rw"
                run_cmd.extend(["-v", f"{source}:{destination}:{mode}"])
    for destination, options in (host_config.get("Tmpfs") or {}).items():
        value = str(destination)
        if options:
            value += f":{options}"
        run_cmd.extend(["--tmpfs", value])
    for source in host_config.get("VolumesFrom") or []:
        run_cmd.extend(["--volumes-from", str(source)])

    replacement_keys = {
        "LLAMA_PARALLEL", "LLAMA_REASONING", "GGUF_FILE", "LLM_MODEL",
        "CTX_SIZE", "MAX_CONTEXT", "LLAMA_SERVER_IMAGE",
    }
    replacement_env = {
        key: str(value)
        for key, value in env.items()
        if key.startswith("LLAMA_ARG_") or key in replacement_keys
    }
    seen_env_keys = set()
    for entry in container_config.get("Env") or []:
        key = str(entry).split("=", 1)[0]
        if key in replacement_env:
            run_cmd.extend(["-e", f"{key}={replacement_env[key]}"])
            seen_env_keys.add(key)
        elif key.startswith("LLAMA_ARG_") or key in replacement_keys:
            continue
        else:
            run_cmd.extend(["-e", str(entry)])
    for key, value in replacement_env.items():
        if key not in seen_env_keys:
            run_cmd.extend(["-e", f"{key}={value}"])

    scalar_options = (
        ("User", "--user"),
        ("WorkingDir", "--workdir"),
        ("Domainname", "--domainname"),
        ("StopSignal", "--stop-signal"),
    )
    for config_key, flag in scalar_options:
        value = container_config.get(config_key)
        if value:
            run_cmd.extend([flag, str(value)])
    stop_timeout = container_config.get("StopTimeout")
    if isinstance(stop_timeout, int) and stop_timeout > 0:
        run_cmd.extend(["--stop-timeout", str(stop_timeout)])
    if hostname:
        run_cmd.extend(["--hostname", hostname])
    if container_config.get("Tty"):
        run_cmd.append("--tty")
    if container_config.get("OpenStdin"):
        run_cmd.append("--interactive")
    for key, value in (container_config.get("Labels") or {}).items():
        run_cmd.extend(["--label", f"{key}={value}"])
    healthcheck = container_config.get("Healthcheck") or {}
    health_test = _as_argv(healthcheck.get("Test"))
    if health_test == ["NONE"]:
        run_cmd.append("--no-healthcheck")
    elif health_test and health_test[0] in {"CMD", "CMD-SHELL"} and len(health_test) > 1:
        health_command = (
            health_test[1]
            if health_test[0] == "CMD-SHELL"
            else shlex.join(health_test[1:])
        )
        run_cmd.extend(["--health-cmd", health_command])
        for key, flag in (
            ("Interval", "--health-interval"),
            ("Timeout", "--health-timeout"),
            ("StartPeriod", "--health-start-period"),
        ):
            value = healthcheck.get(key)
            if isinstance(value, int) and value > 0:
                run_cmd.extend([flag, f"{value}ns"])
        retries = healthcheck.get("Retries")
        if isinstance(retries, int) and retries > 0:
            run_cmd.extend(["--health-retries", str(retries)])

    for host in host_config.get("ExtraHosts") or []:
        run_cmd.extend(["--add-host", str(host)])
    for link in host_config.get("Links") or []:
        run_cmd.extend(["--link", str(link)])
    for device in host_config.get("Devices") or []:
        source = device.get("PathOnHost")
        destination = device.get("PathInContainer") or source
        permissions = device.get("CgroupPermissions") or "rwm"
        if source and destination:
            run_cmd.extend(["--device", f"{source}:{destination}:{permissions}"])
    for group in host_config.get("GroupAdd") or []:
        run_cmd.extend(["--group-add", str(group)])
    for request in host_config.get("DeviceRequests") or []:
        value = _device_request_cli_value(request)
        if value:
            run_cmd.extend(["--gpus", value])

    for capability in host_config.get("CapAdd") or []:
        run_cmd.extend(["--cap-add", str(capability)])
    for capability in host_config.get("CapDrop") or []:
        run_cmd.extend(["--cap-drop", str(capability)])
    for option in host_config.get("SecurityOpt") or []:
        run_cmd.extend(["--security-opt", str(option)])
    for rule in host_config.get("DeviceCgroupRules") or []:
        run_cmd.extend(["--device-cgroup-rule", str(rule)])
    if host_config.get("Privileged"):
        run_cmd.append("--privileged")
    if host_config.get("ReadonlyRootfs"):
        run_cmd.append("--read-only")
    if host_config.get("Init"):
        run_cmd.append("--init")
    if host_config.get("AutoRemove"):
        run_cmd.append("--rm")

    host_scalar_options = (
        ("Runtime", "--runtime"),
        ("IpcMode", "--ipc"),
        ("PidMode", "--pid"),
        ("UTSMode", "--uts"),
        ("UsernsMode", "--userns"),
        ("CgroupnsMode", "--cgroupns"),
        ("CgroupParent", "--cgroup-parent"),
        ("CpusetCpus", "--cpuset-cpus"),
        ("CpusetMems", "--cpuset-mems"),
    )
    for config_key, flag in host_scalar_options:
        value = host_config.get(config_key)
        if value and value not in {"default", "private"}:
            run_cmd.extend([flag, str(value)])
    numeric_options = (
        ("Memory", "--memory"),
        ("MemoryReservation", "--memory-reservation"),
        ("MemorySwap", "--memory-swap"),
        ("CpuShares", "--cpu-shares"),
        ("PidsLimit", "--pids-limit"),
        ("ShmSize", "--shm-size"),
        ("OomScoreAdj", "--oom-score-adj"),
    )
    for config_key, flag in numeric_options:
        value = host_config.get(config_key)
        if isinstance(value, int) and value != 0:
            run_cmd.extend([flag, str(value)])
    nano_cpus = host_config.get("NanoCpus")
    if isinstance(nano_cpus, int) and nano_cpus > 0:
        run_cmd.extend(["--cpus", f"{nano_cpus / 1_000_000_000:g}"])
    if host_config.get("OomKillDisable"):
        run_cmd.append("--oom-kill-disable")
    for key, value in (host_config.get("Sysctls") or {}).items():
        run_cmd.extend(["--sysctl", f"{key}={value}"])
    for limit in host_config.get("Ulimits") or []:
        name = limit.get("Name")
        soft = limit.get("Soft")
        hard = limit.get("Hard")
        if name and soft is not None and hard is not None:
            run_cmd.extend(["--ulimit", f"{name}={soft}:{hard}"])
    for server in host_config.get("Dns") or []:
        run_cmd.extend(["--dns", str(server)])
    for search in host_config.get("DnsSearch") or []:
        run_cmd.extend(["--dns-search", str(search)])
    for option in host_config.get("DnsOptions") or []:
        run_cmd.extend(["--dns-option", str(option)])

    log_config = host_config.get("LogConfig") or {}
    if log_config.get("Type"):
        run_cmd.extend(["--log-driver", str(log_config["Type"])])
        for key, value in (log_config.get("Config") or {}).items():
            run_cmd.extend(["--log-opt", f"{key}={value}"])

    entrypoint = _as_argv(container_config.get("Entrypoint"))
    if entrypoint:
        run_cmd.extend(["--entrypoint", entrypoint[0]])
    run_cmd.append(image)
    run_cmd.extend(entrypoint[1:])
    run_cmd.extend(_refresh_llama_cmd(_as_argv(container_config.get("Cmd")), env))

    connect_commands = []
    for network_name, network in network_items[1:]:
        connect = ["docker", "network", "connect"]
        for alias in network.get("Aliases") or []:
            if alias and alias not in {container, hostname}:
                connect.extend(["--alias", str(alias)])
        if "llama-server" not in (network.get("Aliases") or []):
            connect.extend(["--alias", "llama-server"])
        connect.extend([network_name, container])
        connect_commands.append(connect)
    return run_cmd, connect_commands


def _recreate_llama_server(env: dict, override_image: str = ""):
    """Transactionally recreate llama-server from its inspected runtime state."""
    container = "ods-llama-server"
    inspect_result = subprocess.run(
        ["docker", "inspect", container],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if inspect_result.returncode != 0:
        raise RuntimeError(
            f"Failed to inspect {container}: {(inspect_result.stderr or '').strip()[-500:]}"
        )
    try:
        inspect_config = json.loads(inspect_result.stdout)[0]
    except (IndexError, json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError(f"Docker returned invalid inspect data for {container}") from exc

    image = override_image or (inspect_config.get("Config") or {}).get("Image")
    if not image:
        raise RuntimeError(f"Docker inspect did not report an image for {container}")
    run_cmd, connect_commands = _llama_recreate_argv(
        inspect_config,
        env,
        str(image),
        container,
    )

    backup = f"{container}-ods-rollback-{os.getpid()}"

    def _checked(argv: list[str], timeout: int) -> subprocess.CompletedProcess:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"{' '.join(argv[:3])} failed: {detail[-500:]}")
        return result

    def _best_effort(argv: list[str], timeout: int) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("Best-effort Docker recovery command failed (%s): %s", argv, exc)
            return None

    _checked(["docker", "stop", container], 120)
    try:
        _checked(["docker", "rename", container, backup], 30)
    except Exception as rename_exc:
        try:
            _checked(["docker", "start", container], 120)
        except Exception as restart_exc:
            raise RuntimeError(
                f"Could not stage or restart existing llama-server: {restart_exc}"
            ) from rename_exc
        raise
    try:
        logger.info("Recreating llama-server: %s with model %s", image, env.get("GGUF_FILE", ""))
        _checked(run_cmd, 120)
        for command in connect_commands:
            _checked(command, 30)
    except Exception as recreate_exc:
        logger.exception("Replacement llama-server failed; restoring inspected container")
        _best_effort(["docker", "rm", "-f", container], 30)
        try:
            _checked(["docker", "rename", backup, container], 30)
            _checked(["docker", "start", container], 120)
        except Exception as rollback_exc:
            raise RuntimeError(
                f"llama-server recreation failed and rollback also failed: {rollback_exc}"
            ) from recreate_exc
        raise

    cleanup = _best_effort(["docker", "rm", backup], 30)
    if cleanup is None or cleanup.returncode != 0:
        logger.warning(
            "Replacement succeeded but old llama-server cleanup failed: %s",
            (
                (cleanup.stderr or cleanup.stdout or "").strip()[-500:]
                if cleanup is not None
                else "Docker cleanup command did not complete"
            ),
        )
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
        with _model_status_lock:
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
    # Dashboard model discovery can issue bursts larger than HTTPServer's
    # default backlog of 5; keep action requests from being dropped behind polls.
    request_queue_size = 128


def _create_host_agent_server(env: dict, bind_addr: str, port: int):
    """Create the agent server after removing any colliding macOS bridge."""
    _disable_conflicting_macos_bridge(
        env,
        bind_addr,
        _MACOS_HOST_AGENT_BRIDGE_LABEL,
    )
    return ThreadedHTTPServer((bind_addr, port), AgentHandler)


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
    global INSTALL_DIR, DATA_DIR, AGENT_API_KEY, GPU_BACKEND, STARTUP_ODS_MODE
    global TIER, GPU_COUNT, CORE_SERVICE_IDS
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
    if _switchboard_state is not None:
        try:
            _switchboard_state.initialize_if_missing(
                INSTALL_DIR / "data" / "model-state.json", env
            )
        except Exception as exc:
            logger.warning("switchboard state init skipped: %s", exc)
        _schedule_initial_switchboard_verification("startup")
    STARTUP_ODS_MODE = _normalize_ods_mode(env.get("ODS_MODE"))
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

    server = _create_host_agent_server(env, bind_addr, port)
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
    logger.info(
        "Install dir: %s | GPU: %s | Tier: %s | Effective mode: %s",
        INSTALL_DIR,
        GPU_BACKEND,
        TIER,
        STARTUP_ODS_MODE,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
