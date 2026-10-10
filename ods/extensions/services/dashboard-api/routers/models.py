"""Model Library router — browse, benchmark, and manage GGUF models."""

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from typing import Optional
from urllib.parse import quote, urljoin, urlsplit

import httpx
from model_activation_status import model_activation_status
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse, JSONResponse

from env_values import parse_env_value
import hf_gguf_header
import model_preflight
from config import (
    DATA_DIR,
    GPU_BACKEND,
    INSTALL_DIR,
    LLM_BACKEND,
    LOCAL_MODEL_MODES,
    ODS_MODE_EFFECTIVE,
    SERVICES,
    normalize_ods_mode,
    read_live_env_values,
)
from gpu import get_gpu_info
from helpers import (
    get_bootstrap_status,
    get_llama_context_size,
    get_llama_metrics,
    get_loaded_model,
    is_plausible_single_request_tps,
    record_model_performance,
)
from host_agent_client import (
    AgentClientError,
    AgentHTTPError,
    AgentProtocolError,
    AgentUnavailable,
    request_json as request_agent_json,
)
from models import ModelLibraryGpu, ModelLibraryResponse
from pixel_runtime_state import pixel_stream_active
from context_policy import HERMES_MIN_CONTEXT
from performance_oracle import (
    activation_context_plan,
    build_models_payload,
    build_sample_signature,
    current_model_matches,
    find_catalog_model,
    load_model_catalog,
    model_files_dir as model_files_dir,
    planned_model_context,
    read_env_file_value,
    read_env_value,
)
from security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["models"])

_LIBRARY_PATH = Path(INSTALL_DIR) / "config" / "model-library.json"
_MODELS_DIR = Path(DATA_DIR) / "models"


def _installed_model_paths() -> dict[str, Path]:
    from model_stores import scan_model_files
    return scan_model_files(Path(DATA_DIR), container=Path("/.dockerenv").exists(), default_dir=_MODELS_DIR)


def _installed_model_path(filename: str) -> Path | None:
    return next((path for name, path in _installed_model_paths().items() if name.casefold() == filename.casefold()), None)
_ENV_PATH = Path(INSTALL_DIR) / ".env"


def _windows_hosted_runtime() -> bool:
    """The model runs on the Windows host, controlled only through the host agent.

    The WSL Portal drives an ODS-owned llama-server.exe through the WSL
    bridge; the agent's ownership proof decides whether this install may
    change or stop it. ``LEMONADE_HOST_TRANSPORT`` is the transport key's
    one-release legacy name. An unmigrated pre-round-F .env for the owner's
    own Lemonade (``LLM_BACKEND=lemonade`` with ``LEMONADE_EXTERNAL=true``)
    is never controllable from here.
    """
    env = read_live_env_values((
        "ODS_HOST_LLM_TRANSPORT", "LEMONADE_HOST_TRANSPORT", "AMD_INFERENCE_RUNTIME_MODE",
        "LLM_BACKEND", "LEMONADE_EXTERNAL",
    ))
    transport = str(env.get("ODS_HOST_LLM_TRANSPORT") or env.get("LEMONADE_HOST_TRANSPORT") or "")
    unmigrated_external = (
        str(env.get("LLM_BACKEND") or "").strip().casefold() == "lemonade"
        and str(env.get("LEMONADE_EXTERNAL") or "").strip().casefold() in {"1", "true", "yes", "on"}
    )
    return (
        transport.strip().casefold() == "model-router"
        or str(env.get("AMD_INFERENCE_RUNTIME_MODE") or "").strip().casefold() == "windows-portal-llama-server"
        or unmigrated_external
    )


_HF_API_BASE = "https://huggingface.co"
_HF_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_HF_AUTHOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_HF_AVATAR_HOSTS = {"huggingface.co", "cdn-avatars.huggingface.co"}
_HF_SPLIT_GGUF_RE = re.compile(r"^(?P<prefix>.+)-(?P<part>\d{5})-of-(?P<total>\d{5})\.gguf$", re.IGNORECASE)
# MXFP4 is gpt-oss's native format ("gpt-oss-20b-MXFP4.gguf", unsloth's
# "*-MXFP4_MOE.gguf"); without it the import was named "· unknown".
_HF_QUANT_RE = re.compile(
    r"(?:^|[-_.])(?P<quant>(?:IQ\d(?:_[A-Z0-9]+)+|Q\d(?:_[A-Z0-9]+)+|MXFP4(?:_MOE)?|BF16|F16|F32))(?:[-_.]|$)",
    re.IGNORECASE,
)
_HF_SEARCH_CACHE_TTL_SECONDS = 300.0
_HF_SEARCH_CACHE_MAX_ENTRIES = 128
_HF_SEARCH_CACHE: dict[tuple[str, str, int, str], tuple[float, dict[str, Any]]] = {}
_HF_SEARCH_CACHE_LOCK = threading.Lock()
_HF_AVATAR_CACHE_TTL_SECONDS = 3600.0
_HF_AVATAR_CACHE_MAX_ENTRIES = 512
_HF_AVATAR_CACHE: dict[tuple[str, str], tuple[float, str | None]] = {}
_HF_AVATAR_CACHE_LOCK = threading.Lock()
_IMPORTED_MODELS_LOCK = threading.Lock()
_last_recorded_throughput_sample = None
_MODEL_DISCOVERY_TIMEOUT_SECONDS = float(os.environ.get("DASHBOARD_MODEL_DISCOVERY_TIMEOUT", "15.0"))
_MIN_MODEL_CONTEXT = 1024
_MAX_MODEL_CONTEXT = 9007199254740991
_AGENT_MODEL_STATUS_CACHE_TTL_SECONDS = float(
    os.environ.get("DASHBOARD_AGENT_MODEL_STATUS_CACHE_TTL", "0.5")
)
_STALE_TERMINAL_DOWNLOAD_STATUS_SECONDS = float(
    os.environ.get("DASHBOARD_STALE_TERMINAL_DOWNLOAD_STATUS_SECONDS", "1800")
)
_STALE_ACTIVE_BOOTSTRAP_STATUS_SECONDS = float(
    os.environ.get("DASHBOARD_STALE_ACTIVE_BOOTSTRAP_STATUS_SECONDS", "900")
)
_ACTIVE_BOOTSTRAP_STATUSES = {"starting", "downloading", "verifying", "swapping"}
_agent_model_status_cache_lock = threading.Lock()
_agent_model_status_cache_at = 0.0
_agent_model_status_cache_value: Optional[dict] = None
_GPU_VRAM_EXCEPTIONS = (
    ImportError,
    FileNotFoundError,
    OSError,
    KeyError,
    AttributeError,
)


def _model_lifecycle_from_agent_status(status: Optional[dict]) -> Optional[dict[str, Any]]:
    if not isinstance(status, dict):
        return None
    operation = status.get("activeOperation")
    active = bool(status.get("lifecycleActive") or operation)
    if not active or not isinstance(operation, str) or not operation:
        return None
    target = status.get("activeTarget")
    model_id = status.get("activeModelId") or target
    return {
        "active": True,
        "operation": operation,
        "target": target,
        "modelId": model_id,
    }


def _annotate_model_lifecycle(payload: dict[str, Any], lifecycle: Optional[dict[str, Any]]) -> None:
    if not lifecycle:
        return
    payload["modelLifecycle"] = lifecycle
    if lifecycle.get("operation") != "model_activation":
        return
    target = lifecycle.get("modelId") or lifecycle.get("target")
    if not target:
        return
    for model in payload.get("models") or []:
        if isinstance(model, dict) and current_model_matches(model, str(target), str(target)):
            model["modelOperation"] = lifecycle
            return


def _configured_ods_mode() -> str:
    """Return the current persisted mode without treating process env as config."""
    try:
        return normalize_ods_mode(read_env_file_value("ODS_MODE", INSTALL_DIR, raise_on_error=True))
    except PermissionError:
        # The private .env can belong to a different host UID than this API.
        # Ask its owner-side agent for only the persisted mode, never secrets
        # or the process environment. Read freshly before every activation.
        try:
            snapshot = request_agent_json("GET", "/v1/model/config", timeout=5)
        except AgentClientError:
            return "unknown"
        mode = snapshot.get("configuredMode") if isinstance(snapshot, dict) else None
        return mode if isinstance(mode, str) and mode in {"local", "cloud", "hybrid"} else "unknown"
    except (OSError, UnicodeError):
        return "unknown"


def _model_activation_mode_denial(
    effective_mode: str,
    configured_mode: str,
    llm_backend: str = "",
) -> dict[str, str] | None:
    """Describe why this runtime cannot safely perform a local model swap."""
    effective_mode = normalize_ods_mode(effective_mode)
    configured_mode = normalize_ods_mode(configured_mode)
    normalized_backend = str(llm_backend or "").strip().lower()
    if normalized_backend == "external":
        code = "external_llm_managed"
        reason = "external_backend_selected"
        message = (
            "Local model activation is unavailable while ODS uses your own "
            "model server. Change the model in that server, or rerun the "
            "installer with an ODS-managed backend to activate downloaded models."
        )
    elif "unknown" in {effective_mode, configured_mode}:
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
    elif effective_mode not in LOCAL_MODEL_MODES:
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
        "llmBackend": normalized_backend or "unknown",
    }

try:
    import pynvml
except ImportError:
    pynvml = None
else:
    _GPU_VRAM_EXCEPTIONS = _GPU_VRAM_EXCEPTIONS + (pynvml.NVMLError,)


def _local_model_name_from_gguf(gguf_file: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(gguf_file).stem).strip("-._")
    return name or "local-gguf"


def _imported_library_path() -> Path:
    return Path(DATA_DIR) / "model-imports.json"


def _read_model_records(
    path: Path,
    *,
    required: bool,
    strict: bool = False,
) -> list[dict[str, Any]]:
    if not path.exists():
        if required:
            logger.warning("Model library not found: %s", path)
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError) as exc:
        logger.warning("Failed to load model library %s: %s", path, exc)
        if strict:
            raise HTTPException(
                status_code=409,
                detail="The Hugging Face import registry is unreadable; it was not overwritten",
            ) from exc
        return []
    records = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        logger.warning("Model library %s does not contain a models array", path)
        if strict:
            raise HTTPException(
                status_code=409,
                detail="The Hugging Face import registry is malformed; it was not overwritten",
            )
        return []
    if strict and not all(isinstance(record, dict) for record in records):
        raise HTTPException(
            status_code=409,
            detail="The Hugging Face import registry contains invalid records; it was not overwritten",
        )
    return [record for record in records if isinstance(record, dict)]


def _load_library() -> list[dict]:
    """Load the curated catalog plus separately persisted Hub imports."""
    curated = _read_model_records(_LIBRARY_PATH, required=True)
    imported = _read_model_records(_imported_library_path(), required=False)
    seen_ids = {str(model.get("id") or "") for model in curated}
    seen_files = {str(model.get("gguf_file") or "").lower() for model in curated}
    merged = list(curated)
    for model in imported:
        model_id = str(model.get("id") or "")
        filename = str(model.get("gguf_file") or "").lower()
        if (
            model.get("source") != "huggingface"
            or not model_id
            or not filename
            or model_id in seen_ids
            or filename in seen_files
        ):
            continue
        merged.append(model)
        seen_ids.add(model_id)
        seen_files.add(filename)
    return merged


def _write_imported_library(records: list[dict[str, Any]]) -> None:
    """Atomically persist the Hub import allowlist on the shared data volume."""
    target = _imported_library_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise HTTPException(status_code=409, detail="Refusing to replace a symlinked model import registry")
    content = json.dumps(
        {"version": 1, "models": records},
        indent=2,
        sort_keys=True,
    ) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _scan_downloaded_models() -> dict[str, int]:
    """Scan data/models/ for downloaded GGUF files. Returns {filename: size_bytes}."""
    downloaded = {}
    for name, path in _installed_model_paths().items():
        try:
            downloaded[name] = path.stat().st_size
        except OSError:
            continue
    return downloaded


def _is_final_gguf_file(path: Path) -> bool:
    try:
        return path.is_file() and path.name.lower().endswith(".gguf") and path.stat().st_size > 0
    except OSError:
        return False


def _read_active_model() -> Optional[str]:
    """Read the currently active GGUF_FILE from .env."""
    if not _ENV_PATH.exists():
        return None
    try:
        for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith("GGUF_FILE="):
                return parse_env_value(line.split("=", 1)[1])
    except OSError:
        pass
    return None


def _strip_llm_api_suffix(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    for suffix in ("/api/v1", "/v1", "/api"):
        if base.endswith(suffix):
            return base[: -len(suffix)].rstrip("/")
    return base


def _configured_llm_base_url(host: str, port: int) -> str:
    # LiteLLM's LLM_API_URL is an alias gateway, not the physical runtime.
    # Model identity probes must follow the server that serves the model.
    if LLM_BACKEND == "external":
        value = read_env_value("EXTERNAL_LLM_CONTAINER_URL", INSTALL_DIR)
        if value:
            return _strip_llm_api_suffix(value)
    if read_env_value("AMD_INFERENCE_LOCATION", INSTALL_DIR).strip().lower() == "host":
        # A Windows-hosted llama-server, as containers reach it (the legacy
        # key name is read for one release).
        for key in ("NATIVE_LLM_CONTAINER_BASE_URL", "LEMONADE_CONTAINER_BASE_URL"):
            value = read_env_value(key, INSTALL_DIR)
            if value:
                return _strip_llm_api_suffix(value)
    for key in ("LLM_URL", "LLM_API_URL", "OLLAMA_URL"):
        value = read_env_value(key, INSTALL_DIR)
        if value and "litellm" not in value.lower():
            return _strip_llm_api_suffix(value)
    return f"http://{host}:{port}"


def _model_name_tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    token = Path(str(value).strip()).name
    if not token:
        return set()
    lower = token.lower()
    tokens = {lower}
    # Retired Lemonade ids stay matchable for one release, so a persisted
    # receipt or performance row still names its GGUF.
    for prefix in ("extra.", "user."):
        if lower.startswith(prefix):
            tokens.add(lower[len(prefix):])
    for candidate in tuple(tokens):
        if candidate.endswith(".gguf"):
            tokens.add(candidate[:-5])
    return tokens


def _catalog_model_tokens(model: dict) -> set[str]:
    tokens: set[str] = set()
    for key in ("id", "gguf_file", "llm_model_name"):
        tokens.update(_model_name_tokens(model.get(key)))
    gguf_file = model.get("gguf_file")
    if gguf_file:
        tokens.update(_model_name_tokens(f"extra.{gguf_file}"))
    return tokens


def _fetch_loaded_model_sync() -> str | None:
    service = SERVICES.get("llama-server", {})
    host = service.get("host", "llama-server")
    port = int(service.get("port", 8080))
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_fetch_llama_loaded_model(host, port))
    except (httpx.HTTPError, OSError, RuntimeError, ValueError):
        return None
    finally:
        loop.close()


