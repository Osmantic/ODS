"""Model Library router — browse, benchmark, and manage GGUF models."""

import asyncio
import hashlib
import json
import logging
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
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from config import (
    DATA_DIR,
    INSTALL_DIR,
    LLM_BACKEND,
    LOCAL_MODEL_MODES,
    ODS_MODE_EFFECTIVE,
    SERVICES,
    normalize_ods_mode,
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
from performance_oracle import (
    build_models_payload,
    build_sample_signature,
    current_model_matches,
    find_catalog_model,
    load_model_catalog,
    model_files_dir,
    read_env_file_value,
    read_env_value,
)
from security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["models"])

_LIBRARY_PATH = Path(INSTALL_DIR) / "config" / "model-library.json"
_MODELS_DIR = Path(DATA_DIR) / "models"
_ENV_PATH = Path(INSTALL_DIR) / ".env"
_HF_API_BASE = "https://huggingface.co"
_HF_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_HF_AUTHOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_HF_AVATAR_HOSTS = {"huggingface.co", "cdn-avatars.huggingface.co"}
_HF_SPLIT_GGUF_RE = re.compile(r"^(?P<prefix>.+)-(?P<part>\d{5})-of-(?P<total>\d{5})\.gguf$", re.IGNORECASE)
_HF_QUANT_RE = re.compile(
    r"(?:^|[-_.])(?P<quant>(?:IQ\d(?:_[A-Z0-9]+)+|Q\d(?:_[A-Z0-9]+)+|BF16|F16|F32))(?:[-_.]|$)",
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
_MODEL_DISCOVERY_TIMEOUT_SECONDS = float(os.environ.get("DASHBOARD_MODEL_DISCOVERY_TIMEOUT", "15.0"))
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
    return normalize_ods_mode(read_env_file_value("ODS_MODE", INSTALL_DIR))


def _model_activation_mode_denial(
    effective_mode: str,
    configured_mode: str,
) -> dict[str, str] | None:
    """Describe why this runtime cannot safely perform a local model swap."""
    effective_mode = normalize_ods_mode(effective_mode)
    configured_mode = normalize_ods_mode(configured_mode)
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
    downloaded: dict[str, int] = {}
    if not _MODELS_DIR.is_dir():
        return downloaded
    try:
        for f in _MODELS_DIR.iterdir():
            if _is_final_gguf_file(f):
                try:
                    downloaded[f.name] = f.stat().st_size
                except OSError:
                    pass
    except OSError as exc:
        logger.warning("Failed to scan models directory: %s", exc)
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
                return line.split("=", 1)[1].strip().strip('"').strip("'")
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
    for key in ("LLM_URL", "LLM_API_URL", "OLLAMA_URL"):
        value = read_env_value(key, INSTALL_DIR)
        if value:
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
    if lower.startswith("extra."):
        tokens.add(lower[6:])
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
    api_prefix = "/api/v1" if LLM_BACKEND == "lemonade" else "/v1"
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_fetch_llama_loaded_model(host, port, api_prefix))
    except (httpx.HTTPError, OSError, RuntimeError, ValueError):
        return None
    finally:
        loop.close()


async def _probe_loaded_lemonade_model(model_name: str) -> bool:
    service = SERVICES.get("llama-server", {})
    host = service.get("host", "llama-server")
    port = int(service.get("port", 8080))
    base_url = _configured_llm_base_url(host, port)
    headers = {}
    api_key = read_env_value("LEMONADE_API_KEY", INSTALL_DIR) or "lemonade"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "temperature": 0,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(f"{base_url}/api/v1/chat/completions", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            return False
        if isinstance(data.get("error"), dict):
            return False
        return bool(data.get("choices"))


def _loaded_model_backend_ready_sync(loaded_model: str | None) -> bool:
    if not loaded_model:
        return False
    if LLM_BACKEND != "lemonade":
        return True
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_probe_loaded_lemonade_model(loaded_model))
    except (httpx.HTTPError, OSError, RuntimeError, ValueError):
        return False
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


