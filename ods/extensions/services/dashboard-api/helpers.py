"""Shared helper functions for service health checking, metrics, and system info."""

import asyncio
import json
import logging
import os
import platform
import re
import shutil
import socket
import time
from pathlib import Path
from typing import Optional

import aiohttp
import httpx

from config import SERVICES, INSTALL_DIR, DATA_DIR, LLM_BACKEND
from host_agent_client import AgentClientError, async_request_json as request_agent_json
from models import ServiceStatus, DiskUsage, ModelInfo, BootstrapStatus


class _DirSizeCache:
    """Per-path TTL cache for dir_size_gb to avoid repeated rglob walks."""

    def __init__(self, ttl: float = 60.0):
        self._ttl = ttl
        self._store: dict[str, tuple[float, float]] = {}

    def get(self, path: Path) -> float | None:
        key = str(path.resolve())
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, path: Path, value: float):
        now = time.monotonic()
        expired_keys = [k for k, (expires_at, _) in self._store.items() if now > expires_at]
        for k in expired_keys:
            del self._store[k]
        key = str(path.resolve())
        if len(self._store) >= 1000 and key not in self._store:
            oldest_key = next(iter(self._store))
            del self._store[oldest_key]
        self._store[key] = (now + self._ttl, value)

    def invalidate(self, path: Path) -> None:
        self._store.pop(str(path.resolve()), None)

    def clear(self) -> None:
        self._store.clear()


_dir_size_cache = _DirSizeCache()

# Lemonade serves at /api/v1 instead of llama.cpp's /v1
_LLM_API_PREFIX = "/api/v1" if LLM_BACKEND == "lemonade" else "/v1"

logger = logging.getLogger(__name__)

# --- Shared HTTP sessions (connection pooling) ---
# Re-using sessions avoids creating/destroying TCP connections every
# poll cycle and prevents file-descriptor exhaustion.

_aio_session: Optional[aiohttp.ClientSession] = None
_aio_session_lock: Optional[asyncio.Lock] = None
_HEALTH_TIMEOUT = aiohttp.ClientTimeout(total=30)
# Short timeout for the catalog fan-out: one slow probe must not stall the
# whole Extensions page (frontend aborts after 8 s).
_CATALOG_HEALTH_TIMEOUT = aiohttp.ClientTimeout(total=5)


def _get_aio_session_lock() -> asyncio.Lock:
    global _aio_session_lock
    if _aio_session_lock is None:
        _aio_session_lock = asyncio.Lock()
    return _aio_session_lock


async def _get_aio_session() -> aiohttp.ClientSession:
    """Return (and lazily create) a module-level aiohttp session."""
    global _aio_session
    if _aio_session is not None and not _aio_session.closed:
        return _aio_session
    async with _get_aio_session_lock():
        if _aio_session is None or _aio_session.closed:
            _aio_session = aiohttp.ClientSession(
                timeout=_HEALTH_TIMEOUT,
                connector=aiohttp.TCPConnector(family=socket.AF_INET),
            )
    return _aio_session


# Shared httpx client for llama-server requests (connection pooling)
_httpx_client: Optional[httpx.AsyncClient] = None
_httpx_client_lock: Optional[asyncio.Lock] = None


def _get_httpx_client_lock() -> asyncio.Lock:
    global _httpx_client_lock
    if _httpx_client_lock is None:
        _httpx_client_lock = asyncio.Lock()
    return _httpx_client_lock


async def _get_httpx_client() -> httpx.AsyncClient:
    """Return (and lazily create) a module-level httpx async client."""
    global _httpx_client
    if _httpx_client is not None and not _httpx_client.is_closed:
        return _httpx_client
    async with _get_httpx_client_lock():
        if _httpx_client is None or _httpx_client.is_closed:
            _httpx_client = httpx.AsyncClient(timeout=5.0)
    return _httpx_client


def _service_status_from_config(service_id: str, config: dict, status: str) -> ServiceStatus:
    return ServiceStatus(
        id=service_id, name=config["name"], port=config["port"],
        external_port=config.get("external_port", config["port"]),
        status=status, response_time_ms=None,
    )