def _read_activation_receipt() -> dict:
    path = Path(DATA_DIR) / "model-activation-receipt.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _activation_receipt_matches(
    model_id: str | None,
    model: dict,
    loaded_model: str | None,
) -> bool:
    if not model_id or not loaded_model:
        return False
    receipt = _read_activation_receipt()
    if (
        receipt.get("schema") != "ods.model-activation-receipt.v1"
        or receipt.get("status") != "complete"
        or receipt.get("modelId") != model_id
        or receipt.get("contextVerified") is False
    ):
        return False

    gguf_file = str(model.get("gguf_file") or model.get("gguf") or "").strip()
    if not gguf_file or str(receipt.get("ggufFile") or "").casefold() != gguf_file.casefold():
        return False
    if not isinstance(receipt.get("consumers"), dict):
        return False

    runtime_tokens = _model_name_tokens(receipt.get("runtimeModelId"))
    loaded_tokens = _model_name_tokens(loaded_model)
    return bool(runtime_tokens and loaded_tokens and runtime_tokens & loaded_tokens)


def _verified_activation_context(loaded_model: str | None) -> int | None:
    if not loaded_model:
        return None
    receipt = _read_activation_receipt()
    if (
        receipt.get("schema") != "ods.model-activation-receipt.v1"
        or receipt.get("status") != "complete"
        or receipt.get("contextVerified") is not True
        or not (_model_name_tokens(receipt.get("runtimeModelId")) & _model_name_tokens(loaded_model))
    ):
        return None
    try:
        context = int(receipt.get("contextLength") or 0)
    except (TypeError, ValueError):
        return None
    return context if context > 0 else None


def _configured_model_identity_matches(model: dict) -> bool:
    gguf_file = model.get("gguf_file")
    if not gguf_file:
        return False
    if _read_active_model() != gguf_file:
        return False
    configured_llm = (
        read_env_file_value("LLM_MODEL", INSTALL_DIR)
        or read_env_value("LLM_MODEL", INSTALL_DIR)
    )
    if not (_model_name_tokens(configured_llm) & _catalog_model_tokens(model)):
        return False
    if not (Path(DATA_DIR) / "models" / gguf_file).exists():
        return False
    return True


def _already_active_model(model_id: str, model: dict) -> tuple[bool, str | None]:
    # Fetch the live backend identity even when the bind-mounted .env identity
    # is stale. Model activation replaces .env atomically on the host, so a
    # long-running container with a single-file bind mount can retain the old
    # inode until it is recreated.
    loaded_model = _fetch_loaded_model_sync()
    if not _configured_model_identity_matches(model):
        return False, loaded_model

    if _model_name_tokens(loaded_model) & _catalog_model_tokens(model):
        # llama-server serves exactly one model, proven by the activation
        # receipt; a chat probe here would only turn an idempotent Run click
        # into a long wait.
        if _activation_receipt_matches(model_id, model, loaded_model):
            return True, loaded_model
    return False, loaded_model


def _requested_activation_context(
    body: dict[str, Any] | None,
) -> int | None:
    if body is None or "context_length" not in body:
        return None
    value = body.get("context_length")
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(
            status_code=400,
            detail="context_length must be an integer",
        )
    if not _MIN_MODEL_CONTEXT <= value <= _MAX_MODEL_CONTEXT:
        raise HTTPException(
            status_code=400,
            detail=f"context_length must be a safe integer of at least {_MIN_MODEL_CONTEXT}",
        )
    return value


def _policy_activation_context(model_id: str, preferred_context: int | None = None) -> int | None:
    """Context a switch to ``model_id`` serves: the installer's policy.

    performance_oracle.activation_context_plan runs the installer's selector
    code for this one model on this hardware (the Hermes floor when it fits,
    otherwise the largest context that does), so a dashboard switch, the
    model list and a fresh install agree. None keeps the host agent's own
    default (unknown hardware, an import outside the catalog, or a model that
    fits at no context).
    """
    entry = _find_normalized_model(model_id)
    if entry is None:
        return None
    try:
        gpu = get_gpu_info()
    except _GPU_VRAM_EXCEPTIONS as exc:
        logger.debug("GPU detection failed while planning activation context: %s", exc)
        gpu = None
    plan = activation_context_plan(entry, INSTALL_DIR, gpu, preferred_context=preferred_context)
    if not plan or not plan.get("fits"):
        return None
    try:
        context = int(plan.get("context_length") or 0)
    except (TypeError, ValueError):
        return None
    return context if _MIN_MODEL_CONTEXT <= context <= _MAX_MODEL_CONTEXT else None


def _recommended_model_context(model: dict) -> int | None:
    """Installer-recorded context when ``model`` is the installer's pick."""
    identity = {
        str(value).casefold()
        for value in (
            read_env_file_value("MODEL_RECOMMENDED_GGUF", INSTALL_DIR),
            read_env_file_value("MODEL_RECOMMENDED_MODEL", INSTALL_DIR),
        )
        if value
    }
    if not identity & {
        str(value).casefold()
        for value in (model.get("gguf_file"), model.get("llm_model_name"), model.get("id"))
        if value
    }:
        return None
    try:
        context = int(str(read_env_file_value("MODEL_RECOMMENDED_CONTEXT", INSTALL_DIR) or "").strip())
    except (TypeError, ValueError):
        return None
    return context if context > 0 else None


def _configured_context_length() -> int | None:
    keys = ("CTX_SIZE", "MAX_CONTEXT")
    for reader in (read_env_file_value, read_env_value):
        for key in keys:
            value = reader(key, INSTALL_DIR)
            try:
                parsed = int(str(value).strip())
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                return parsed
    return None


async def _await_or_default(coro, default, label: str, timeout_seconds: float = 2.0):
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except (asyncio.TimeoutError, httpx.HTTPError, OSError, RuntimeError, KeyError) as exc:
        logger.debug("%s unavailable: %s", label, exc)
        return default


def _get_gpu_vram() -> Optional[ModelLibraryGpu]:
    """Get GPU VRAM info for model compatibility gating."""
    try:
        from gpu import get_gpu_info
        gpu = get_gpu_info()
        if gpu is None:
            return None
        total_gb = gpu.memory_total_mb / 1024
        used_gb = gpu.memory_used_mb / 1024
        return ModelLibraryGpu(
            vramTotal=round(total_gb, 1),
            vramUsed=round(used_gb, 1),
            vramFree=round(total_gb - used_gb, 1),
        )
    except _GPU_VRAM_EXCEPTIONS as exc:
        logger.warning("GPU VRAM detection failed: %s", exc)
        return None


def _format_size(size_mb: int) -> str:
    """Format size in MB to a human-readable string."""
    if size_mb >= 1024:
        return f"{size_mb / 1024:.1f} GB"
    return f"{size_mb} MB"


def _hf_token() -> str:
    return str(
        read_env_file_value("HF_TOKEN", INSTALL_DIR)
        or read_env_value("HF_TOKEN", INSTALL_DIR)
        or ""
    ).strip()


def _hf_cache_identity() -> str:
    """Partition metadata caches without retaining or exposing the token."""
    token = _hf_token()
    return hashlib.sha256(token.encode("utf-8")).hexdigest() if token else "public"


def _hf_headers() -> dict[str, str]:
    headers = {"User-Agent": "ODS-dashboard/2.5 model-library"}
    token = _hf_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _hf_cache_get(
    cache_key: tuple[str, str, int, str],
) -> tuple[float, dict[str, Any]] | None:
    """Read and refresh one bounded least-recently-used search entry."""
    with _HF_SEARCH_CACHE_LOCK:
        cached = _HF_SEARCH_CACHE.pop(cache_key, None)
        if cached is not None:
            _HF_SEARCH_CACHE[cache_key] = cached
        return cached


def _hf_cache_put(
    cache_key: tuple[str, str, int, str],
    response: dict[str, Any],
) -> None:
    """Bound arbitrary search/token combinations while retaining stale fallback."""
    with _HF_SEARCH_CACHE_LOCK:
        _HF_SEARCH_CACHE.pop(cache_key, None)
        _HF_SEARCH_CACHE[cache_key] = (time.monotonic(), response)
        while len(_HF_SEARCH_CACHE) > _HF_SEARCH_CACHE_MAX_ENTRIES:
            _HF_SEARCH_CACHE.pop(next(iter(_HF_SEARCH_CACHE)))


def _hf_avatar_cache_get(cache_key: tuple[str, str]) -> tuple[float, str | None] | None:
    with _HF_AVATAR_CACHE_LOCK:
        cached = _HF_AVATAR_CACHE.pop(cache_key, None)
        if cached is not None:
            _HF_AVATAR_CACHE[cache_key] = cached
        return cached


def _hf_avatar_cache_put(cache_key: tuple[str, str], avatar_url: str | None) -> None:
    with _HF_AVATAR_CACHE_LOCK:
        _HF_AVATAR_CACHE.pop(cache_key, None)
        _HF_AVATAR_CACHE[cache_key] = (time.monotonic(), avatar_url)
        while len(_HF_AVATAR_CACHE) > _HF_AVATAR_CACHE_MAX_ENTRIES:
            _HF_AVATAR_CACHE.pop(next(iter(_HF_AVATAR_CACHE)))


def _hf_trusted_avatar_url(value: Any) -> str | None:
    """Normalize Hub avatar metadata without permitting an arbitrary redirect."""
    if not isinstance(value, str) or not value.strip():
        return None
    avatar_url = urljoin(f"{_HF_API_BASE}/", value.strip())
    parsed = urlsplit(avatar_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _HF_AVATAR_HOSTS
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return avatar_url


async def _hf_author_avatar_url(author: str) -> str | None:
    """Resolve an author's uploaded Hub avatar, caching positive and negative results."""
    if not _HF_AUTHOR_RE.fullmatch(author):
        return None
    cache_key = (author.lower(), _hf_cache_identity())
    now = time.monotonic()
    cached = _hf_avatar_cache_get(cache_key)
    if cached and now - cached[0] < _HF_AVATAR_CACHE_TTL_SECONDS:
        return cached[1]

    for account_type in ("organizations", "users"):
        try:
            payload, _headers = await _hf_get_json(
                f"/api/{account_type}/{quote(author, safe='')}/overview",
            )
        except HTTPException as exc:
            if exc.status_code == 404:
                continue
            logger.info("Hugging Face avatar lookup failed for %s: %s", author, exc.detail)
            return None
        if isinstance(payload, dict):
            avatar_url = _hf_trusted_avatar_url(payload.get("avatarUrl"))
            _hf_avatar_cache_put(cache_key, avatar_url)
            return avatar_url

    _hf_avatar_cache_put(cache_key, None)
    return None


def _hf_license(payload: dict[str, Any]) -> str | None:
    card_data = payload.get("cardData")
    if isinstance(card_data, dict) and card_data.get("license"):
        return str(card_data["license"])
    raw_tags = payload.get("tags")
    for tag in raw_tags if isinstance(raw_tags, list) else []:
        if isinstance(tag, str) and tag.startswith("license:"):
            return tag.split(":", 1)[1]
    return None


def _hf_quantization(filename: str) -> str | None:
    match = _HF_QUANT_RE.search(Path(filename).name)
    return match.group("quant").upper() if match else None


def _hf_context_length(payload: dict[str, Any]) -> tuple[int | None, str]:
    raw_gguf = payload.get("gguf")
    gguf = raw_gguf if isinstance(raw_gguf, dict) else {}
    value = gguf.get("context_length")
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 512 <= value <= _MAX_MODEL_CONTEXT
    ):
        return value, "gguf_metadata"

    raw_config = payload.get("config")
    config = raw_config if isinstance(raw_config, dict) else {}
    raw_text_config = config.get("text_config")
    text_config = raw_text_config if isinstance(raw_text_config, dict) else {}
    for source in (text_config, config):
        for key in (
            "max_position_embeddings",
            "max_sequence_length",
            "model_max_length",
            "n_ctx",
            "seq_length",
        ):
            value = source.get(key)
            # Generic model configs sometimes publish enormous tokenizer
            # sentinels instead of a usable runtime window. Keep that defensive
            # ceiling for config fallbacks; the GGUF summary above is authoritative.
            if (
                isinstance(value, int)
                and not isinstance(value, bool)
                and 512 <= value <= 4_194_304
            ):
                return value, "hub_config"
    return None, "unavailable"