def _already_active_model(model_id: str, model: dict) -> tuple[bool, str | None]:
    gguf_file = model.get("gguf_file")
    if not gguf_file:
        return False, None
    if _read_active_model() != gguf_file:
        return False, None
    configured_llm = (
        read_env_file_value("LLM_MODEL", INSTALL_DIR)
        or read_env_value("LLM_MODEL", INSTALL_DIR)
    )
    if not (_model_name_tokens(configured_llm) & _catalog_model_tokens(model)):
        return False, None
    if not (Path(DATA_DIR) / "models" / gguf_file).exists():
        return False, None

    loaded_model = _fetch_loaded_model_sync()
    if _model_name_tokens(loaded_model) & _catalog_model_tokens(model):
        # Lemonade's health endpoint is the authoritative loaded-model source.
        # A one-token chat probe against a large already-active model can take
        # longer than dashboard/UI clients will wait, which turns an idempotent
        # Run click into an unnecessary activation.
        if (
            _activation_receipt_matches(model_id, model, loaded_model)
            and (LLM_BACKEND == "lemonade" or _loaded_model_backend_ready_sync(loaded_model))
        ):
            return True, loaded_model
    return False, loaded_model


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


def _hf_context_length(payload: dict[str, Any]) -> tuple[int, str]:
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    text_config = config.get("text_config") if isinstance(config.get("text_config"), dict) else {}
    for source in (text_config, config):
        for key in ("max_position_embeddings", "max_sequence_length", "seq_length"):
            value = source.get(key)
            if isinstance(value, int) and 512 <= value <= 4_194_304:
                return value, "hub_config"
    return 32768, "ods_safe_default"


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


def _hf_supported_gguf_filename(filename: str) -> bool:
    basename = Path(filename).name.lower()
    if not basename.endswith(".gguf"):
        return False
    unsupported_markers = ("mmproj", "projector", "adapter", "lora", "tokenizer")
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


async def _hf_get_json(path: str, *, params: dict[str, Any] | None = None) -> tuple[Any, httpx.Headers]:
    timeout = httpx.Timeout(20.0, connect=8.0)
    response: httpx.Response | None = None
    last_error: HTTPException | None = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
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
        params={"blobs": "true"},
    )
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Hugging Face returned an invalid repository record")
    artifacts = _hf_gguf_artifacts(payload)
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
                (_MODELS_DIR / filename).is_file()
                for filename in filenames
            )
        else:
            artifact["installed"] = False
    context_length, context_source = _hf_context_length(payload)
    runtime_compatible, runtime_reason = _hf_llm_runtime_compatibility(payload)
    return {
        "id": repo_id,
        "sha": str(payload.get("sha") or ""),
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
        "authenticated": bool(_hf_token()),
        "url": f"{_HF_API_BASE}/{repo_id}",
    }


def _hf_local_filename(repo_id: str, remote_filename: str, revision: str) -> str:
    repo_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", repo_id).strip("-._")
    basename = Path(remote_filename).name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(basename).stem).strip("-._")
    digest = hashlib.sha256(
        f"{repo_id}\n{revision}\n{remote_filename}".encode("utf-8")
    ).hexdigest()[:8]
    filename = f"hf-{repo_slug}-{stem}-{digest}.gguf"
    if len(filename) > 220:
        filename = f"hf-{repo_slug[:60]}-{stem[:120]}-{digest}.gguf"
    return filename