async def _check_tailscale_health(service_id: str, config: dict) -> ServiceStatus:
    """Map the host-agent Tailscale snapshot into a service health status.

    Tailscale has no HTTP port to poll. Treat an absent container as
    not_deployed so the optional Remote Access feature does not make a fresh
    local install look degraded.
    """
    try:
        payload = await request_agent_json("GET", "/v1/tailscale/status", timeout=5)
    except AgentClientError:
        return _service_status_from_config(service_id, config, "not_deployed")

    if not payload.get("running"):
        return _service_status_from_config(service_id, config, "not_deployed")
    if payload.get("authenticated"):
        return _service_status_from_config(service_id, config, "healthy")
    return _service_status_from_config(service_id, config, "unhealthy")


async def _check_host_systemd_health(service_id: str, config: dict) -> ServiceStatus:
    """Check a host-managed service through the authenticated host-agent.

    Host-systemd services such as OpenCode usually bind to host loopback. From
    inside Docker, probing ``localhost`` checks the dashboard-api container
    instead of the real host, so ask the host-agent to prove the local port is
    open. If the proof is unavailable, fail closed so the dashboard does not
    launch users into a dead localhost URL.
    """
    port = int(config.get("health_port") or config.get("external_port") or config.get("port") or 0)
    if port <= 0:
        return _service_status_from_config(service_id, config, "not_deployed")

    try:
        payload = await request_agent_json(
            "GET",
            "/v1/host/port",
            params={"host": "127.0.0.1", "port": port},
            timeout=5,
        )
    except AgentClientError:
        return _service_status_from_config(service_id, config, "down")

    status = "healthy" if payload.get("reachable") else "not_deployed"
    return ServiceStatus(
        id=service_id,
        name=config["name"],
        port=config["port"],
        external_port=config.get("external_port", config["port"]),
        status=status,
        response_time_ms=payload.get("response_time_ms"),
    )


# --- Token Tracking ---

_TOKEN_FILE = Path(DATA_DIR) / "token_counter.json"
_PERF_FILE = Path(DATA_DIR) / "model_performance.json"
_prev_tokens = {"count": 0, "time": 0.0, "tps": 0.0}


