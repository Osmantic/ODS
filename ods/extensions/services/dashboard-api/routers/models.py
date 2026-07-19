"""Model Library router — browse, benchmark, and manage GGUF models."""

import asyncio
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException

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
_MODEL_DISCOVERY_TIMEOUT_SECONDS = float(os.environ.get("DASHBOARD_MODEL_DISCOVERY_TIMEOUT", "15.0"))
_AGENT_MODEL_STATUS_CACHE_TTL_SECONDS = float(
    os.environ.get("DASHBOARD_AGENT_MODEL_STATUS_CACHE_TTL", "0.5")
)
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


def _load_library() -> list[dict]:
    """Load the model library catalog from config/model-library.json."""
    if not _LIBRARY_PATH.exists():
        logger.warning("Model library not found: %s", _LIBRARY_PATH)
        return []
    try:
        data = json.loads(_LIBRARY_PATH.read_text(encoding="utf-8"))
        return data.get("models", [])
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load model library: %s", exc)
        return []


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
    tokens = {token.lower()}
    if token.lower().startswith("extra."):
        tokens.add(token[6:].lower())
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
    if (
        _model_name_tokens(loaded_model) & _catalog_model_tokens(model)
        and _loaded_model_backend_ready_sync(loaded_model)
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


@router.get("/api/models", response_model=ModelLibraryResponse)
async def list_models(api_key: str = Depends(verify_api_key)):
    """List model catalog entries with source-labelled performance metadata."""
    gpu_info, loaded_model = await asyncio.gather(
        asyncio.to_thread(get_gpu_info),
        _await_or_default(
            get_loaded_model(),
            None,
            "loaded model",
            timeout_seconds=_MODEL_DISCOVERY_TIMEOUT_SECONDS,
        ),
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
    return payload


@router.get("/api/models/download-status")
def model_download_status(api_key: str = Depends(verify_api_key)):
    """Get current model download progress (if any)."""
    agent_status = _get_agent_model_status()
    if agent_status and agent_status.get("status") != "idle":
        return agent_status

    status_path = Path(DATA_DIR) / "model-download-status.json"
    if not status_path.exists():
        bootstrap_info = get_bootstrap_status()
        if not bootstrap_info.active:
            return {"status": "idle", "active": False, "isDownloading": False}
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
        return json.loads(status_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "idle"}


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


def _call_agent_model(path: str, body: dict, timeout: int = 30) -> dict:
    """Call the host agent model endpoint."""
    try:
        return request_agent_json("POST", path, payload=body, timeout=timeout)
    except AgentHTTPError as exc:
        if exc.status_code == 409:
            detail: Any = exc.detail
            try:
                payload = json.loads(exc.response_text)
                if isinstance(payload, dict):
                    detail = payload
            except (json.JSONDecodeError, TypeError):
                pass
            raise HTTPException(status_code=409, detail=detail) from exc
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
    async with httpx.AsyncClient(timeout=10.0) as client:
        if api_prefix == "/api/v1":
            try:
                resp = await client.get(f"{base_url}{api_prefix}/health")
                resp.raise_for_status()
                loaded = resp.json().get("model_loaded")
                if loaded:
                    return loaded
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
            desired_tokens = (
                _model_name_tokens(_read_active_model())
                | _model_name_tokens(read_env_value("LLM_MODEL", INSTALL_DIR))
            )
            if api_prefix == "/api/v1" and desired_tokens:
                for model in data:
                    model_tokens = _model_name_tokens(model.get("id"))
                    model_tokens.update(_model_name_tokens(model.get("checkpoint")))
                    checkpoints = model.get("checkpoints")
                    if isinstance(checkpoints, dict):
                        for checkpoint in checkpoints.values():
                            model_tokens.update(_model_name_tokens(checkpoint))
                    if desired_tokens & model_tokens:
                        return model.get("id")
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

    # Activation includes downstream synchronization and a bounded rollback.
    result = _call_agent_model("/v1/model/activate", {"model_id": model_id}, timeout=2700)
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