def _hf_import_record(details: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    repo_id = details["id"]
    revision = details["sha"]
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", revision):
        raise HTTPException(status_code=502, detail="Hugging Face did not provide an immutable repository revision")
    remote_files = artifact["files"]
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
    context_length = int(details.get("contextLength") or 32768)
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


@router.post("/api/models/huggingface/import")
async def import_huggingface_model(
    body: dict[str, Any] = Body(...),
    api_key: str = Depends(verify_api_key),
):
    """Pin, register, and start one integrity-qualified Hub GGUF download."""
    repo_id = str(body.get("repoId") or "").strip()
    artifact_id = str(body.get("artifactId") or "").strip()
    if not _HF_REPO_RE.fullmatch(repo_id) or not re.fullmatch(r"[0-9a-f]{20}", artifact_id):
        raise HTTPException(status_code=400, detail="repoId and artifactId are required")
    details = await _hf_repo_details(repo_id)
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
    record = _hf_import_record(details, artifact)

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

    payload = {
        "gguf_file": record["gguf_file"],
        "gguf_url": record["gguf_url"],
        "gguf_sha256": record["gguf_sha256"],
    }
    if record.get("gguf_parts"):
        payload["gguf_parts"] = record["gguf_parts"]
    try:
        result = await asyncio.to_thread(
            _call_agent_model,
            "/v1/model/download",
            payload,
        )
    except HTTPException:
        raise
    return {
        **result,
        "modelId": record["id"],
        "repoId": repo_id,
        "artifact": artifact["label"],
        "revision": details["sha"],
    }


@router.get("/api/models", response_model=ModelLibraryResponse)
async def list_models(api_key: str = Depends(verify_api_key)):
    """List model catalog entries with source-labelled performance metadata."""
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
        api_prefix = "/api/v1" if LLM_BACKEND == "lemonade" else "/v1"
        loaded_model = await _await_or_default(
            _fetch_llama_loaded_model(host, port, api_prefix),
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
    live_tps = float(metrics.get("tokens_per_second") or 0)
    payload = await asyncio.to_thread(
        build_models_payload,
        gpu_info,
        loaded_model,
        live_tps,
        INSTALL_DIR,
        DATA_DIR,
        context_size,
        catalog=_load_library(),
        downloaded_files_override=_scan_downloaded_models(),
    )
    _annotate_model_lifecycle(
        payload,
        _model_lifecycle_from_agent_status(agent_status),
    )
    if gpu_info and loaded_model and live_tps > 0:
        loaded_entry = next((m for m in payload["models"] if m["status"] == "loaded"), None) or {}
        signature = build_sample_signature(
            loaded_entry or {"id": loaded_model, "gguf": _read_active_model()},
            gpu_info,
            context_size,
            INSTALL_DIR,
            model_files_dir(DATA_DIR) / loaded_entry["gguf"] if loaded_entry.get("gguf") else None,
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
    payload["configuredMode"] = _configured_ods_mode()
    loaded_entry = next((model for model in payload["models"] if model["status"] == "loaded"), None)
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


def _bootstrap_upgrade_download_conflict() -> dict[str, Any] | None:
    """Return a lifecycle-busy payload when bootstrap upgrade owns download priority."""
    bootstrap_status = _read_bootstrap_status_file()
    if _is_stale_active_bootstrap_status(bootstrap_status):
        return {
            "error": "Cannot start model download while bootstrap full-model upgrade is pending retry",
            "code": "model_lifecycle_busy",
            "activeOperation": "bootstrap_upgrade_retry_pending",
            "activeTarget": bootstrap_status.get("model") if bootstrap_status else None,
        }

    bootstrap_info = get_bootstrap_status()
    if bootstrap_info.active:
        return {
            "error": "Cannot start model download while bootstrap full-model upgrade is in progress",
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
        "error": "Cannot start model download while bootstrap full-model upgrade is pending retry",
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


# Large GGUF downloads can report the row as downloaded before the host-agent
# releases the model_download lifecycle lock. Keep this finite so unrelated
# conflicts still surface, but cover observed 30s+ multipart teardown lag.
_MODEL_DOWNLOAD_BUSY_ACTIVATION_GRACE_SECONDS = 120.0


def _agent_http_detail(exc: AgentHTTPError) -> Any:
    detail: Any = exc.detail
    try:
        payload = json.loads(exc.response_text)
        if isinstance(payload, dict):
            detail = payload
    except (json.JSONDecodeError, TypeError):
        pass
    return detail


def _is_download_lifecycle_busy(detail: Any) -> bool:
    return (
        isinstance(detail, dict)
        and detail.get("code") == "model_lifecycle_busy"
        and detail.get("activeOperation") == "model_download"
    )


def _call_agent_model(
    path: str,
    body: dict,
    timeout: int = 30,
    *,
    retry_download_busy_seconds: float = 0.0,
) -> dict:
    """Call the host agent model endpoint."""
    deadline = time.monotonic() + max(float(retry_download_busy_seconds or 0.0), 0.0)
    try:
        while True:
            try:
                return request_agent_json("POST", path, payload=body, timeout=timeout)
            except AgentHTTPError as exc:
                if exc.status_code != 409:
                    raise
                detail = _agent_http_detail(exc)
                if (
                    retry_download_busy_seconds > 0
                    and _is_download_lifecycle_busy(detail)
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.5)
                    continue
                raise HTTPException(status_code=409, detail=detail) from exc
    except AgentHTTPError as exc:
        if exc.status_code == 409:
            raise HTTPException(status_code=409, detail=_agent_http_detail(exc)) from exc
        raise HTTPException(status_code=502, detail=exc.detail) from exc
    except AgentUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"Host agent unreachable: {exc}") from exc
    except AgentProtocolError as exc:
        raise HTTPException(status_code=502, detail=f"Invalid host agent response: {exc}") from exc


def _find_model_in_library(model_id: str) -> Optional[dict]:
    """Look up a model by ID in the library catalog."""
    for model in _load_library():
        if model.get("id") == model_id:
            return model
    return None


def _local_gguf_filename_from_id(model_id: str) -> str | None:
    """Map a dashboard fallback model ID to a local GGUF filename."""
    token = str(model_id or "").strip()
    if token.lower().startswith("extra."):
        token = token[6:]
    if not token or any(sep in token for sep in ("/", "\\", "\x00")):
        return None
    filename = token if token.lower().endswith(".gguf") else f"{token}.gguf"
    if filename.lower().endswith(".part") or Path(filename).name != filename:
        return None
    return filename


def _resolve_local_gguf_filename(model_id: str) -> str | None:
    candidate = _local_gguf_filename_from_id(model_id)
    if not candidate or not _MODELS_DIR.is_dir():
        return None

    candidate_lower = candidate.lower()
    candidate_stem = Path(candidate).stem.lower()
    exact_matches: list[Path] = []
    stem_matches: list[Path] = []
    logical_matches: list[Path] = []
    candidate_logical = _local_model_name_from_gguf(candidate).lower()
    try:
        for path in _MODELS_DIR.iterdir():
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
    models_dir = _MODELS_DIR.resolve()
    target = (_MODELS_DIR / gguf_file).resolve()
    if not target.is_relative_to(models_dir) or not _is_final_gguf_file(target):
        return None

    context_length = 32768
    for key in ("MAX_CONTEXT", "CTX_SIZE"):
        try:
            value = int(
                read_env_file_value(key, INSTALL_DIR)
                or read_env_value(key, INSTALL_DIR)
                or 0
            )
        except (TypeError, ValueError):
            continue
        if value > 0:
            context_length = value
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


def _parse_llama_metric_counters(text: str) -> dict:
    counters = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        try:
            value = float(parts[-1])
        except ValueError:
            continue
        if "tokens_predicted_total" in name:
            counters["tokens_predicted_total"] = value
        elif "tokens_predicted_seconds_total" in name:
            counters["tokens_predicted_seconds_total"] = value
    return counters


async def _fetch_llama_counters(host: str, port: int, model_name: str) -> dict:
    metrics_port = int(read_env_value("LLAMA_METRICS_PORT", INSTALL_DIR) or port)
    params = {"model": model_name} if model_name else {}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"http://{host}:{metrics_port}/metrics", params=params)
        resp.raise_for_status()
        return _parse_llama_metric_counters(resp.text)


async def _fetch_llama_loaded_model(host: str, port: int, api_prefix: str) -> str | None:
    base_url = _configured_llm_base_url(host, port)
    lemonade_api = api_prefix == "/api/v1"
    async with httpx.AsyncClient(timeout=10.0) as client:
        if lemonade_api:
            try:
                resp = await client.get(f"{base_url}{api_prefix}/health")
                resp.raise_for_status()
                health = resp.json()
                loaded = health.get("model_loaded")
                if loaded:
                    return loaded
                if "model_loaded" in health:
                    return None
            except (httpx.HTTPError, ValueError):
                pass

        try:
            resp = await client.get(f"{base_url}{api_prefix}/models")
            resp.raise_for_status()
            data = resp.json().get("data") or []
            for model in data:
                status = model.get("status", {})
                if isinstance(status, dict) and status.get("value") == "loaded":
                    return model.get("id")
            if lemonade_api:
                return None
            if data and data[0].get("id"):
                return data[0]["id"]
        except (httpx.HTTPError, ValueError):
            pass

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


def _completion_text_and_usage(data: dict) -> tuple[str, int]:
    if not isinstance(data, dict):
        return "", 0
    usage = data.get("usage") or {}
    completion_tokens = int(usage.get("completion_tokens") or 0)
    choices = data.get("choices") or []
    text = ""
    if choices:
        first = choices[0] or {}
        message = first.get("message") or {}
        text = first.get("text") or message.get("content") or ""
    if completion_tokens <= 0 and text:
        completion_tokens = max(len(text.split()), 1)
    return text, completion_tokens


async def _run_current_model_benchmark(model_id: str, max_tokens: int) -> dict:
    service = SERVICES.get("llama-server")
    if not service:
        raise HTTPException(status_code=503, detail="llama-server service is not configured")
    host = service.get("host", "llama-server")
    port = int(service.get("port", 8080))
    api_prefix = "/api/v1" if LLM_BACKEND == "lemonade" else "/v1"

    loaded_model = await get_loaded_model()
    if not loaded_model:
        loaded_model = await _fetch_llama_loaded_model(host, port, api_prefix)
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
        float(metrics.get("tokens_per_second") or 0),
        INSTALL_DIR,
        DATA_DIR,
        context_size,
        catalog=_load_library(),
        downloaded_files_override=_scan_downloaded_models(),
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

    before = {}
    try:
        before = await _fetch_llama_counters(host, port, loaded_model)
    except httpx.HTTPError as exc:
        logger.debug("Benchmark metrics pre-read failed: %s", exc)
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=max(60.0, max_tokens * 3.0)) as client:
        resp = await client.post(
            f"http://{host}:{port}{api_prefix}/chat/completions",
            json={
                "model": loaded_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": max_tokens,
                "stream": False,
            },
        )
        resp.raise_for_status()
        response_data = resp.json()
    wall_seconds = max(time.perf_counter() - started, 0.001)
    after = {}
    try:
        after = await _fetch_llama_counters(host, port, loaded_model)
    except httpx.HTTPError as exc:
        logger.debug("Benchmark metrics post-read failed: %s", exc)

    generated = after.get("tokens_predicted_total", 0) - before.get("tokens_predicted_total", 0)
    generate_seconds = after.get("tokens_predicted_seconds_total", 0) - before.get("tokens_predicted_seconds_total", 0)
    _, fallback_tokens = _completion_text_and_usage(response_data)
    timings = response_data.get("timings") if isinstance(response_data, dict) else {}
    if generated <= 0 and isinstance(timings, dict):
        generated = int(timings.get("predicted_n") or 0)
    if generate_seconds <= 0 and isinstance(timings, dict):
        timing_ms = float(timings.get("predicted_ms") or 0)
        generate_seconds = timing_ms / 1000.0 if timing_ms > 0 else 0
    if generated <= 0:
        generated = fallback_tokens
    if generate_seconds <= 0:
        generate_seconds = wall_seconds
    if generated <= 0:
        raise HTTPException(status_code=502, detail="Benchmark completed but no generated token count was reported")

    tokens_per_second = round(generated / generate_seconds, 2)
    if not is_plausible_single_request_tps(tokens_per_second):
        raise HTTPException(
            status_code=502,
            detail="Benchmark returned implausible single-request throughput; result was not saved",
        )
    if gpu_info:
        gguf_path = model_files_dir(DATA_DIR) / target["gguf"] if target.get("gguf") else None
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
        "method": "llama-server OpenAI chat completion + Prometheus counters",
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

    payload = {
        "gguf_file": model["gguf_file"],
        "gguf_url": model.get("gguf_url", ""),
        "gguf_sha256": model.get("gguf_sha256", ""),
    }
    # Split-file models provide gguf_parts array
    if model.get("gguf_parts"):
        payload["gguf_parts"] = model["gguf_parts"]

    result = _call_agent_model("/v1/model/download", payload)
    return result


@router.post("/api/models/download/cancel")
def cancel_download(api_key: str = Depends(verify_api_key)):
    """Cancel an in-progress model download."""
    result = _call_agent_model("/v1/model/download/cancel", {})
    return result


@router.post("/api/models/{model_id}/load")
def load_model(model_id: str, api_key: str = Depends(verify_api_key)):
    """Activate a model — update config and restart llama-server."""
    mode_denial = _model_activation_mode_denial(
        ODS_MODE_EFFECTIVE,
        _configured_ods_mode(),
    )
    if mode_denial is not None:
        raise HTTPException(
            status_code=409,
            detail={**mode_denial, "requestedModelId": model_id},
        )

    model = _find_loadable_model(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found in library or local GGUF files")

    already_active, loaded_model = _already_active_model(model_id, model)
    if already_active:
        return {"status": "already_active", "model_id": model_id, "loadedModel": loaded_model}

    bootstrap_conflict = _bootstrap_upgrade_download_conflict()
    if bootstrap_conflict is not None:
        raise HTTPException(
            status_code=409,
            detail={**bootstrap_conflict, "requestedModelId": model_id},
        )

    # Activation includes downstream synchronization and a bounded rollback.
    result = _call_agent_model(
        "/v1/model/activate",
        {"model_id": model_id},
        timeout=2700,
        retry_download_busy_seconds=_MODEL_DOWNLOAD_BUSY_ACTIVATION_GRACE_SECONDS,
    )
    return result


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
    result = _call_agent_model("/v1/model/delete", payload)
    return result