def _update_lifetime_tokens(server_counter: float) -> int:
    """Accumulate tokens across server restarts using a persistent file."""
    data = {"lifetime": 0, "last_server_counter": 0}
    try:
        if _TOKEN_FILE.exists():
            data = json.loads(_TOKEN_FILE.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read token counter file %s: %s", _TOKEN_FILE, e)

    prev = data.get("last_server_counter", 0)
    delta = server_counter if server_counter < prev else server_counter - prev

    data["lifetime"] = int(data.get("lifetime", 0) + delta)
    data["last_server_counter"] = server_counter

    try:
        _TOKEN_FILE.write_text(json.dumps(data))
    except OSError as e:
        logger.warning("Failed to write token counter file %s: %s", _TOKEN_FILE, e)

    return data["lifetime"]


def _get_lifetime_tokens() -> int:
    try:
        return json.loads(_TOKEN_FILE.read_text()).get("lifetime", 0)
    except (json.JSONDecodeError, OSError):
        return 0


def _normalize_perf_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def _read_json_file(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.debug("Failed to read JSON file %s: %s", path, e)
    return default


def _write_json_file(path: Path, data) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as e:
        logger.debug("Failed to write JSON file %s: %s", path, e)


def _performance_key(backend: str, gpu_name: str, model_name: str,
                     context_length: Optional[int] = None,
                     gguf: Optional[str] = None,
                     vram_total_mb: Optional[int] = None) -> str:
    parts = [
        _normalize_perf_key(backend or "unknown"),
        _normalize_perf_key(gpu_name),
        _normalize_perf_key(model_name),
    ]
    if gguf:
        parts.append(_normalize_perf_key(gguf))
    if context_length:
        parts.append(f"ctx-{int(context_length)}")
    if vram_total_mb:
        parts.append(f"vram-{int(round(vram_total_mb / 1024))}gb")
    return ":".join(parts)


def record_model_performance(
    model_name: Optional[str],
    gpu_name: Optional[str],
    backend: str,
    tokens_per_second: float,
    *,
    model_id: Optional[str] = None,
    gguf: Optional[str] = None,
    quantization: Optional[str] = None,
    architecture: Optional[str] = None,
    context_length: Optional[int] = None,
    decode_read_mb: Optional[float] = None,
    vram_total_mb: Optional[int] = None,
    os_name: Optional[str] = None,
    flags: Optional[dict] = None,
    source: str = "local_metric",
) -> None:
    """Persist observed throughput for this exact machine/model pair."""
    if not model_name or not gpu_name:
        return
    try:
        tps = float(tokens_per_second)
    except (TypeError, ValueError):
        return
    if tps <= 0:
        return

    data = _read_json_file(_PERF_FILE, {"schema_version": "ods.model-performance.v1", "samples": {}})
    samples = data.setdefault("samples", {})
    key = _performance_key(backend, gpu_name, model_name, context_length, gguf, vram_total_mb)
    previous = samples.get(key, {})
    previous_avg = float(previous.get("tokens_per_second", tps))
    previous_count = int(previous.get("sample_count", 0))
    avg = (previous_avg * 0.8) + (tps * 0.2) if previous_count else tps
    samples[key] = {
        "model": model_name,
        "model_id": model_id or previous.get("model_id"),
        "gguf": gguf or previous.get("gguf"),
        "quantization": quantization or previous.get("quantization"),
        "architecture": architecture or previous.get("architecture"),
        "gpu": gpu_name,
        "backend": backend or "unknown",
        "context_length": context_length or previous.get("context_length"),
        "decode_read_mb": decode_read_mb or previous.get("decode_read_mb"),
        "vram_total_mb": vram_total_mb or previous.get("vram_total_mb"),
        "os": os_name or previous.get("os"),
        "flags": flags or previous.get("flags", {}),
        "source": source,
        "tokens_per_second": round(avg, 1),
        "last_tokens_per_second": round(tps, 1),
        "sample_count": previous_count + 1,
        "updated_at": int(time.time()),
    }
    samples[_performance_key(backend, gpu_name, model_name)] = samples[key]
    _write_json_file(_PERF_FILE, data)


def get_recorded_model_performance(
    model_name: str,
    gpu_name: str,
    backend: str,
    *,
    context_length: Optional[int] = None,
    gguf: Optional[str] = None,
    vram_total_mb: Optional[int] = None,
) -> Optional[dict]:
    data = _read_json_file(_PERF_FILE, {"samples": {}})
    keys = [
        _performance_key(backend, gpu_name, model_name, context_length, gguf, vram_total_mb),
        _performance_key(backend, gpu_name, model_name, context_length, gguf),
        _performance_key(backend, gpu_name, model_name, context_length),
        _performance_key(backend, gpu_name, model_name),
    ]
    samples = data.get("samples", {})
    sample = next((samples.get(k) for k in keys if samples.get(k)), None)
    return sample if isinstance(sample, dict) else None


def get_model_performance_samples() -> list[dict]:
    data = _read_json_file(_PERF_FILE, {"samples": {}})
    samples = data.get("samples", {})
    if not isinstance(samples, dict):
        return []
    return [sample for sample in samples.values() if isinstance(sample, dict)]


# --- LLM Metrics ---

async def get_llama_metrics(model_hint: Optional[str] = None) -> dict:
    """Get inference metrics from llama-server Prometheus /metrics endpoint.

    Accepts an optional *model_hint* so callers that already resolved the
    loaded model name can avoid a redundant HTTP round-trip.
    """
    try:
        host = SERVICES["llama-server"]["host"]
        port = SERVICES["llama-server"]["port"]
        metrics_port = int(os.environ.get("LLAMA_METRICS_PORT", port))
        model_name = model_hint if model_hint is not None else (await get_loaded_model() or "")
        url = f"http://{host}:{metrics_port}/metrics"
        params = {"model": model_name} if model_name else {}
        client = await _get_httpx_client()
        resp = await client.get(url, params=params)

        metrics = {}
        for line in resp.text.split("\n"):
            if line.startswith("#"):
                continue
            if "tokens_predicted_total" in line:
                try:
                    metrics["tokens_predicted_total"] = float(line.split()[-1])
                except (ValueError, IndexError):
                    pass
            if "tokens_predicted_seconds_total" in line:
                try:
                    metrics["tokens_predicted_seconds_total"] = float(line.split()[-1])
                except (ValueError, IndexError):
                    pass

        now = time.time()
        curr = metrics.get("tokens_predicted_total", 0)
        gen_secs = metrics.get("tokens_predicted_seconds_total", 0)
        if _prev_tokens["time"] > 0 and curr > _prev_tokens["count"]:
            delta_secs = gen_secs - _prev_tokens.get("gen_secs", 0)
            if delta_secs > 0:
                _prev_tokens["tps"] = round((curr - _prev_tokens["count"]) / delta_secs, 1)
        _prev_tokens["count"] = curr
        _prev_tokens["time"] = now
        _prev_tokens["gen_secs"] = gen_secs

        lifetime = _update_lifetime_tokens(curr)
        return {"tokens_per_second": _prev_tokens["tps"], "lifetime_tokens": lifetime}
    except (httpx.HTTPError, httpx.TimeoutException, OSError) as e:
        logger.warning(f"get_llama_metrics failed: {e}")
        return {"tokens_per_second": 0, "lifetime_tokens": _get_lifetime_tokens()}


async def get_loaded_model() -> Optional[str]:
    """Query llama-server for actually loaded model name."""
    try:
        host = SERVICES["llama-server"]["host"]
        port = SERVICES["llama-server"]["port"]
        client = await _get_httpx_client()

        # Lemonade lists ALL available models at /v1/models without a status
        # field, so the first entry is arbitrary.  The health endpoint is the
        # authoritative source for which model is actually loaded.
        if LLM_BACKEND == "lemonade":
            resp = await client.get(f"http://{host}:{port}{_LLM_API_PREFIX}/health")
            loaded = resp.json().get("model_loaded")
            return loaded if loaded else None

        # llama.cpp: /v1/models returns the loaded model with status info.
        resp = await client.get(f"http://{host}:{port}{_LLM_API_PREFIX}/models")
        models = resp.json().get("data", [])
        for m in models:
            status = m.get("status", {})
            if isinstance(status, dict) and status.get("value") == "loaded":
                return m.get("id")
        if models:
            return models[0].get("id")
    except (httpx.HTTPError, httpx.TimeoutException, ValueError) as e:
        logger.debug("get_loaded_model failed: %s", e)
    return None


async def get_llama_context_size(model_hint: Optional[str] = None) -> Optional[int]:
    """Query llama-server /props for the actual n_ctx.

    Accepts an optional *model_hint* to skip the redundant
    ``get_loaded_model()`` call when the caller already has it.
    """
    try:
        host = SERVICES["llama-server"]["host"]
        port = SERVICES["llama-server"]["port"]
        loaded = model_hint if model_hint is not None else await get_loaded_model()
        url = f"http://{host}:{port}/props"
        if loaded:
            url += f"?model={loaded}"
        client = await _get_httpx_client()
        resp = await client.get(url)
        n_ctx = resp.json().get("default_generation_settings", {}).get("n_ctx")
        return int(n_ctx) if n_ctx else None
    except (httpx.HTTPError, httpx.TimeoutException, ValueError) as e:
        logger.debug("get_llama_context_size failed: %s", e)
        return None


# --- Service Health Cache ---
# Written by background poll loop in main.py, read by API endpoints.
# Keeps health checking decoupled from request handling so slow DNS
# lookups (Docker Desktop) never block API responses.

_services_cache: Optional[list] = None  # list[ServiceStatus], set by poll loop


def _normalize_cached_service_status(status: ServiceStatus) -> ServiceStatus:
    """Avoid treating absent optional host-managed tools as broken services."""
    config = SERVICES.get(status.id, {})
    if (
        status.status == "down"
        and config.get("type") == "host-systemd"
        and not config.get("required", False)
    ):
        return ServiceStatus(
            id=status.id,
            name=status.name,
            port=status.port,
            external_port=status.external_port,
            status="not_deployed",
            response_time_ms=status.response_time_ms,
        )
    return status


def set_services_cache(statuses: list) -> None:
    """Store latest health check results (called by background poll)."""
    global _services_cache
    _services_cache = [_normalize_cached_service_status(status) for status in statuses]


def get_cached_services() -> Optional[list]:
    """Read cached health check results. Returns None if no poll has completed yet."""
    return _services_cache


# --- Service Health ---

async def check_service_health(
    service_id: str,
    config: dict,
    *,
    timeout: Optional[aiohttp.ClientTimeout] = None,
) -> ServiceStatus:
    """Check if a service is healthy by hitting its health endpoint.

    *timeout* overrides the session-level timeout for a single probe.  The
    catalog fan-out passes a shorter timeout so one slow service does not
    stall the entire Extensions page.
    """
    if config.get("type") == "host-systemd":
        return await _check_host_systemd_health(service_id, config)

    if config.get("host_network") and int(config.get("port") or 0) <= 0:
        if service_id == "tailscale":
            return await _check_tailscale_health(service_id, config)
        return _service_status_from_config(service_id, config, "not_deployed")

    host = config.get('host', 'localhost')
    health_port = config.get('health_port', config['port'])
    url = f"http://{host}:{health_port}{config['health']}"
    status = "unknown"
    response_time = None

    try:
        session = await _get_aio_session()
        start = asyncio.get_event_loop().time()
        # Send Host header so reverse-proxy services (e.g. Caddy in Baserow)
        # route the request correctly instead of returning 404.
        headers = {"Host": "localhost"}
        get_kwargs: dict = {"headers": headers}
        if timeout is not None:
            get_kwargs["timeout"] = timeout
        async with session.get(url, **get_kwargs) as resp:
            response_time = (asyncio.get_event_loop().time() - start) * 1000
            status = "healthy" if resp.status < 400 else "unhealthy"
    except asyncio.TimeoutError:
        # Service is reachable but slow — report degraded rather than down
        # to avoid false "offline" flashes during startup or heavy load.
        status = "degraded"
    except aiohttp.ClientConnectorError as e:
        if "Name or service not known" in str(e) or "nodename nor servname" in str(e):
            status = "not_deployed"
        else:
            status = "down"
    except (aiohttp.ClientError, OSError) as e:
        logger.debug(f"Health check failed for {service_id} at {url}: {e}")
        status = "down"

    return ServiceStatus(
        id=service_id, name=config["name"], port=config["port"],
        external_port=config.get("external_port", config["port"]),
        status=status, response_time_ms=round(response_time, 1) if response_time else None
    )


async def get_all_services() -> list[ServiceStatus]:
    """Get all service health statuses.

    Uses ``return_exceptions=True`` so that one misbehaving service
    cannot take down the entire status response.
    """
    tasks = [check_service_health(sid, cfg) for sid, cfg in SERVICES.items()]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    statuses: list[ServiceStatus] = []
    for (sid, cfg), result in zip(SERVICES.items(), results):
        if isinstance(result, BaseException):
            logger.warning("Health check for %s raised %s: %s", sid, type(result).__name__, result)
            statuses.append(ServiceStatus(
                id=sid, name=cfg["name"], port=cfg["port"],
                external_port=cfg.get("external_port", cfg["port"]),
                status="down", response_time_ms=None,
            ))
        else:
            statuses.append(result)
    return statuses


# --- System Metrics ---

def dir_size_gb(path: Path) -> float:
    """Calculate total size of a directory in GB. Returns 0.0 if path doesn't exist.

    Skips symlinks to avoid following links outside DATA_DIR and double-counting.
    Results are cached for 60 seconds to avoid repeated expensive rglob walks.
    """
    cached = _dir_size_cache.get(path)
    if cached is not None:
        return cached
    if not path.exists():
        _dir_size_cache.set(path, 0.0)
        return 0.0
    total = 0
    try:
        for f in path.rglob("*"):
            try:
                if f.is_symlink():
                    continue
                if f.is_file():
                    total += f.stat().st_size
            except (PermissionError, OSError):
                pass
    except (PermissionError, OSError):
        pass
    result = round(total / (1024**3), 2)
    _dir_size_cache.set(path, result)
    return result


def invalidate_dir_size_cache(path: Path):
    """Remove cached size for a specific path after it has been modified."""
    _dir_size_cache.invalidate(path)


def clear_dir_size_cache():
    """Clear the entire dir_size_gb cache (e.g. after bulk operations)."""
    _dir_size_cache.clear()


def get_disk_usage() -> DiskUsage:
    """Get disk usage for the ODS install directory."""
    path = INSTALL_DIR if os.path.exists(INSTALL_DIR) else os.path.expanduser("~")
    total, used, free = shutil.disk_usage(path)
    percent = round(used / total * 100, 1) if total > 0 else 0.0
    return DiskUsage(path=path, used_gb=round(used / (1024**3), 2), total_gb=round(total / (1024**3), 2), percent=percent)


def get_model_info() -> Optional[ModelInfo]:
    """Get current model info from .env config."""
    env_path = Path(INSTALL_DIR) / ".env"
    if env_path.exists():
        try:
            env_values = {}
            with open(env_path) as f:
                for line in f:
                    if "=" not in line or line.lstrip().startswith("#"):
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    if not key:
                        continue
                    value = value.strip()
                    # Strip exactly one matching pair of surrounding quotes.
                    # str.strip("\"'") removes any run of either quote from
                    # both ends, so a value legitimately ending in a quote is
                    # truncated and "'literal'" loses its inner quotes. Keep
                    # mismatched quotes verbatim, matching lib/safe-env.sh.
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                        value = value[1:-1]
                    env_values[key] = value

            model_name = env_values.get("LLM_MODEL")
            if model_name:
                size_gb, quant = 15.0, None
                # MAX_CONTEXT/CTX_SIZE come straight from .env and may be
                # non-numeric (e.g. "auto", "8k", or a trailing comment); fall
                # back to the default rather than 500-ing every caller. Mirrors
                # the guard already used in routers/models.py.
                try:
                    context = int(env_values.get("MAX_CONTEXT") or env_values.get("CTX_SIZE") or 32768)
                except (TypeError, ValueError):
                    context = 32768

                import re as _re

                name_lower = model_name.lower()
                if "gemma-4-e2b" in name_lower:
                    size_gb = 2.8
                elif "gemma-4-e4b" in name_lower:
                    size_gb = 5.3
                elif "gemma-4-26b" in name_lower:
                    size_gb = 18.0
                elif "gemma-4-31b" in name_lower:
                    size_gb = 19.8
                elif _re.search(r'\b2b\b', name_lower):
                    size_gb = 1.5
                elif _re.search(r'\b4b\b', name_lower):
                    size_gb = 2.8
                elif _re.search(r'\b7b\b', name_lower):
                    size_gb = 4.0
                elif _re.search(r'\b8b\b', name_lower):
                    size_gb = 4.5
                elif _re.search(r'\b9b\b', name_lower):
                    size_gb = 5.8
                elif _re.search(r'\b14b\b', name_lower):
                    size_gb = 8.0
                elif _re.search(r'\b26b\b', name_lower):
                    size_gb = 18.0
                elif _re.search(r'\b30b\b', name_lower):
                    size_gb = 18.6
                elif _re.search(r'\b31b\b', name_lower):
                    size_gb = 19.8
                elif _re.search(r'\b32b\b', name_lower):
                    size_gb = 16.0
                elif _re.search(r'\b70b\b', name_lower):
                    size_gb = 35.0

                gguf_file = env_values.get("GGUF_FILE", "").lower()
                if "awq" in name_lower:
                    quant = "AWQ"
                elif "gptq" in name_lower:
                    quant = "GPTQ"
                elif "gguf" in name_lower or gguf_file.endswith(".gguf"):
                    quant = "GGUF"

                return ModelInfo(name=model_name, size_gb=size_gb, context_length=context, quantization=quant)
        except OSError as e:
            logger.warning("Failed to read .env for model info: %s", e)
    return None


def get_bootstrap_status() -> BootstrapStatus:
    """Get bootstrap download progress if active."""
    status_file = Path(DATA_DIR) / "bootstrap-status.json"
    if not status_file.exists():
        return BootstrapStatus(active=False)

    try:
        with open(status_file) as f:
            data = json.load(f)

        status = data.get("status", "")
        if status in ("complete", "failed", "cancelled", "error"):
            return BootstrapStatus(active=False)
        if status == "" and not data.get("bytesDownloaded") and not data.get("percent"):
            return BootstrapStatus(active=False)

        # Reconcile with the filesystem only for non-active states. If the
        # target model file is already present on disk and the status is
        # non-active, the download is done enough for UI purposes. Active
        # states remain busy because config updates and the llama-server
        # hot-swap may not have finished yet; returning inactive here would
        # hide a subsequent failure.
        model_name = data.get("model")
        if model_name and status not in ("downloading", "verifying", "swapping"):
            models_dir = Path(DATA_DIR) / "models"
            model_path = (models_dir / model_name).resolve()
            if model_path.is_relative_to(models_dir.resolve()):
                try:
                    if model_path.exists() and model_path.stat().st_size > 0:
                        return BootstrapStatus(active=False)
                except OSError as e:
                    logger.debug("bootstrap reconciliation stat failed: %s", e)

        eta_str = data.get("eta", "")
        eta_seconds = None
        if eta_str and eta_str.strip() and eta_str.strip() != "calculating...":
            try:
                parts = [p.strip() for p in eta_str.replace("m", "").replace("s", "").split() if p.strip()]
                if len(parts) == 2:
                    eta_seconds = int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 1:
                    eta_seconds = int(parts[0])
            except (ValueError, IndexError):
                pass

        bytes_downloaded = data.get("bytesDownloaded", 0)
        bytes_total = data.get("bytesTotal", 0)
        speed_bps = data.get("speedBytesPerSec", 0)

        percent_raw = data.get("percent")
        percent = None
        if percent_raw is not None:
            try:
                percent = max(0.0, min(100.0, float(percent_raw)))
            except (ValueError, TypeError):
                pass
        if bytes_total and bytes_downloaded:
            bytes_downloaded = max(0, min(bytes_downloaded, bytes_total))

        return BootstrapStatus(
            active=True, model_name=data.get("model"), percent=percent,
            downloaded_gb=bytes_downloaded / (1024**3) if bytes_downloaded else None,
            total_gb=bytes_total / (1024**3) if bytes_total else None,
            speed_mbps=speed_bps / (1024**2) if speed_bps else None,
            eta_seconds=eta_seconds
        )
    except (json.JSONDecodeError, OSError, KeyError) as e:
        logger.warning("Failed to parse bootstrap status: %s", e)
        return BootstrapStatus(active=False)


def get_uptime() -> int:
    """Get system uptime in seconds (cross-platform)."""
    _system = platform.system()
    import subprocess
    try:
        if _system == "Linux":
            with open("/proc/uptime") as f:
                return int(float(f.read().split()[0]))
        elif _system == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "kern.boottime"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                # Output: "{ sec = 1234567890, usec = 0 } ..."
                import re
                match = re.search(r"sec\s*=\s*(\d+)", result.stdout)
                if match:
                    import time as _time
                    return int(_time.time()) - int(match.group(1))
        elif _system == "Windows":
            import ctypes
            return ctypes.windll.kernel32.GetTickCount64() // 1000
    except (OSError, subprocess.SubprocessError, ValueError, IndexError, AttributeError) as e:
        logger.debug("get_uptime failed on %s: %s", _system, e)
    return 0


def _get_cpu_metrics_linux() -> dict:
    """Get CPU usage from /proc/stat (Linux only)."""
    result = {"percent": 0, "temp_c": None}
    try:
        with open("/proc/stat") as f:
            line = f.readline()
        parts = line.split()
        if len(parts) >= 8:
            idle = int(parts[4]) + int(parts[5])
            total = sum(int(p) for p in parts[1:8])
            if not hasattr(get_cpu_metrics, "_prev"):
                get_cpu_metrics._prev = (idle, total)
            prev_idle, prev_total = get_cpu_metrics._prev
            d_idle, d_total = idle - prev_idle, total - prev_total
            get_cpu_metrics._prev = (idle, total)
            if d_total > 0:
                result["percent"] = round((1 - d_idle / d_total) * 100, 1)
    except OSError as e:
        logger.debug("Failed to read /proc/stat: %s", e)

    try:
        import glob
        for tz in sorted(glob.glob("/sys/class/thermal/thermal_zone*/type")):
            with open(tz) as f:
                zone_type = f.read().strip()
            if any(k in zone_type.lower() for k in ("k10temp", "coretemp", "cpu", "soc", "tctl")):
                with open(tz.replace("/type", "/temp")) as f:
                    result["temp_c"] = int(f.read().strip()) // 1000
                break
        if result["temp_c"] is None:
            for hwmon in sorted(glob.glob("/sys/class/hwmon/hwmon*/name")):
                with open(hwmon) as f:
                    name = f.read().strip()
                if name in ("k10temp", "coretemp", "zenpower"):
                    with open(hwmon.replace("/name", "/temp1_input")) as f:
                        result["temp_c"] = int(f.read().strip()) // 1000
                    break
    except OSError as e:
        logger.debug("Failed to read CPU temperature: %s", e)
    return result


def _get_cpu_metrics_darwin() -> dict:
    """Get CPU usage on macOS via host_processor_info."""
    result = {"percent": 0, "temp_c": None}
    try:
        import subprocess
        out = subprocess.run(
            ["top", "-l", "1", "-n", "0", "-stats", "cpu"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            import re
            match = re.search(r"CPU usage:\s+([\d.]+)%\s+user.*?([\d.]+)%\s+sys", out.stdout)
            if match:
                result["percent"] = round(float(match.group(1)) + float(match.group(2)), 1)
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        logger.debug("macOS CPU metrics failed: %s", e)
    return result


def get_cpu_metrics() -> dict:
    """Get CPU usage percentage and temperature (cross-platform)."""
    _system = platform.system()
    if _system == "Linux":
        return _get_cpu_metrics_linux()
    elif _system == "Darwin":
        return _get_cpu_metrics_darwin()
    return {"percent": 0, "temp_c": None}


def _get_ram_metrics_linux() -> dict:
    """Get RAM usage from /proc/meminfo (Linux only)."""
    result = {"used_gb": 0, "total_gb": 0, "percent": 0}
    try:
        meminfo = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    meminfo[parts[0].rstrip(":")] = int(parts[1])
        total = meminfo.get("MemTotal", 0)
        available = meminfo.get("MemAvailable", 0)
        used = total - available
        result["total_gb"] = round(total / (1024 * 1024), 1)
        result["used_gb"] = round(used / (1024 * 1024), 1)
        if total > 0:
            result["percent"] = round(used / total * 100, 1)
        # On Apple Silicon, override total_gb with the host's actual RAM
        host_ram_gb_str = os.environ.get("HOST_RAM_GB", "")
        gpu_backend = os.environ.get("GPU_BACKEND", "").lower()
        if gpu_backend == "apple" and host_ram_gb_str:
            try:
                host_ram_gb = float(host_ram_gb_str)
                if host_ram_gb > 0:
                    result["total_gb"] = round(host_ram_gb, 1)
                    result["percent"] = round(used / (host_ram_gb * 1024 * 1024) * 100, 1)
            except ValueError:
                pass
    except OSError as e:
        logger.debug("Failed to read /proc/meminfo: %s", e)
    return result


def _get_ram_metrics_sysctl() -> dict:
    """Get RAM usage on macOS via sysctl."""
    result = {"used_gb": 0, "total_gb": 0, "percent": 0}
    try:
        import subprocess
        out = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            total_bytes = int(out.stdout.strip())
            total_gb = total_bytes / (1024 ** 3)
            result["total_gb"] = round(total_gb, 1)
            # vm_stat for used memory
            vm = subprocess.run(
                ["vm_stat"], capture_output=True, text=True, timeout=5,
            )
            if vm.returncode == 0:
                import re
                pages = {}
                for line in vm.stdout.splitlines():
                    match = re.match(r"(.+?):\s+(\d+)", line)
                    if match:
                        pages[match.group(1).strip()] = int(match.group(2))
                page_size = 16384  # default on Apple Silicon
                ps_match = re.search(r"page size of (\d+) bytes", vm.stdout)
                if ps_match:
                    page_size = int(ps_match.group(1))
                active = pages.get("Pages active", 0)
                wired = pages.get("Pages wired down", 0)
                compressed = pages.get("Pages occupied by compressor", 0)
                used_bytes = (active + wired + compressed) * page_size
                result["used_gb"] = round(used_bytes / (1024 ** 3), 1)
                if total_bytes > 0:
                    result["percent"] = round(used_bytes / total_bytes * 100, 1)
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        logger.debug("macOS RAM metrics failed: %s", e)
    return result


def get_ram_metrics() -> dict:
    """Get RAM usage (cross-platform)."""
    _system = platform.system()
    if _system == "Linux":
        return _get_ram_metrics_linux()
    elif _system == "Darwin":
        return _get_ram_metrics_sysctl()
    return {"used_gb": 0, "total_gb": 0, "percent": 0}