def _hf_file_metadata(sibling: Any) -> tuple[int | None, str | None]:
    if not isinstance(sibling, dict):
        return None, None
    lfs = sibling.get("lfs") if isinstance(sibling.get("lfs"), dict) else {}
    raw_size = lfs.get("size") or sibling.get("size")
    raw_sha = lfs.get("sha256")
    try:
        size = int(raw_size)
    except (TypeError, ValueError):
        size = None
    sha = str(raw_sha or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        sha = None
    return size if size and size > 0 else None, sha


def _hf_nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


# Speculative-decoding heads published beside a model's weights: EAGLE-3
# drafts (ggml-org's "eagle3-*.gguf"), multi-token-prediction heads
# (unsloth's "MTP/mtp-*.gguf") and DFlash drafts. None runs on its own, and
# read as the repository's header one misdescribes the model: gpt-oss-20b
# was refused as "eagle3" on b9014 and gemma-4-E4B shown without a chat
# template (fleet, 2026-10-09).
_HF_SPECULATIVE_HEAD_RE = re.compile(r"(?:^|[/._-])(?:eagle3|mtp|dflash)(?:[/._-]|$)")


def _hf_supported_gguf_filename(filename: str) -> bool:
    basename = Path(filename).name.lower()
    if not basename.endswith(".gguf"):
        return False
    if _HF_SPECULATIVE_HEAD_RE.search(filename.lower()):
        return False
    # imatrix: llama.cpp importance-matrix calibration data that quantizers
    # publish next to the weights (bartowski's "*-imatrix.gguf"); not a model.
    unsupported_markers = ("mmproj", "projector", "adapter", "lora", "tokenizer", "imatrix")
    return not any(marker in basename for marker in unsupported_markers)


def _hf_llm_runtime_compatibility(payload: dict[str, Any]) -> tuple[bool, str | None]:
    pipeline = str(payload.get("pipeline_tag") or "text-generation").lower()
    repo_id = str(payload.get("id") or payload.get("modelId") or "").lower()
    raw_tags = payload.get("tags")
    tags = {
        str(tag).lower()
        for tag in raw_tags
    } if isinstance(raw_tags, list) else set()
    unsupported_pipelines = {
        "automatic-speech-recognition",
        "audio-classification",
        "feature-extraction",
        "image-classification",
        "image-to-image",
        "sentence-similarity",
        "text-ranking",
        "text-to-image",
        "text-to-speech",
        "zero-shot-image-classification",
    }
    if pipeline in unsupported_pipelines:
        return False, f"{pipeline.replace('-', ' ').title()} requires a dedicated ODS runtime"
    unsupported_tags = {
        "automatic-speech-recognition",
        "feature-extraction",
        "sentence-transformers",
        "text-ranking",
        "text-to-image",
        "text-to-speech",
    }
    if tags & unsupported_tags:
        return False, "This repository targets a non-LLM runtime"
    if re.search(r"(^|[-_/])(embed|embedding|asr|whisper|tts)([-_/]|$)", repo_id):
        return False, "This repository targets a non-LLM runtime"
    return True, None


def _hf_artifact_id(filenames: list[str]) -> str:
    return hashlib.sha256("\n".join(filenames).encode("utf-8")).hexdigest()[:20]


def _hf_gguf_artifacts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    siblings = payload.get("siblings") if isinstance(payload.get("siblings"), list) else []
    ggufs = []
    for sibling in siblings:
        filename = str(sibling.get("rfilename") or "") if isinstance(sibling, dict) else ""
        if not _hf_supported_gguf_filename(filename):
            continue
        size, sha = _hf_file_metadata(sibling)
        if size is None or sha is None:
            continue
        ggufs.append({"filename": filename, "sizeBytes": size, "sha256": sha})

    split_groups: dict[tuple[str, str, int], list[tuple[int, dict[str, Any]]]] = {}
    singles: list[dict[str, Any]] = []
    for artifact in ggufs:
        match = _HF_SPLIT_GGUF_RE.match(Path(artifact["filename"]).name)
        if not match:
            singles.append(artifact)
            continue
        parent = Path(artifact["filename"]).parent.as_posix()
        key = (parent, match.group("prefix"), int(match.group("total")))
        split_groups.setdefault(key, []).append((int(match.group("part")), artifact))

    groups: list[dict[str, Any]] = []
    for artifact in singles:
        groups.append({
            "id": _hf_artifact_id([artifact["filename"]]),
            "label": Path(artifact["filename"]).name,
            "quantization": _hf_quantization(artifact["filename"]),
            "sizeBytes": artifact["sizeBytes"],
            "files": [artifact],
            "split": False,
        })
    for (parent, prefix, total), parts in split_groups.items():
        parts.sort(key=lambda item: item[0])
        if len(parts) != total or [number for number, _ in parts] != list(range(1, total + 1)):
            continue
        files = [artifact for _, artifact in parts]
        groups.append({
            "id": _hf_artifact_id([artifact["filename"] for artifact in files]),
            "label": f"{'' if parent == '.' else parent + '/'}{prefix} ({total} parts)",
            "quantization": _hf_quantization(prefix),
            "sizeBytes": sum(artifact["sizeBytes"] for artifact in files),
            "files": files,
            "split": True,
        })
    groups.sort(key=lambda item: (item["sizeBytes"], item["label"].lower()))
    return groups


_HF_PROJECTOR_PRECISION_ORDER = ("F16", "BF16", "F32", "Q8_0")


def _hf_gguf_projectors(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Vision projector files (mmproj) a repository publishes next to its weights."""
    siblings = payload.get("siblings") if isinstance(payload.get("siblings"), list) else []
    projectors = []
    for sibling in siblings:
        filename = str(sibling.get("rfilename") or "") if isinstance(sibling, dict) else ""
        basename = Path(filename).name.lower()
        if not basename.endswith(".gguf") or "mmproj" not in basename:
            continue
        size, sha = _hf_file_metadata(sibling)
        if size is None or sha is None:
            continue
        projectors.append({
            "id": _hf_artifact_id([filename]),
            "label": Path(filename).name,
            "filename": filename,
            "sizeBytes": size,
            "sha256": sha,
            "precision": _hf_quantization(filename),
        })
    projectors.sort(key=lambda item: str(item["label"]).lower())
    return projectors


def _hf_default_projector(projectors: list[dict[str, Any]]) -> dict[str, Any] | None:
    """F16, then BF16 (then F32, Q8_0), else the only one: llama.cpp -hf's closest-name pick."""
    for precision in _HF_PROJECTOR_PRECISION_ORDER:
        matches = [item for item in projectors if str(item.get("precision") or "").upper() == precision]
        if matches:
            return matches[0]
    return projectors[0] if len(projectors) == 1 else None


def _hf_search_item(payload: dict[str, Any]) -> dict[str, Any] | None:
    repo_id = str(payload.get("id") or payload.get("modelId") or "")
    if not _HF_REPO_RE.fullmatch(repo_id):
        return None
    raw_siblings = payload.get("siblings")
    sibling_names = [
        str(item.get("rfilename") or "")
        for item in raw_siblings
        if isinstance(item, dict)
    ] if isinstance(raw_siblings, list) else []
    raw_tags = payload.get("tags")
    tags = [str(tag) for tag in raw_tags[:12]] if isinstance(raw_tags, list) else []
    gguf_count = sum(_hf_supported_gguf_filename(name) for name in sibling_names)
    runtime_compatible, runtime_reason = _hf_llm_runtime_compatibility(payload)
    return {
        "id": repo_id,
        "author": repo_id.split("/", 1)[0],
        "name": repo_id.split("/", 1)[1],
        "downloads": _hf_nonnegative_int(payload.get("downloads")),
        "likes": _hf_nonnegative_int(payload.get("likes")),
        "lastModified": payload.get("lastModified"),
        "pipelineTag": payload.get("pipeline_tag") or "text-generation",
        "gated": bool(payload.get("gated")),
        "private": bool(payload.get("private")),
        "license": _hf_license(payload),
        "ggufFileCount": gguf_count,
        "runtimeCompatible": runtime_compatible,
        "runtimeReason": runtime_reason,
        "tags": tags,
        "url": f"{_HF_API_BASE}/{repo_id}",
    }


async def _hf_get_json(
    path: str,
    *,
    params: dict[str, Any] | None = None,
    timeout_seconds: float = 20.0,
    connect_timeout_seconds: float = 8.0,
) -> tuple[Any, httpx.Headers]:
    timeout = httpx.Timeout(
        timeout_seconds,
        connect=min(connect_timeout_seconds, timeout_seconds),
    )
    response: httpx.Response | None = None
    last_error: HTTPException | None = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    f"{_HF_API_BASE}{path}",
                    params=params,
                    headers=_hf_headers(),
                )
            break
        except httpx.TimeoutException as exc:
            last_error = HTTPException(status_code=504, detail="Hugging Face did not respond in time")
            last_error.__cause__ = exc
        except httpx.HTTPError as exc:
            last_error = HTTPException(status_code=502, detail=f"Hugging Face request failed: {exc}")
            last_error.__cause__ = exc
        if attempt == 0:
            await asyncio.sleep(0.25)
    if response is None:
        assert last_error is not None
        raise last_error
    if response.status_code in {401, 403}:
        raise HTTPException(
            status_code=403,
            detail="This Hugging Face repository requires an accepted license and a valid HF_TOKEN",
        )
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Hugging Face repository not found")
    if response.status_code == 429:
        raise HTTPException(status_code=429, detail="Hugging Face rate limit reached; retry later or configure HF_TOKEN")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Hugging Face returned HTTP {response.status_code}")
    try:
        return response.json(), response.headers
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Hugging Face returned invalid JSON") from exc


async def _hf_repo_details(repo_id: str) -> dict[str, Any]:
    if not _HF_REPO_RE.fullmatch(repo_id):
        raise HTTPException(status_code=400, detail="Invalid Hugging Face repository id")
    payload, _headers = await _hf_get_json(
        f"/api/models/{quote(repo_id, safe='/')}",
        params={
            "blobs": "true",
            "expand": [
                "gguf",
                "sha",
                "downloads",
                "likes",
                "lastModified",
                "pipeline_tag",
                "gated",
                "private",
                "tags",
                "cardData",
            ],
        },
        timeout_seconds=60.0,
        connect_timeout_seconds=20.0,
    )
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Hugging Face returned an invalid repository record")
    artifacts = _hf_gguf_artifacts(payload)
    projectors = _hf_gguf_projectors(payload)
    vision_unavailable = await asyncio.to_thread(_projector_unavailable_reason) if projectors else None
    default_projector = None if vision_unavailable else _hf_default_projector(projectors)
    imported_by_artifact = {
        str(record.get("source_artifact_id") or ""): record
        for record in _read_model_records(_imported_library_path(), required=False)
        if record.get("source") == "huggingface"
        and record.get("source_repo") == repo_id
        and record.get("source_revision") == str(payload.get("sha") or "")
    }
    for artifact in artifacts:
        imported = imported_by_artifact.get(artifact["id"])
        artifact["importedModelId"] = imported.get("id") if imported else None
        if imported:
            filenames = [
                str(part.get("file") or "")
                for part in imported.get("gguf_parts") or []
                if isinstance(part, dict)
            ] or [str(imported.get("gguf_file") or "")]
            artifact["installed"] = bool(filenames) and all(
                _installed_model_path(filename) is not None
                for filename in filenames
            )
        else:
            artifact["installed"] = False
    context_length, context_source = _hf_context_length(payload)
    runtime_compatible, runtime_reason = _hf_llm_runtime_compatibility(payload)
    raw_gguf = payload.get("gguf") if isinstance(payload.get("gguf"), dict) else {}
    raw_tags = payload.get("tags")
    return {
        "id": repo_id,
        "sha": str(payload.get("sha") or ""),
        # The Hub summary describes one file only; the per-file header read
        # in the preflight is authoritative.
        "ggufArchitecture": raw_gguf.get("architecture") if isinstance(raw_gguf.get("architecture"), str) else None,
        "tags": [str(tag) for tag in raw_tags] if isinstance(raw_tags, list) else [],
        "downloads": _hf_nonnegative_int(payload.get("downloads")),
        "likes": _hf_nonnegative_int(payload.get("likes")),
        "lastModified": payload.get("lastModified"),
        "pipelineTag": payload.get("pipeline_tag") or "text-generation",
        "gated": bool(payload.get("gated")),
        "private": bool(payload.get("private")),
        "license": _hf_license(payload),
        "contextLength": context_length,
        "contextSource": context_source,
        "runtimeCompatible": runtime_compatible,
        "runtimeReason": runtime_reason,
        "artifacts": artifacts,
        # Vision projectors (WP2): one is imported with the chosen weights by default.
        "projectors": projectors,
        "defaultProjectorId": default_projector["id"] if default_projector else None,
        "visionUnavailableReason": vision_unavailable,
        "authenticated": bool(_hf_token()),
        "url": f"{_HF_API_BASE}/{repo_id}",
    }


_ARCHITECTURES_PATH = Path(INSTALL_DIR) / "config" / "llama-cpp-architectures.json"


def _preflight_header_artifact(artifacts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Default to one small artifact; its header speaks only for that artifact."""
    candidates = [artifact for artifact in artifacts if artifact.get("files")]
    if not candidates:
        return None
    return min(candidates, key=lambda artifact: (_hf_artifact_size(artifact), str(artifact.get("label") or "")))


def _hf_artifact_binding(details: dict[str, Any], artifact: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "repoId": details["id"],
        "revision": details["sha"],
        "artifactId": artifact.get("id") if artifact else None,
        "file": artifact["files"][0]["filename"] if artifact and artifact.get("files") else None,
    }


def _hf_artifact_context(details: dict[str, Any]) -> tuple[int | None, str]:
    # Hub metadata describes one file, without identifying which artifact.
    if len(details.get("artifacts") or []) != 1:
        return None, "unavailable"
    return details.get("contextLength"), details.get("contextSource") or "unavailable"


def _hf_artifact_size(artifact: dict[str, Any]) -> int:
    """Total bytes of one artifact (all split parts)."""
    declared = artifact.get("sizeBytes")
    if isinstance(declared, int) and not isinstance(declared, bool) and declared > 0:
        return declared
    return sum(
        int(item.get("sizeBytes") or 0)
        for item in artifact.get("files") or []
        if isinstance(item, dict)
    )


async def _hf_preflight_gate(
    details: dict[str, Any],
    artifact: dict[str, Any] | None = None,
    *,
    read_header: bool = True,
) -> dict[str, Any]:
    """The header read and the two hard refusals, shared by preflight and import.

    The import passes ``read_header=False`` and uses only a header the
    preflight already read, so a slow Hub never delays an import; without one
    the gate can use Hub hints only for a repository with one artifact.
    """
    header = None
    header_status: dict[str, Any] = {"status": "unavailable", "reason": "no_artifact"}
    if artifact is None:
        artifact = _preflight_header_artifact(details.get("artifacts") or [])
    source = artifact["files"][0] if artifact and artifact.get("files") else None
    if source is not None and not read_header:
        header = hf_gguf_header.cached_gguf_header(details["id"], details["sha"], source["filename"])
        header_status = (
            {"status": "read", "file": source["filename"], "bytesRead": header.get("bytes_read")}
            if header else {"status": "unavailable", "file": source["filename"], "reason": "not_read"}
        )
    elif source is not None:
        try:
            header = await hf_gguf_header.fetch_gguf_header(
                details["id"],
                details["sha"],
                source["filename"],
                expected_size=source.get("sizeBytes"),
                token=_hf_token(),
            )
            header_status = {
                "status": "read",
                "file": source["filename"],
                "bytesRead": header.get("bytes_read"),
            }
        except hf_gguf_header.HeaderUnavailable as exc:
            header_status = {
                "status": "unavailable",
                "file": source["filename"],
                "reason": exc.code,
                "message": exc.message,
            }
    policy = model_preflight.load_runtime_policy(_ARCHITECTURES_PATH)
    runtime_key, build = model_preflight.effective_build(
        policy,
        GPU_BACKEND,
        windows_hosted=_windows_hosted_runtime(),
    )
    single_artifact = len(details.get("artifacts") or []) == 1
    header_architecture = (header or {}).get("architecture")
    summary_architecture = details.get("ggufArchitecture") if single_artifact else None
    architecture = (
        header_architecture
        if isinstance(header_architecture, str) and header_architecture not in {"", "unknown"}
        else summary_architecture
    )
    kind = model_preflight.model_kind(
        header,
        details.get("pipelineTag") if single_artifact else None,
        details.get("tags") if single_artifact else None,
    )
    supported = (
        model_preflight.architecture_supported(policy, build, architecture)
        if kind == "chat" else None
    )
    # The Hub serves a gated repository's files only with a token whose
    # account accepted the license: refuse when no token is set, or when the
    # header read with the token was itself refused as gated.
    gated = bool(details.get("gated")) and (
        not _hf_token() or header_status.get("reason") == "gated"
    )
    return {
        "artifact": _hf_artifact_binding(details, artifact),
        "header": header,
        "headerStatus": header_status,
        "policy": policy,
        "architecture": architecture,
        "runtime": {"key": runtime_key, "build": build, "architectureSupported": supported},
        "modelKind": kind,
        "refusal": model_preflight.refusal(kind, supported, architecture, build, gated=gated),
    }


# Resolving a Windows-managed model store from WSL asks Windows first (about 5 s
# on the fleet's Strix Halo host), the same cost /v1/model/management carries.
_MODEL_STORAGE_TIMEOUT_SECONDS = 20


async def _model_storage_status() -> dict[str, Any] | None:
    """Free space where downloads land, from the host agent; None when unknown."""
    try:
        value = await asyncio.to_thread(
            request_agent_json, "GET", "/v1/model/storage", timeout=_MODEL_STORAGE_TIMEOUT_SECONDS,
        )
    except AgentClientError:
        return None
    if not isinstance(value, dict):
        return None
    return {key: value.get(key) for key in ("freeBytes", "totalBytes", "marginBytes")}


def _hf_default_context(declared: int | None) -> int:
    """The context an import starts at: min(declared, 32K), or 8K when unknown."""
    if isinstance(declared, int) and 512 <= declared <= _MAX_MODEL_CONTEXT:
        return min(declared, 32768)
    return 8192


def _hf_artifact_fit(
    artifact: dict[str, Any],
    layout: dict[str, Any],
    declared_context: int | None,
    gpu_info: Any,
    extra_bytes: int = 0,
) -> dict[str, Any]:
    """Will this quantization fit, and at what context, on this machine?

    ``extra_bytes`` is the vision projector loaded with the weights (WP2).
    """
    estimate = "architecture" if "recurrent_state_bytes" in layout else "rough"
    if gpu_info is None:
        return {"status": "unknown", "estimate": estimate}
    size = _hf_artifact_size(artifact) + max(int(extra_bytes or 0), 0)
    model = {
        "id": f"hf-preflight-{artifact['id']}",
        "gguf_file": artifact["files"][0]["filename"],
        "size_bytes": size,
        "size_mb": round(size / (1024 ** 2), 2),
        "context_length": _hf_default_context(declared_context),
        **layout,
    }
    plan = planned_model_context(model, gpu_info)
    if not plan["fits"]:
        status = "too_large"
    elif plan["meets_min_context"]:
        status = "fits"
    else:
        status = "fits_short_context"
    return {
        "status": status,
        "contextLength": plan["context_length"],
        "requiredGb": plan["required_gb"],
        "capacityGb": plan["capacity_gb"],
        "estimate": estimate,
    }


def _hf_artifact_tensors(gate: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    """WP1.6: can this host's llama.cpp read the artifact's tensor types?

    The header read for the repository speaks for its own file only; every
    other quantization is judged by a label that names a ggml type outright.
    """
    header = gate.get("header") or {}
    files = artifact.get("files") or []
    same_file = bool(files) and files[0].get("filename") == (gate.get("headerStatus") or {}).get("file")
    check = model_preflight.artifact_tensor_check(
        gate.get("policy"),
        gate["runtime"]["build"],
        artifact.get("quantization"),
        header.get("tensor_types") if same_file else None,
    )
    if check["status"] == "unsupported":
        check["refusal"] = model_preflight.tensor_refusal(gate["runtime"]["build"], check["unknown"])
    return check


async def _hf_preflight(
    details: dict[str, Any], artifact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # The Hub header read and the host's own answers are independent: wait for
    # the slowest, not their sum.
    gate, gpu_info, storage = await asyncio.gather(
        _hf_preflight_gate(details, artifact),
        asyncio.to_thread(get_gpu_info),
        _model_storage_status(),
    )
    header = gate["header"]
    layout = model_preflight.memory_fields(header)
    fallback_context, fallback_source = _hf_artifact_context(details)
    declared_context = layout.get("max_context_length") or fallback_context
    context_source = "gguf_header" if layout.get("max_context_length") else fallback_source
    projector = next((item for item in details.get("projectors") or []
                      if item["id"] == details.get("defaultProjectorId")), None)
    projector_bytes = int(projector["sizeBytes"]) if projector else 0
    artifacts = {}
    for artifact in details.get("artifacts") or []:
        artifact_gate = gate if gate["artifact"] == _hf_artifact_binding(details, artifact) else await _hf_preflight_gate(
            details, artifact, read_header=False,
        )
        artifact_layout = model_preflight.memory_fields(artifact_gate["header"])
        artifact_context = artifact_layout.get("max_context_length") or fallback_context
        # Access denial applies to the repository; model evidence does not.
        refusal = gate["refusal"] if (gate["refusal"] or {}).get("code") == "gated" else artifact_gate["refusal"]
        needed = 0 if artifact.get("installed") else _hf_artifact_size(artifact) + projector_bytes
        artifacts[artifact["id"]] = {
            "header": artifact_gate["headerStatus"],
            "architecture": artifact_gate["architecture"],
            "template": model_preflight.template_signals(artifact_gate["header"]),
            "contextLength": artifact_context,
            "contextSource": "gguf_header" if artifact_layout.get("max_context_length") else fallback_source,
            "refusal": refusal,
            "fit": _hf_artifact_fit(artifact, artifact_layout, artifact_context, gpu_info, projector_bytes),
            "disk": model_preflight.disk_status(needed, storage),
            "tensors": _hf_artifact_tensors(artifact_gate, artifact),
        }
    return {
        "id": details["id"],
        "sha": details["sha"],
        "artifactId": gate["artifact"]["artifactId"],
        "header": gate["headerStatus"],
        "architecture": gate["architecture"],
        "runtime": gate["runtime"],
        "modelKind": gate["modelKind"],
        "contextLength": declared_context,
        "contextSource": context_source,
        "template": model_preflight.template_signals(header),
        "storage": storage,
        "artifacts": artifacts,
        "projector": {key: projector[key] for key in ("id", "label", "sizeBytes", "precision")} if projector else None,
        "refusal": gate["refusal"],
    }


@router.get("/api/models/huggingface/preflight/{repo_id:path}")
async def huggingface_repository_preflight(
    repo_id: str,
    artifactId: str | None = None,
    api_key: str = Depends(verify_api_key),
):
    """What ODS can tell about a repository's GGUFs before downloading one."""
    details = await _hf_repo_details(repo_id)
    artifact = None
    if artifactId is not None:
        artifact = next((item for item in details["artifacts"] if item["id"] == artifactId), None)
        if artifact is None:
            raise HTTPException(status_code=409, detail="The selected GGUF artifact is no longer available at this revision")
    return await _hf_preflight(details, artifact)


def _hf_local_filename(repo_id: str, remote_filename: str, revision: str) -> str:
    repo_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", repo_id).strip("-._")
    basename = Path(remote_filename).name
    split = _HF_SPLIT_GGUF_RE.fullmatch(basename)
    stem = split.group("prefix") if split else Path(basename).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
    suffix = ".gguf"
    identity = remote_filename
    if split:
        # llama.cpp derives sibling paths from a shared prefix followed by
        # -00001-of-00002.gguf. Keep the digest common to the complete set and
        # before that suffix, including when long names must be shortened.
        suffix = f"-{split.group('part')}-of-{split.group('total')}.gguf"
        identity = Path(remote_filename).with_name(f"{split.group('prefix')}-of-{split.group('total')}.gguf").as_posix()
    digest = hashlib.sha256(
        f"{repo_id}\n{revision}\n{identity}".encode("utf-8")
    ).hexdigest()[:8]
    filename = f"hf-{repo_slug}-{stem}-{digest}{suffix}"
    if len(filename) > 220:
        filename = f"hf-{repo_slug[:60]}-{stem[:120]}-{digest}{suffix}"
    return filename


def _hf_projector_fields(details: dict[str, Any], projector: dict[str, Any]) -> dict[str, Any]:
    """The import record's vision projector: downloaded, verified and deleted with the weights."""
    repo_id, revision = details["id"], details["sha"]
    return {
        "mmproj_file": _hf_local_filename(repo_id, projector["filename"], revision),
        "mmproj_url": f"{_HF_API_BASE}/{quote(repo_id, safe='/')}/resolve/{revision}/{quote(projector['filename'], safe='/')}",
        "mmproj_sha256": projector["sha256"],
        "mmproj_size_bytes": projector["sizeBytes"],
        "mmproj_source_file": projector["filename"],
    }


def _hf_requested_projector(details: dict[str, Any], body: dict[str, Any]) -> dict[str, Any] | None:
    """The projector an import brings: the chosen one, else the default, unless vision is off."""
    if body.get("includeVision") is False or not details.get("projectors") or not details.get("defaultProjectorId"):
        return None
    wanted = str(body.get("projectorId") or details.get("defaultProjectorId") or "")
    projector = next((item for item in details["projectors"] if item["id"] == wanted), None)
    if body.get("projectorId") and projector is None:
        raise HTTPException(status_code=409, detail="The selected vision projector is no longer available at this revision")
    return projector


def _hf_download_payload(record: dict[str, Any]) -> dict[str, Any]:
    """The host-agent download request for a model record, projector included."""
    payload: dict[str, Any] = {
        "gguf_file": record["gguf_file"],
        "gguf_url": record.get("gguf_url", ""),
        "gguf_sha256": record.get("gguf_sha256", ""),
    }
    if record.get("gguf_parts"):
        payload["gguf_parts"] = record["gguf_parts"]
    if record.get("mmproj_file"):
        payload["mmproj"] = {
            "file": record["mmproj_file"],
            "url": record.get("mmproj_url", ""),
            "sha256": record.get("mmproj_sha256", ""),
        }
    return payload


def _hf_import_record(
    details: dict[str, Any],
    artifact: dict[str, Any],
    *,
    gate: dict[str, Any] | None = None,
    runtime_override: dict[str, Any] | None = None,
    projector: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bound = (gate or {}).get("artifact") == _hf_artifact_binding(details, artifact)
    header = (gate or {}).get("header") if bound else None
    layout = model_preflight.memory_fields(header)
    context_length, context_source = _hf_artifact_context(details)
    details = {**details, "contextLength": context_length, "contextSource": context_source}
    if layout.get("max_context_length"):
        # Only the selected artifact's own header can supply its layout/context.
        details = {**details, "contextLength": layout["max_context_length"], "contextSource": "gguf_header"}
    record = _hf_import_record_base(details, artifact)
    if header:
        record.update({key: value for key, value in layout.items() if key != "max_context_length"})
        record["architecture"] = (gate or {}).get("architecture")
        record["template_signals"] = model_preflight.template_signals(header)
    if runtime_override and bound:
        record["runtime_override"] = runtime_override
    if projector:
        record.update(_hf_projector_fields(details, projector))
    return record


def _hf_import_record_base(details: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    repo_id = details["id"]
    revision = details["sha"]
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", revision):
        raise HTTPException(status_code=502, detail="Hugging Face did not provide an immutable repository revision")
    remote_files = artifact["files"]
    if not remote_files:
        raise HTTPException(status_code=422, detail="The selected artifact contains no files")
    local_files = [
        _hf_local_filename(repo_id, item["filename"], revision)
        for item in remote_files
    ]
    if len(set(local_files)) != len(local_files):
        raise HTTPException(status_code=409, detail="The selected artifact contains colliding local filenames")
    parts = []
    for remote, local_filename in zip(remote_files, local_files):
        parts.append({
            "file": local_filename,
            "url": f"{_HF_API_BASE}/{quote(repo_id, safe='/')}/resolve/{revision}/{quote(remote['filename'], safe='/')}",
            "sha256": remote["sha256"],
            "size_bytes": remote["sizeBytes"],
            "source_file": remote["filename"],
        })
    total_size = sum(item["sizeBytes"] for item in remote_files)
    quantization = artifact.get("quantization") or "unknown"
    digest = hashlib.sha256(
        f"{repo_id}\n{revision}\n{artifact['id']}".encode("utf-8")
    ).hexdigest()[:12]
    model_id = f"hf-{re.sub(r'[^a-z0-9]+', '-', repo_id.lower()).strip('-')[:72]}-{digest}"
    declared_context = details.get("contextLength")
    try:
        max_context_length = int(declared_context) if declared_context is not None else 0
    except (TypeError, ValueError, OverflowError):
        max_context_length = 0
    context_limit_known = 512 <= max_context_length <= _MAX_MODEL_CONTEXT
    context_length = min(max_context_length, 32768) if context_limit_known else 8192
    size_gb = total_size / (1024 ** 3)
    record: dict[str, Any] = {
        "id": model_id,
        "name": f"{repo_id.split('/', 1)[1]} · {quantization}",
        "family": repo_id.split("/", 1)[1].split("-", 1)[0].lower(),
        "gguf_file": local_files[0],
        "gguf_url": parts[0]["url"],
        "gguf_sha256": parts[0]["sha256"],
        "size_bytes": total_size,
        "size_mb": round(total_size / (1024 ** 2), 2),
        "vram_required_gb": round(size_gb + min(max(size_gb * 0.18, 0.5), 3.5), 2),
        "context_length": context_length,
        "max_context_length": max_context_length if context_limit_known else None,
        "context_limit_known": context_limit_known,
        "quantization": quantization,
        "specialty": "Community GGUF",
        "description": f"Imported from Hugging Face repository {repo_id}. Not validated by ODS.",
        "llm_model_name": model_id,
        "source": "huggingface",
        "source_repo": repo_id,
        "source_revision": revision,
        "source_artifact_id": artifact["id"],
        "source_url": details["url"],
        "license": details.get("license"),
        "context_source": details.get("contextSource"),
        "app_compatibility": {
            "openai_chat": {
                "status": "unknown",
                "label": "Community model not validated",
                "reason": "Run a local benchmark and compatibility check after download.",
            },
            "hermes_talk": {
                "status": "unknown",
                "label": "ODS Talk not validated",
                "reason": "Community Hugging Face imports are not part of the ODS compatibility matrix.",
            },
            "agent_viability": {
                "status": "unknown",
                "label": "Agent viability not validated",
                "reason": "Tool calling and instruction behavior vary by community model.",
            },
        },
        "imported_at": datetime.now(timezone.utc).isoformat(),
    }
    if len(parts) > 1:
        record["gguf_parts"] = parts
    else:
        record["size_bytes"] = parts[0]["size_bytes"]
    return record


@router.get("/api/models/huggingface/search")
async def search_huggingface_models(
    q: str = Query(default="", max_length=100),
    sort: str = Query(default="downloads"),
    limit: int = Query(default=20, ge=1, le=30),
    api_key: str = Depends(verify_api_key),
):
    """Search public/authenticated Hub metadata without exposing the token."""
    query = q.strip()
    sort_key = sort if sort in {"downloads", "likes", "lastModified"} else "downloads"
    cache_key = (query.lower(), sort_key, limit, _hf_cache_identity())
    now = time.monotonic()
    cached = _hf_cache_get(cache_key)
    if cached and now - cached[0] < _HF_SEARCH_CACHE_TTL_SECONDS:
        return cached[1]
    params: dict[str, Any] = {
        "filter": "gguf",
        "sort": sort_key,
        "direction": -1,
        "limit": limit,
        "full": "true",
    }
    if query:
        params["search"] = query
    try:
        payload, _headers = await _hf_get_json("/api/models", params=params)
    except HTTPException as exc:
        if cached and exc.status_code in {429, 502, 504}:
            return {**cached[1], "stale": True}
        raise
    if not isinstance(payload, list):
        raise HTTPException(status_code=502, detail="Hugging Face returned an invalid search result")
    models = [item for raw in payload if isinstance(raw, dict) if (item := _hf_search_item(raw))]
    response = {
        "models": models,
        "query": query,
        "sort": sort_key,
        "authenticated": bool(_hf_token()),
        "source": "huggingface",
    }
    _hf_cache_put(cache_key, response)
    return response


@router.get("/api/models/huggingface/authors/{author}/avatar")
async def huggingface_author_avatar(
    author: str,
    api_key: str = Depends(verify_api_key),
):
    """Redirect to the uploaded avatar declared by an official Hub profile."""
    if not _HF_AUTHOR_RE.fullmatch(author):
        raise HTTPException(status_code=400, detail="Invalid Hugging Face author")
    avatar_url = await _hf_author_avatar_url(author)
    if avatar_url is None:
        raise HTTPException(status_code=404, detail="Hugging Face author has no uploaded avatar")
    return RedirectResponse(
        avatar_url,
        status_code=307,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/api/models/huggingface/repositories/{repo_id:path}")
async def huggingface_repository_details(
    repo_id: str,
    api_key: str = Depends(verify_api_key),
):
    """Return integrity-qualified GGUF choices for one Hub repository."""
    return await _hf_repo_details(repo_id)


async def _prepare_huggingface_import(body: dict[str, Any]):
    """Prepare metadata without submitting a download to the host."""
    repo_id = str(body.get("repoId") or "").strip()
    artifact_id = str(body.get("artifactId") or "").strip()
    if not _HF_REPO_RE.fullmatch(repo_id) or not re.fullmatch(r"[0-9a-f]{20}", artifact_id):
        raise HTTPException(status_code=400, detail="repoId and artifactId are required")
    details = await _hf_repo_details(repo_id)
    if "revision" in body and body["revision"] != details.get("sha"):
        raise HTTPException(status_code=409, detail="The repository revision changed. Reopen it and check the selected file before importing.")
    if not details["runtimeCompatible"]:
        raise HTTPException(status_code=422, detail=details["runtimeReason"])
    artifact = next((item for item in details["artifacts"] if item["id"] == artifact_id), None)
    if artifact is None:
        raise HTTPException(status_code=409, detail="The selected GGUF artifact is no longer available at this revision")
    if (details.get("private") or details.get("gated")) and not _hf_token():
        raise HTTPException(
            status_code=403,
            detail="Private or gated repositories require HF_TOKEN",
        )
    gate = await _hf_preflight_gate(details, artifact, read_header=False)
    refusal = gate["refusal"]
    runtime_override = None
    if refusal is not None:
        if not (refusal["overridable"] and body.get("allowUnsupportedRuntime") is True):
            raise HTTPException(status_code=422, detail={
                "code": refusal["code"],
                "message": refusal["message"],
                "overridable": refusal["overridable"],
            })
        runtime_override = {
            "code": refusal["code"],
            "architecture": gate["architecture"],
            "build": gate["runtime"]["build"],
            "acknowledgedAt": datetime.now(timezone.utc).isoformat(),
        }
        logger.warning(
            "Hugging Face import of %s acknowledged an architecture (%s) that llama.cpp %s does not list",
            repo_id, gate["architecture"], gate["runtime"]["build"],
        )
    tensors = _hf_artifact_tensors(gate, artifact)
    if tensors["status"] == "unsupported":
        tensor_refusal = tensors["refusal"]
        if body.get("allowUnsupportedRuntime") is not True:
            raise HTTPException(status_code=422, detail=tensor_refusal)
        runtime_override = {
            **(runtime_override or {}),
            "code": tensor_refusal["code"] if runtime_override is None else runtime_override["code"],
            "tensorTypes": tensors["unknown"],
            "build": gate["runtime"]["build"],
            "acknowledgedAt": datetime.now(timezone.utc).isoformat(),
        }
        logger.warning(
            "Hugging Face import of %s (%s) acknowledged tensor types %s that llama.cpp %s cannot read",
            repo_id, artifact["label"], tensors["unknown"], gate["runtime"]["build"],
        )
    projector = _hf_requested_projector(details, body)
    record = _hf_import_record(details, artifact, gate=gate, runtime_override=runtime_override, projector=projector)

    bootstrap_conflict = _bootstrap_upgrade_download_conflict()
    if bootstrap_conflict is not None:
        raise HTTPException(
            status_code=409,
            detail={**bootstrap_conflict, "requestedModelId": record["id"]},
        )

    with _IMPORTED_MODELS_LOCK:
        records = _read_model_records(
            _imported_library_path(),
            required=False,
            strict=True,
        )
        previous = next(
            (
                item for item in records
                if item.get("id") == record["id"]
                and item.get("source_revision") == record["source_revision"]
                and item.get("source_artifact_id") == record["source_artifact_id"]
            ),
            None,
        )
        if previous and previous.get("imported_at"):
            record["imported_at"] = previous["imported_at"]
        record_filename = str(record.get("gguf_file") or "").lower()
        retained = [
            item for item in records
            if item.get("id") != record["id"]
            and str(item.get("gguf_file") or "").lower() != record_filename
        ]
        retained.append(record)
        _write_imported_library(retained)

    return details, artifact, record


@router.post("/api/models/huggingface/import")
async def import_huggingface_model(
    body: dict[str, Any] = Body(...),
    api_key: str = Depends(verify_api_key),
):
    """Pin, register, and start one integrity-qualified Hub GGUF download."""
    # A failure before dispatch is a definitive refusal, even if its status
    # is 500. A transport failure after dispatch remains uncertain: never
    # encourage the UI to replay a potentially accepted host operation.
    try:
        details, artifact, record = await _prepare_huggingface_import(body)
    except HTTPException as exc:
        exc.headers = {**(exc.headers or {}), "X-ODS-Import-Started": "false"}
        raise
    except Exception as exc:
        logger.exception("Hugging Face import preparation failed before dispatch")
        raise HTTPException(
            status_code=500,
            detail="Could not prepare the import. No download was started; you can retry.",
            headers={"X-ODS-Import-Started": "false"},
        ) from exc

    payload = _hf_download_payload(record)
    try:
        result = await asyncio.to_thread(_request_agent_download, payload)
    except HTTPException as exc:
        busy = exc.status_code == 409 and isinstance(exc.detail, dict) \
            and exc.detail.get("code") == "model_lifecycle_busy"
        if exc.status_code == 507 or busy:
            # The host refused before starting: a definitive, retryable refusal.
            exc.headers = {**(exc.headers or {}), "X-ODS-Import-Started": "false"}
        raise
    return {
        **result,
        "modelId": record["id"],
        "repoId": details["id"],
        "artifact": artifact["label"],
        "revision": details["sha"],
    }


def _newly_measured_tps(metrics: dict, loaded_model: str | None) -> float:
    # Sticky Dashboard values are historical evidence, not a fresh performance
    # sample for the model catalogue/current context on every polling request.
    if (metrics.get("throughput_state") != "measured"
            or metrics.get("throughput_model") != loaded_model
            # Runtime live intervals can aggregate concurrent slots; do not
            # turn them into single-request model performance/benchmark data.
            or metrics.get("throughput_mode") == "live_output_interval"):
        return 0.0
    return float(metrics.get("tokens_per_second") or 0)


def _model_management_proof() -> dict:
    """The host agent's management proof, with whether its launcher loads a vision projector."""
    if not _windows_hosted_runtime():
        return {"managed": False, "canActivate": False, "canUnload": False, "running": False, "vision": False}
    try:
        value = request_agent_json("GET", "/v1/model/management", timeout=20)
        if not isinstance(value, dict) or any(type(value.get(key)) is not bool for key in (
            "managed", "canActivate", "canUnload", "running"
        )):
            raise ValueError("Invalid model management response")
        if (not value['managed'] and any(value[key] for key in ('canActivate', 'canUnload', 'running'))
                or value['canActivate'] and not value['running']):
            raise ValueError("Inconsistent model management response")
        result = {key: value[key] for key in ("managed", "canActivate", "canUnload", "running")}
        result["vision"] = value["managed"] and value.get("vision") is True
        if isinstance(value.get('reason'), str):
            result['reason'] = value['reason'][:500]
        return result
    except (AgentClientError, ValueError):
        # A failed proof is unknown, not evidence of an independently managed service.
        return {"managed": None, "canActivate": False, "canUnload": False, "running": False, "vision": False,
                "reason": "Runtime management could not be verified"}


def _model_management() -> dict:
    """Project capability evidence; a network topology flag grants no control."""
    proof = _model_management_proof()
    if not _windows_hosted_runtime():
        return {key: proof[key] for key in ("managed", "canActivate", "canUnload", "running")}
    return {key: value for key, value in proof.items() if key != "vision"}


# Docker Desktop installs whose host agent launches llama-server.exe on
# Windows itself (the host agent's _WINDOWS_NATIVE_RUNTIME_MODES); that
# launch takes no projector.
_LEGACY_WINDOWS_NATIVE_MODES = frozenset({"windows-native-llama-server", "windows-llama-server-fallback"})


def _projector_unavailable_reason() -> str | None:
    """Why this runtime cannot load a vision projector, or None when it can."""
    mode = read_live_env_values(("AMD_INFERENCE_RUNTIME_MODE",)).get("AMD_INFERENCE_RUNTIME_MODE")
    if str(mode or "").strip().casefold() in _LEGACY_WINDOWS_NATIVE_MODES:
        return "The Windows llama.cpp runtime of this installation does not load vision files, so the model imports without one"
    if not _windows_hosted_runtime():
        return None
    management = _model_management_proof()
    if management.get("vision") is True:
        return None
    if management.get("managed") is True:
        return ("This Windows model runtime was set up before vision support, so the model imports "
                "without its vision file. Run Windows setup again to add vision")
    return "ODS could not confirm that the Windows model runtime loads vision files, so the model imports without one"


@router.get("/api/models", response_model=ModelLibraryResponse)
async def list_models(api_key: str = Depends(verify_api_key)):
    """List model catalog entries with source-labelled performance metadata."""
    global _last_recorded_throughput_sample
    gpu_info, loaded_model, agent_status = await asyncio.gather(
        asyncio.to_thread(get_gpu_info),
        _await_or_default(
            get_loaded_model(),
            None,
            "loaded model",
            timeout_seconds=_MODEL_DISCOVERY_TIMEOUT_SECONDS,
        ),
        asyncio.to_thread(_get_agent_model_status),
    )
    if not loaded_model:
        service = SERVICES.get("llama-server", {})
        host = service.get("host", "llama-server")
        port = int(service.get("port", 8080))
        loaded_model = await _await_or_default(
            _fetch_llama_loaded_model(host, port),
            None,
            "loaded model fallback",
            timeout_seconds=_MODEL_DISCOVERY_TIMEOUT_SECONDS,
        )
    metrics, context_size = await asyncio.gather(
        _await_or_default(
            get_llama_metrics(model_hint=loaded_model),
            {"tokens_per_second": 0, "lifetime_tokens": 0},
            "llama metrics",
        ),
        _await_or_default(
            get_llama_context_size(model_hint=loaded_model),
            None,
            "llama context",
        ),
    )
    context_size = context_size or _verified_activation_context(loaded_model)
    live_tps = _newly_measured_tps(metrics, loaded_model)
    payload = await asyncio.to_thread(
        build_models_payload,
        gpu_info,
        loaded_model,
        live_tps,
        INSTALL_DIR,
        DATA_DIR,
        context_size,
        catalog=_load_library(),
        downloaded_files_override=_installed_model_paths(),
    )
    _annotate_model_lifecycle(
        payload,
        _model_lifecycle_from_agent_status(agent_status),
    )
    payload["modelActivation"] = model_activation_status(agent_status)
    payload["modelRecoveryPending"] = isinstance(agent_status, dict) and agent_status.get("modelTransactionPending") is True
    loaded_entry = next((m for m in payload["models"] if m["status"] == "loaded"), None) or {}
    sample_key = (loaded_model, metrics.get("throughput_sampled_at"))
    if (gpu_info and loaded_model and live_tps > 0
            and sample_key[1] is not None and sample_key != _last_recorded_throughput_sample
            and loaded_entry.get("metadata", {}).get("source") != "runtime"):
        _last_recorded_throughput_sample = sample_key
        signature = build_sample_signature(
            loaded_entry or {"id": loaded_model, "gguf": _read_active_model()},
            gpu_info,
            context_size,
            INSTALL_DIR,
            _installed_model_path(loaded_entry["gguf"]) if loaded_entry.get("gguf") else None,
        )
        await asyncio.to_thread(
            record_model_performance,
            loaded_model,
            gpu_info.name,
            gpu_info.gpu_backend,
            live_tps,
            model_id=signature.get("model_id"),
            gguf=signature.get("gguf"),
            quantization=signature.get("quantization"),
            architecture=signature.get("architecture"),
            context_length=signature.get("context_length"),
            decode_read_mb=signature.get("decode_read_mb"),
            vram_total_mb=signature.get("vram_total_mb"),
            os_name=signature.get("os"),
            flags=signature.get("flags"),
        )
    payload["odsMode"] = ODS_MODE_EFFECTIVE
    payload["configuredMode"] = await asyncio.to_thread(_configured_ods_mode)
    payload["llmBackend"] = LLM_BACKEND or "unknown"
    if LLM_BACKEND == "external":
        # API mode: name the model and the API's host so the page can say
        # what serves chat. The installer refuses URLs with credentials.
        payload["externalModel"] = read_env_value("EXTERNAL_LLM_MODEL", INSTALL_DIR).strip() or None
        payload["externalHost"] = _external_api_host(read_env_value("EXTERNAL_LLM_URL", INSTALL_DIR))
    payload["hostRuntime"] = _windows_hosted_runtime()
    if payload["hostRuntime"]:
        payload["modelManagement"] = await asyncio.to_thread(_model_management)
    payload["activationReadyModel"] = (
        payload.get("currentModel")
        if loaded_entry
        and _activation_receipt_matches(payload.get("currentModel"), loaded_entry, loaded_model)
        else None
    )
    return payload


@router.get("/api/models/download-status")
def model_download_status(api_key: str = Depends(verify_api_key)):
    """Get current model download progress (if any)."""
    agent_status = _get_agent_model_status()
    lifecycle = _model_lifecycle_from_agent_status(agent_status)
    if agent_status and agent_status.get("status") != "idle":
        if _is_cancelled_download_status(agent_status) or _is_stale_terminal_download_status(agent_status):
            idle_status = _idle_download_status(last_terminal_status=agent_status)
            if lifecycle:
                idle_status["modelLifecycle"] = lifecycle
            return idle_status
        return agent_status

    status_path = Path(DATA_DIR) / "model-download-status.json"
    if not status_path.exists():
        bootstrap_status = _read_bootstrap_status_file()
        if _is_stale_active_bootstrap_status(bootstrap_status):
            return _stale_bootstrap_download_status(bootstrap_status)
        bootstrap_info = get_bootstrap_status()
        if not bootstrap_info.active:
            status = {"status": "idle", "active": False, "isDownloading": False}
            if lifecycle:
                status["modelLifecycle"] = lifecycle
            return status
        return {
            "status": "downloading",
            "active": True,
            "isDownloading": True,
            "model": bootstrap_info.model_name,
            "percent": bootstrap_info.percent,
            "bytesDownloaded": int((bootstrap_info.downloaded_gb or 0) * 1024**3),
            "bytesTotal": int((bootstrap_info.total_gb or 0) * 1024**3),
            "speedMbps": bootstrap_info.speed_mbps,
            "eta": bootstrap_info.eta_seconds,
        }
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if _is_cancelled_download_status(status) or _is_stale_terminal_download_status(status):
            idle_status = _idle_download_status(last_terminal_status=status)
            if lifecycle:
                idle_status["modelLifecycle"] = lifecycle
            return idle_status
        if lifecycle:
            status["modelLifecycle"] = lifecycle
        return status
    except (json.JSONDecodeError, OSError):
        status = {"status": "idle"}
        if lifecycle:
            status["modelLifecycle"] = lifecycle
        return status


def _idle_download_status(last_terminal_status: Optional[dict] = None) -> dict:
    status = {"status": "idle", "active": False, "isDownloading": False}
    if last_terminal_status:
        status["lastTerminalStatus"] = last_terminal_status
    return status


def _parse_status_updated_at(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_stale_terminal_download_status(status: Any) -> bool:
    if not isinstance(status, dict):
        return False
    key = str(status.get("status") or "").casefold()
    if key not in {"failed", "error", "cancelled", "canceled"}:
        return False
    updated_at = _parse_status_updated_at(status.get("updatedAt"))
    if not updated_at:
        return False
    age = (datetime.now(timezone.utc) - updated_at).total_seconds()
    return age > _STALE_TERMINAL_DOWNLOAD_STATUS_SECONDS


def _is_cancelled_download_status(status: Any) -> bool:
    if not isinstance(status, dict):
        return False
    key = str(status.get("status") or "").casefold()
    return key in {"cancelled", "canceled"}


def _read_bootstrap_status_file() -> Optional[dict[str, Any]]:
    status_path = Path(DATA_DIR) / "bootstrap-status.json"
    if not status_path.exists():
        return None
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return status if isinstance(status, dict) else None


def _is_stale_active_bootstrap_status(status: Any) -> bool:
    if not isinstance(status, dict):
        return False
    state = str(status.get("status") or "").casefold()
    if state not in _ACTIVE_BOOTSTRAP_STATUSES:
        return False
    updated_at = _parse_status_updated_at(status.get("updatedAt"))
    if not updated_at:
        return False
    age = (datetime.now(timezone.utc) - updated_at).total_seconds()
    return age > _STALE_ACTIVE_BOOTSTRAP_STATUS_SECONDS


def _stale_bootstrap_download_status(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "failed",
        "active": False,
        "isDownloading": False,
        "bootstrapStale": True,
        "model": status.get("model"),
        "percent": status.get("percent"),
        "bytesDownloaded": status.get("bytesDownloaded", 0),
        "bytesTotal": status.get("bytesTotal", 0),
        "speedBytesPerSec": status.get("speedBytesPerSec", 0),
        "eta": status.get("eta"),
        "updatedAt": status.get("updatedAt"),
        "error": "Bootstrap full-model upgrade appears stalled. Run ods restart to resume it.",
    }


def _bootstrap_retry_pending_error(model_name: Any) -> str:
    """Say why downloads wait on the first full model and how to retry it.

    The retry starts with the next ODS start or restart, and nothing else
    told the owner that (a user hit this on three computers with no way on).
    """
    model = str(model_name or "").strip() or "the full model"
    return (f"ODS's first download of {model} stopped before it finished, and it goes before other "
            "model downloads. Restart ODS to retry it (ods restart). The reason is in "
            "logs/model-upgrade.log in your ODS folder.")


def _bootstrap_upgrade_download_conflict() -> dict[str, Any] | None:
    """Return a lifecycle-busy payload when bootstrap upgrade owns download priority."""
    bootstrap_status = _read_bootstrap_status_file()
    if _is_stale_active_bootstrap_status(bootstrap_status):
        target = bootstrap_status.get("model") if bootstrap_status else None
        return {
            "error": _bootstrap_retry_pending_error(target),
            "code": "model_lifecycle_busy",
            "activeOperation": "bootstrap_upgrade_retry_pending",
            "activeTarget": target,
        }

    bootstrap_info = get_bootstrap_status()
    if bootstrap_info.active:
        model = str(bootstrap_info.model_name or "").strip() or "the full model"
        return {
            "error": (f"ODS is still downloading {model}, its first full model. "
                      "Other model downloads can start when it finishes."),
            "code": "model_lifecycle_busy",
            "activeOperation": "bootstrap_upgrade",
            "activeTarget": bootstrap_info.model_name,
        }

    args_path = Path(DATA_DIR) / "bootstrap-upgrade.args"
    if bootstrap_status is None or not args_path.exists():
        return None

    state = str(bootstrap_status.get("status") or "").casefold()
    model_name = str(bootstrap_status.get("model") or "").strip()
    if state not in {"failed", "error"} or not model_name:
        return None
    if "\x00" in model_name or "/" in model_name or "\\" in model_name or Path(model_name).name != model_name:
        return None

    try:
        final_path = (Path(DATA_DIR) / "models" / model_name).resolve()
        models_root = (Path(DATA_DIR) / "models").resolve()
        if not final_path.is_relative_to(models_root):
            return None
        if final_path.exists() and final_path.stat().st_size > 0:
            return None
    except OSError:
        return None

    return {
        "error": _bootstrap_retry_pending_error(model_name),
        "code": "model_lifecycle_busy",
        "activeOperation": "bootstrap_upgrade_retry_pending",
        "activeTarget": model_name,
    }


def _get_agent_model_status(timeout: int = 5) -> Optional[dict]:
    """Return host-agent-normalized model download status when reachable."""
    global _agent_model_status_cache_at, _agent_model_status_cache_value

    now = time.monotonic()
    if now - _agent_model_status_cache_at < _AGENT_MODEL_STATUS_CACHE_TTL_SECONDS:
        return _agent_model_status_cache_value

    with _agent_model_status_cache_lock:
        now = time.monotonic()
        if now - _agent_model_status_cache_at < _AGENT_MODEL_STATUS_CACHE_TTL_SECONDS:
            return _agent_model_status_cache_value

        try:
            status = request_agent_json("GET", "/v1/model/status", timeout=timeout)
        except AgentClientError:
            status = None

        _agent_model_status_cache_value = status
        _agent_model_status_cache_at = time.monotonic()
        return status


def _invalidate_agent_model_status_cache() -> None:
    """Forget the cached host-agent model status.

    Reads hold the lock across the host-agent request, so an in-flight read
    finishes (and caches) before this reset, never after it. Only the
    timestamp is reset: a lock-free reader then sees either the previous
    consistent entry or an expired one, never a torn value.
    """
    global _agent_model_status_cache_at

    with _agent_model_status_cache_lock:
        _agent_model_status_cache_at = 0.0


# Large GGUF downloads can report the row as downloaded before the host-agent
# releases the model_download lifecycle lock. Keep this finite so unrelated
# conflicts still surface, but cover observed 30s+ multipart teardown lag.
_MODEL_DOWNLOAD_BUSY_ACTIVATION_GRACE_SECONDS = 120.0
# The Pixel access monitor re-proves every ~45 s under the model lifecycle lock
# for a few seconds. A switch that lands in that window waits it out instead of
# returning 409 to a Models page that keeps waiting (Strixy, 2026-10-05).
_PIXEL_LIFECYCLE_OPERATIONS = frozenset({
    "pixel_startup_reproof", "pixel_access_mode", "pixel_open_app",
    "pixel_providers", "pixel_settings",
})
_MODEL_PIXEL_BUSY_ACTIVATION_GRACE_SECONDS = 30.0
# Short holds a download, import or delete waits out instead of refusing: the
# Pixel access re-proof above (about every 45 s; a delete that landed on one
# was refused on Strixy, 2026-10-09), and the integrity check a restarted host
# agent runs when it finds a download status left "verifying" (minutes after an
# install, Mac, 2026-10-09). This retry grace is not the whole import deadline:
# metadata preparation and the transport's other timeout phases are separate.
_DOWNLOAD_SHORT_HOLD_OPERATIONS = _PIXEL_LIFECYCLE_OPERATIONS | {"artifact_verification"}
_DOWNLOAD_SHORT_HOLD_GRACE_SECONDS = 30.0
_LIFECYCLE_BUSY_WORDS = {
    "model_download": "downloading another model",
    "artifact_verification": "checking a downloaded model file",
    "model_activation": "switching models",
    "model_delete": "deleting a model",
    "model_recovery": "restoring the previous model",
    "model_runtime": "changing the model runtime",
    "route_migration": "updating the model route",
    "system_update": "installing an update",
    "opencode_setup": "setting up OpenCode",
    "opencode_start": "starting OpenCode",
    "model_profile_recheck": "checking what the running model can do",
    **{operation: "checking Pixel" for operation in _PIXEL_LIFECYCLE_OPERATIONS},
}


def _agent_http_detail(exc: AgentHTTPError) -> Any:
    detail: Any = exc.detail
    try:
        payload = json.loads(exc.response_text)
        if isinstance(payload, dict):
            detail = payload
    except (json.JSONDecodeError, TypeError):
        pass
    return detail


def _format_gb(value: Any) -> str:
    try:
        return f"{max(int(value), 0) / (1024 ** 3):.1f} GB"
    except (TypeError, ValueError, OverflowError):
        return "an unknown amount"


def _insufficient_disk_detail(agent_detail: Any) -> dict[str, Any]:
    """Explain a host-agent disk refusal in words; no download was started."""
    detail = agent_detail if isinstance(agent_detail, dict) else {}
    required = detail.get("requiredBytes")
    free = detail.get("freeBytes")
    margin = detail.get("marginBytes")
    return {
        "code": "insufficient_disk_space",
        "message": (
            f"Not enough free disk space for this model. It needs {_format_gb(required)}, "
            f"ODS keeps {_format_gb(margin)} free, and {_format_gb(free)} is free now. "
            "Delete models you no longer use or free up space on this drive, then retry."
        ),
        "requiredBytes": required,
        "freeBytes": free,
        "marginBytes": margin,
    }


def _is_download_lifecycle_busy(detail: Any) -> bool:
    return (
        isinstance(detail, dict)
        and detail.get("code") == "model_lifecycle_busy"
        and detail.get("activeOperation") == "model_download"
    )


def _is_pixel_lifecycle_busy(detail: Any) -> bool:
    return (
        isinstance(detail, dict)
        and detail.get("code") == "model_lifecycle_busy"
        and detail.get("activeOperation") in _PIXEL_LIFECYCLE_OPERATIONS
    )


def _is_short_lifecycle_hold(detail: Any) -> bool:
    return (
        isinstance(detail, dict)
        and detail.get("code") == "model_lifecycle_busy"
        and detail.get("activeOperation") in _DOWNLOAD_SHORT_HOLD_OPERATIONS
    )


def _lifecycle_busy_detail(detail: dict[str, Any], blocked: str = "this download cannot start yet") -> dict[str, Any]:
    """A busy refusal in words; nothing was started, so retrying is safe."""
    doing = _LIFECYCLE_BUSY_WORDS.get(str(detail.get("activeOperation") or ""), "finishing another model task")
    return {
        **detail,
        "message": f"ODS is {doing} right now, so {blocked}. Try again in a minute.",
    }


def _call_agent_model(
    path: str,
    body: dict,
    timeout: float = 30,
    *,
    retry_download_busy_seconds: float = 0.0,
    retry_pixel_busy_seconds: float = 0.0,
) -> dict:
    """Call the host agent model endpoint."""
    started = time.monotonic()
    deadline = started + max(float(retry_download_busy_seconds or 0.0), 0.0)
    pixel_deadline = started + max(float(retry_pixel_busy_seconds or 0.0), 0.0)
    try:
        while True:
            try:
                return request_agent_json("POST", path, payload=body, timeout=timeout)
            except AgentHTTPError as exc:
                if exc.status_code != 409:
                    raise
                detail = _agent_http_detail(exc)
                if (
                    (retry_download_busy_seconds > 0
                     and _is_download_lifecycle_busy(detail)
                     and time.monotonic() < deadline)
                    or (retry_pixel_busy_seconds > 0
                        and _is_pixel_lifecycle_busy(detail)
                        and time.monotonic() < pixel_deadline)
                ):
                    time.sleep(0.5)
                    continue
                raise HTTPException(status_code=409, detail=detail) from exc
    except AgentHTTPError as exc:
        if exc.status_code == 409:
            raise HTTPException(status_code=409, detail=_agent_http_detail(exc)) from exc
        if exc.status_code == 400:
            raise HTTPException(status_code=400, detail=_agent_http_detail(exc)) from exc
        if exc.status_code == 507:
            raise HTTPException(
                status_code=507,
                detail=_insufficient_disk_detail(_agent_http_detail(exc)),
            ) from exc
        raise HTTPException(status_code=502, detail=exc.detail) from exc
    except AgentUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"Host agent unreachable: {exc}") from exc
    except AgentProtocolError as exc:
        raise HTTPException(status_code=502, detail=f"Invalid host agent response: {exc}") from exc


def _request_agent_download(payload: dict) -> dict:
    """Start a host-agent download; wait out short holds, refuse longer ones in words."""
    return _request_agent_waiting_short_holds("/v1/model/download", payload, "this download cannot start yet")


def _request_agent_waiting_short_holds(path: str, payload: dict, blocked: str,
                                       grace_seconds: float = _DOWNLOAD_SHORT_HOLD_GRACE_SECONDS) -> dict:
    """Call a host-agent model route; wait out short holds, refuse longer ones in words."""
    remaining = grace_seconds
    deadline = time.monotonic() + remaining
    while True:
        try:
            return _call_agent_model(path, payload, timeout=remaining)
        except HTTPException as exc:
            detail = exc.detail
            if exc.status_code != 409 or not isinstance(detail, dict) \
                    or detail.get("code") != "model_lifecycle_busy":
                raise
            remaining = deadline - time.monotonic()
            if _is_short_lifecycle_hold(detail) and remaining > 0:
                time.sleep(min(0.5, remaining))
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    continue
            raise HTTPException(status_code=409, detail=_lifecycle_busy_detail(detail, blocked)) from exc


def _find_model_in_library(model_id: str) -> Optional[dict]:
    """Look up a model by ID in the library catalog."""
    for model in _load_library():
        if model.get("id") == model_id:
            return model
    return None


def _local_gguf_filename_from_id(model_id: str) -> str | None:
    """Map a dashboard fallback model ID to a local GGUF filename."""
    token = str(model_id or "").strip()
    # A retired Lemonade id still names its GGUF for one release.
    for prefix in ("extra.", "user."):
        if token.lower().startswith(prefix):
            token = token[len(prefix):]
            break
    if not token or any(sep in token for sep in ("/", "\\", "\x00")):
        return None
    filename = token if token.lower().endswith(".gguf") else f"{token}.gguf"
    if filename.lower().endswith(".part") or Path(filename).name != filename:
        return None
    return filename


def _resolve_local_gguf_filename(model_id: str) -> str | None:
    candidate = _local_gguf_filename_from_id(model_id)
    if not candidate:
        return None

    candidate_lower = candidate.lower()
    candidate_stem = Path(candidate).stem.lower()
    exact_matches: list[Path] = []
    stem_matches: list[Path] = []
    logical_matches: list[Path] = []
    candidate_logical = _local_model_name_from_gguf(candidate).lower()
    try:
        for path in _installed_model_paths().values():
            if not _is_final_gguf_file(path):
                continue
            if path.name.lower() == candidate_lower:
                exact_matches.append(path)
            elif path.stem.lower() == candidate_stem:
                stem_matches.append(path)
            elif _local_model_name_from_gguf(path.name).lower() == candidate_logical:
                logical_matches.append(path)
    except OSError as exc:
        logger.warning("Failed to resolve local GGUF %s: %s", model_id, exc)
        return None

    matches = exact_matches or stem_matches or logical_matches
    if len(matches) == 1:
        return matches[0].name
    if len(matches) > 1:
        logger.warning("Ambiguous local GGUF model id %s matched %s", model_id, [p.name for p in matches])
    return None


def _find_local_gguf_model(model_id: str) -> Optional[dict]:
    """Return a synthetic activation record for a manually installed GGUF."""
    gguf_file = _resolve_local_gguf_filename(model_id)
    if not gguf_file:
        return None
    target = _installed_model_path(gguf_file)
    if target is None or not _is_final_gguf_file(target):
        return None

    context_length = 32768
    context_found = False
    for reader in (read_env_file_value, read_env_value):
        for key in ("CTX_SIZE", "MAX_CONTEXT"):
            try:
                value = int(reader(key, INSTALL_DIR) or 0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                context_length = value
                context_found = True
                break
        if context_found:
            break

    model_name = _local_model_name_from_gguf(gguf_file)
    return {
        "id": model_name,
        "gguf_file": gguf_file,
        "llm_model_name": model_name,
        "context_length": context_length,
        "runtime_profiles": [],
        "local": True,
    }


def _find_loadable_model(model_id: str) -> Optional[dict]:
    return _find_model_in_library(model_id) or _find_local_gguf_model(model_id)


def _find_normalized_model(model_id: str) -> Optional[dict]:
    return find_catalog_model(load_model_catalog(INSTALL_DIR), model_id, None)


def _external_api_host(url: str) -> str | None:
    """The host (and port) of the API URL, without anything else."""
    try:
        parsed = urlsplit(url.strip())
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if not host or parsed.scheme not in {"http", "https"}:
        return None
    return f"{host}:{port}" if port else host


async def _fetch_llama_loaded_model(host: str, port: int) -> str | None:
    base_url = _configured_llm_base_url(host, port)
    # A generic OpenAI-compatible server lists every model it can serve with no
    # loaded status: its first entry is not the active model.
    external_compatible = (
        LLM_BACKEND == "external"
        and os.environ.get("EXTERNAL_LLM_PROVIDER", "").strip().lower() == "openai-compatible"
    )
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(f"{base_url}/v1/models")
            resp.raise_for_status()
            data = resp.json().get("data") or []
            for model in data:
                status = model.get("status", {})
                if isinstance(status, dict) and status.get("value") == "loaded":
                    return model.get("id")
            if data and data[0].get("id") and not external_compatible:
                return data[0]["id"]
        except (httpx.HTTPError, ValueError):
            pass
        if external_compatible:
            return None

        try:
            resp = await client.get(f"{base_url}/props")
            resp.raise_for_status()
            props = resp.json()
            if props.get("model_alias"):
                return props["model_alias"]
            if props.get("model_path"):
                return Path(props["model_path"]).name
        except (httpx.HTTPError, ValueError):
            return None
    return None


def _benchmark_measurement(data: Any, wall_seconds: float) -> tuple[int, float, str]:
    """Use a matched pair of request measurements; server totals include peers."""
    data = data if isinstance(data, dict) else {}
    timings = data.get("timings")
    if isinstance(timings, dict):
        tokens = timings.get("predicted_n")
        milliseconds = timings.get("predicted_ms")
        if (type(tokens) is int and tokens > 0
                and type(milliseconds) in (int, float)
                and math.isfinite(milliseconds) and milliseconds > 0):
            return tokens, milliseconds / 1000.0, "request timings"
    usage = data.get("usage")
    tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
    if type(tokens) is int and tokens > 0:
        return tokens, wall_seconds, "request usage / wall time"
    raise HTTPException(
        status_code=502,
        detail="Benchmark completed but no valid request token count was reported; result was not saved",
    )


async def _run_current_model_benchmark(model_id: str, max_tokens: int) -> dict:
    service = SERVICES.get("llama-server")
    if not service:
        raise HTTPException(status_code=503, detail="llama-server service is not configured")
    host = service.get("host", "llama-server")
    port = int(service.get("port", 8080))

    loaded_model = await get_loaded_model()
    if not loaded_model:
        loaded_model = await _fetch_llama_loaded_model(host, port)
    if not loaded_model:
        loaded_model = _read_active_model() or read_env_value("LLM_MODEL", INSTALL_DIR)
    if not loaded_model:
        raise HTTPException(status_code=503, detail="llama-server is not reporting a loaded model")

    gpu_info = await asyncio.to_thread(get_gpu_info)
    context_size = await get_llama_context_size(model_hint=loaded_model)
    metrics = await _await_or_default(
        get_llama_metrics(model_hint=loaded_model),
        {"tokens_per_second": 0},
        "llama metrics",
    )
    payload = await asyncio.to_thread(
        build_models_payload,
        gpu_info,
        loaded_model,
        _newly_measured_tps(metrics, loaded_model),
        INSTALL_DIR,
        DATA_DIR,
        context_size,
        catalog=_load_library(),
        downloaded_files_override=_installed_model_paths(),
    )
    target = next((m for m in payload["models"] if m["id"] == model_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Unknown model")
    if target["status"] != "loaded":
        raise HTTPException(status_code=409, detail="Load the model before benchmarking it")

    max_tokens = max(32, min(int(max_tokens or 128), 512))
    prompt = (
        "You are benchmarking local inference. Write a concise technical explanation "
        "of why local LLM throughput depends on model size, quantization, backend, "
        "context length, and GPU memory bandwidth. Continue until the token budget ends."
    )

    # A Windows-hosted llama-server requires its key, which only LiteLLM
    # holds; every other runtime is measured directly.
    url, headers = f"http://{host}:{port}/v1/chat/completions", None
    if read_env_value("AMD_INFERENCE_LOCATION", INSTALL_DIR).strip().lower() == "host":
        gateway_key = read_env_value("LITELLM_KEY", INSTALL_DIR) or read_env_value("LITELLM_MASTER_KEY", INSTALL_DIR)
        url = "http://litellm:4000/v1/chat/completions"
        headers = {"Authorization": f"Bearer {gateway_key}"} if gateway_key else None
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=max(60.0, max_tokens * 3.0)) as client:
        resp = await client.post(
            url,
            json={
                "model": loaded_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": max_tokens,
                "stream": False,
            },
            headers=headers,
        )
        resp.raise_for_status()
        response_data = resp.json()
    wall_seconds = max(time.perf_counter() - started, 0.001)
    generated, generate_seconds, method = _benchmark_measurement(response_data, wall_seconds)

    tokens_per_second = round(generated / generate_seconds, 2)
    if not is_plausible_single_request_tps(tokens_per_second):
        raise HTTPException(
            status_code=502,
            detail="Benchmark returned implausible single-request throughput; result was not saved",
        )
    if gpu_info:
        gguf_path = _installed_model_path(target["gguf"]) if target.get("gguf") else None
        signature = build_sample_signature(target, gpu_info, context_size, INSTALL_DIR, gguf_path)
        for sample_name in {model_id, loaded_model, target.get("gguf") or "", target.get("llmModelName") or ""}:
            if not sample_name:
                continue
            await asyncio.to_thread(
                record_model_performance,
                sample_name,
                gpu_info.name,
                gpu_info.gpu_backend,
                tokens_per_second,
                model_id=signature.get("model_id"),
                gguf=signature.get("gguf"),
                quantization=signature.get("quantization"),
                architecture=signature.get("architecture"),
                context_length=signature.get("context_length"),
                decode_read_mb=signature.get("decode_read_mb"),
                vram_total_mb=signature.get("vram_total_mb"),
                os_name=signature.get("os"),
                flags=signature.get("flags"),
                source="local_benchmark",
            )

    return {
        "model": model_id,
        "loadedModel": loaded_model,
        "contextLength": context_size or target.get("contextLength"),
        "tokensPerSecond": tokens_per_second,
        "generatedTokens": int(generated),
        "generateSeconds": round(generate_seconds, 3),
        "wallSeconds": round(wall_seconds, 3),
        "source": "local_benchmark",
        "method": method,
    }


@router.post("/api/models/{model_id}/download")
def download_model(model_id: str, api_key: str = Depends(verify_api_key)):
    """Start downloading a model from HuggingFace."""
    model = _find_model_in_library(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found in library")

    bootstrap_conflict = _bootstrap_upgrade_download_conflict()
    if bootstrap_conflict is not None:
        raise HTTPException(
            status_code=409,
            detail={**bootstrap_conflict, "requestedModelId": model_id},
        )

    # Split-file models provide gguf_parts; imports may carry a vision projector.
    return _request_agent_download(_hf_download_payload(model))


@router.post("/api/models/download/cancel")
def cancel_download(api_key: str = Depends(verify_api_key)):
    """Cancel an in-progress model download."""
    result = _call_agent_model("/v1/model/download/cancel", {})
    return result


def _model_recovery_projection(value):
    phases = {'idle', 'completed', 'prepared', 'held', 'applying', 'applied', 'committing', 'rolling-back', 'unavailable'}
    if (type(value) is not dict or type(value.get('pending')) is not bool
            or value.get('phase') not in phases
            or value['pending'] != (value['phase'] not in ('idle', 'completed'))):
        raise ValueError('invalid-model-recovery')
    transaction = value.get('transactionId')
    if (transaction is not None and (type(transaction) is not str or re.fullmatch('[a-f0-9]{64}', transaction) is None)
            or value['phase'] not in ('idle', 'unavailable') and transaction is None):
        raise ValueError('invalid-model-recovery')
    result = {'pending': value['pending'], 'phase': value['phase'], 'transactionId': transaction}
    if value.get('outcome') in ('commit', 'rollback') and value['phase'] == 'completed':
        result['outcome'] = value['outcome']
    if value.get('reason') in ('model-recovery-proof-required', 'model-recovery-unavailable'):
        result['reason'] = value['reason']
    # The agent offers the owner a release without the live proof only for a
    # switch that changed nothing (fleet row 27).
    if value['pending'] and type(value.get('releasable')) is bool:
        result['releasable'] = value['releasable']
    return result


_RECOVERY_REQUESTS: tuple[dict[str, bool], ...] = ({}, {'releaseUnverified': True})


def _model_recovery_request(method, body=None):
    try:
        value = request_agent_json(method, '/v1/model/recovery' if method == 'GET' else '/v1/model/recover',
                                   payload=None if method == 'GET' else body, timeout=5 if method == 'GET' else 400)
        return _model_recovery_projection(value)
    except AgentHTTPError as exc:
        if exc.status_code in (409, 503):
            try:
                return JSONResponse(_model_recovery_projection(_agent_http_detail(exc)), status_code=exc.status_code,
                                    headers={'Cache-Control': 'no-store'})
            except ValueError:
                pass
        raise HTTPException(status_code=503, detail='Model recovery is unavailable. No new model switch was started.') from None
    except (AgentClientError, ValueError):
        raise HTTPException(status_code=503, detail='Model recovery could not be confirmed. Refresh before retrying.') from None


@router.get('/api/models/recovery')
def model_recovery_status(api_key: str = Depends(verify_api_key)):
    value = _model_recovery_request('GET')
    return value if isinstance(value, JSONResponse) else JSONResponse(value, headers={'Cache-Control': 'no-store'})


@router.post('/api/models/recovery')
def recover_model_switch(body: dict | None = Body(default=None), api_key: str = Depends(verify_api_key)):
    if body is None or not any(body == allowed and all(type(body[key]) is type(value) for key, value in allowed.items())
                               for allowed in _RECOVERY_REQUESTS):
        raise HTTPException(status_code=400, detail='Recovery accepts {} or {"releaseUnverified": true} only.')
    value = _model_recovery_request('POST', dict(body))
    return value if isinstance(value, JSONResponse) else JSONResponse(value, headers={'Cache-Control': 'no-store'})


_RETIRED_ADOPTION = {
    "error": "Adopting a model loaded in Lemonade was removed",
    "code": "external_lemonade_removed",
    "hint": ("ODS runs llama-server for every managed runtime. To use your own Lemonade, "
             "change its model there and select it with the installer's --external-llm-* options."),
}


@router.get('/api/models/external-observation')
def external_model_observation(api_key: str = Depends(verify_api_key)):
    """Removed with Lemonade (round F); answers 410 for one release."""
    raise HTTPException(status_code=410, detail=_RETIRED_ADOPTION)


@router.post('/api/models/external-adopt')
def adopt_external_model(api_key: str = Depends(verify_api_key)):
    """Removed with Lemonade (round F); answers 410 for one release."""
    raise HTTPException(status_code=410, detail=_RETIRED_ADOPTION)


@router.post("/api/models/runtime/{operation}")
def manage_model_runtime(operation: str, body: dict | None = Body(default=None),
                         api_key: str = Depends(verify_api_key)):
    if operation not in {"stop", "start"} or body not in (None, {}):
        raise HTTPException(status_code=400, detail="A start or stop operation with an empty body is required")
    if pixel_stream_active():
        raise HTTPException(status_code=409, detail="Stop the active Portal response before changing its runtime")
    try:
        value = _call_agent_model(f"/v1/model/runtime/{operation}", {}, timeout=1200)
    finally:
        _invalidate_agent_model_status_cache()
    expected = "started" if operation == "start" else "stopped"
    if not isinstance(value, dict) or value.get("status") != expected:
        raise HTTPException(status_code=502, detail="The runtime operation was not confirmed; refresh its status")
    return JSONResponse({"status": expected}, headers={"Cache-Control": "no-store"})


@router.post("/api/models/{model_id}/load")
def load_model(
    model_id: str,
    body: dict[str, Any] | None = Body(default=None),
    api_key: str = Depends(verify_api_key),
):
    """Activate a model — update config and restart llama-server."""
    return _activate_model(model_id, body)


def _activate_model(model_id: str, body: dict[str, Any] | None, *, chat_template_override: str | None = None):
    """Run ``model_id`` through the host agent's activation transaction.

    ``chat_template_override`` (any-model WP5) asks for the model's fixed
    chat template; such a request always restarts the runtime, even when the
    model already runs.
    """
    mode_denial = _model_activation_mode_denial(
        ODS_MODE_EFFECTIVE,
        _configured_ods_mode(),
        LLM_BACKEND,
    )
    if mode_denial is not None:
        raise HTTPException(
            status_code=409,
            detail={**mode_denial, "requestedModelId": model_id},
        )
    if _windows_hosted_runtime() and not _model_management().get("canActivate"):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "This installation cannot change the model runtime on the Windows host right now",
                "code": "external_runtime_unmanaged",
                "requestedModelId": model_id,
            },
        )

    model = _find_loadable_model(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found in library or local GGUF files")

    requested_context = _requested_activation_context(body)
    already_active, loaded_model = _already_active_model(model_id, model)
    served_context = _verified_activation_context(loaded_model) if already_active else None
    # Without an explicit context, a switch serves what the installer would
    # serve on this hardware (see _policy_activation_context), starting from
    # the context this model already runs at, or the installer's pick.
    policy_context = None
    if requested_context is None:
        if _configured_model_identity_matches(model):
            preferred = _configured_context_length()
        else:
            preferred = _recommended_model_context(model)
        policy_context = _policy_activation_context(model_id, preferred)
    # An idempotent reload keeps a running model as it is, unless it runs
    # below the Hermes floor and the floor fits: that model cannot serve
    # ODS Talk, so the reload repairs it.
    raise_below_floor = (
        requested_context is None
        and policy_context is not None
        and served_context is not None
        and served_context < HERMES_MIN_CONTEXT <= policy_context
    )
    if chat_template_override is None and already_active and not raise_below_floor and (
        requested_context is None
        or requested_context == served_context
    ):
        response: dict[str, Any] = {
            "status": "already_active",
            "model_id": model_id,
            "loadedModel": loaded_model,
        }
        configured_context = _configured_context_length()
        if configured_context is not None:
            response["context_length"] = configured_context
        return response

    if pixel_stream_active():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "pixel_chat_active",
                "message": "Portal is working. Stop the active response before changing models.",
                "requestedModelId": model_id,
            },
        )

    bootstrap_conflict = _bootstrap_upgrade_download_conflict()
    if bootstrap_conflict is not None:
        raise HTTPException(
            status_code=409,
            detail={**bootstrap_conflict, "requestedModelId": model_id},
        )

    # Activation includes downstream synchronization and a bounded rollback.
    activation_body: dict[str, Any] = {"model_id": model_id}
    activation_context = requested_context
    if activation_context is None and policy_context is not None:
        activation_context = policy_context
    if (
        activation_context is None
        and (
            _configured_model_identity_matches(model)
            or (
                loaded_model
                and (_model_name_tokens(loaded_model) & _catalog_model_tokens(model))
            )
        )
    ):
        # A matching live backend may still require reconciliation when its
        # activation receipt is absent or stale (for example immediately after
        # bootstrap promotion).  Preserve the verified runtime context across
        # that repair.  Otherwise the host agent falls back to the catalog's
        # conservative default and can silently shrink a 64K Hermes-capable
        # runtime to 32K during an idempotent dashboard reload.
        configured_context = _configured_context_length()
        if (
            configured_context is not None
            and _MIN_MODEL_CONTEXT <= configured_context <= _MAX_MODEL_CONTEXT
        ):
            activation_context = configured_context
    if activation_context is not None:
        activation_body["context_length"] = activation_context
    if chat_template_override is not None:
        activation_body["chat_template_override"] = chat_template_override
    try:
        result = _call_agent_model(
            "/v1/model/activate",
            activation_body,
            timeout=2700,
            retry_download_busy_seconds=_MODEL_DOWNLOAD_BUSY_ACTIVATION_GRACE_SECONDS,
            retry_pixel_busy_seconds=_MODEL_PIXEL_BUSY_ACTIVATION_GRACE_SECONDS,
        )
    finally:
        # A status read cached while the activation ran still reports its
        # lifecycle as active. Drop it so the dashboard's confirming poll,
        # sent as soon as this response lands, sees the settled state.
        _invalidate_agent_model_status_cache()
    return result


@router.get("/api/models/{model_id}/profile")
async def model_profile(model_id: str, api_key: str = Depends(verify_api_key)):
    """What a model was measured to do on this machine (PLAN WP3); advisory."""
    try:
        return await asyncio.to_thread(
            request_agent_json, "GET", "/v1/model/profile", params={"model": model_id}, timeout=10,
        )
    except AgentHTTPError as exc:
        raise HTTPException(status_code=502, detail=_agent_http_detail(exc)) from exc
    except AgentClientError as exc:
        raise HTTPException(status_code=503, detail=f"Host agent unreachable: {exc}") from exc


# The probe battery's own budget (120 s) plus the runtime's /props read.
_MODEL_PROFILE_RECHECK_TIMEOUT_SECONDS = 180


@router.post("/api/models/{model_id}/profile/recheck")
def recheck_model_profile(model_id: str, api_key: str = Depends(verify_api_key)):
    """Measure the running model again, ignoring its stored profile."""
    return _call_agent_model(
        "/v1/model/profile/recheck", {"model": model_id}, timeout=_MODEL_PROFILE_RECHECK_TIMEOUT_SECONDS,
    )


# A fixed chat template's id, as config/chat-templates/index.json spells it.
_CHAT_TEMPLATE_ID_RE = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?")


def _chat_template_unavailable_reason() -> str | None:
    """Why this runtime cannot load a fixed chat template, or None when it can."""
    mode = read_live_env_values(("AMD_INFERENCE_RUNTIME_MODE",)).get("AMD_INFERENCE_RUNTIME_MODE")
    if str(mode or "").strip().casefold() in _LEGACY_WINDOWS_NATIVE_MODES or _windows_hosted_runtime():
        return "The Windows model runtime on this machine cannot use a fixed chat template yet. Nothing was changed."
    return None


@router.post("/api/models/{model_id}/chat-template")
def run_with_fixed_chat_template(
    model_id: str,
    body: dict[str, Any] | None = Body(default=None),
    api_key: str = Depends(verify_api_key),
):
    """Run a model with ODS's fixed chat template for it (any-model WP5).

    The host agent accepts the template only when its index fixes exactly the
    template this model was measured with. A running model keeps its context.
    """
    override = body.get("override") if isinstance(body, dict) and set(body) == {"override"} else None
    if not isinstance(override, str) or not _CHAT_TEMPLATE_ID_RE.fullmatch(override):
        raise HTTPException(status_code=400, detail='Ask for a fixed chat template as {"override": "<id>"}.')
    reason = _chat_template_unavailable_reason()
    if reason is not None:
        raise HTTPException(status_code=409, detail={
            "code": "chat_template_override_unsupported", "message": reason, "requestedModelId": model_id,
        })
    model = _find_loadable_model(model_id)
    activation = None
    if model is not None and _configured_model_identity_matches(model):
        configured = _configured_context_length()
        if configured is not None and _MIN_MODEL_CONTEXT <= configured <= _MAX_MODEL_CONTEXT:
            activation = {"context_length": configured}
    return _activate_model(model_id, activation, chat_template_override=override)


@router.post("/api/models/{model_id}/benchmark")
async def benchmark_model(model_id: str, body: dict[str, Any] | None = None, api_key: str = Depends(verify_api_key)):
    """Benchmark only the currently loaded model on this machine."""
    max_tokens = 128
    if isinstance(body, dict) and body.get("max_tokens"):
        try:
            max_tokens = int(body["max_tokens"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="max_tokens must be an integer")
    try:
        return await _run_current_model_benchmark(model_id, max_tokens)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"llama-server benchmark request failed: HTTP {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"llama-server is not reachable for benchmark: {exc}") from exc


@router.delete("/api/models/{model_id}")
def delete_model(model_id: str, api_key: str = Depends(verify_api_key)):
    """Delete a downloaded model file."""
    model = _find_model_in_library(model_id) or _find_local_gguf_model(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found in library or local GGUF files")

    payload = {
        "gguf_file": model["gguf_file"],
    }
    if model.get("gguf_parts"):
        payload["gguf_parts"] = model["gguf_parts"]
    # 20 s leaves the delete itself inside the Models page's 35 s deadline.
    return _request_agent_waiting_short_holds("/v1/model/delete", payload, "this model cannot be deleted yet",
                                              grace_seconds=20.0)
