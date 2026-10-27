"""Direct native llama-server client, document extractor, and workspace tool for Windows Desktop."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import zlib
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import httpx

import config
from context_budget import (
    BudgetedContext,
    DEFAULT_CONTEXT_WINDOW,
    plan_context_budget,
)
from gpu import get_gpu_info, read_windows_user_gpu_preference

logger = logging.getLogger(__name__)

DEFAULT_NATIVE_SYSTEM_PROMPT = (
    "You are ODS, a helpful, accurate local AI assistant running natively on the user's "
    "Windows computer. Answer clearly and concisely. When the user explicitly asks you "
    "to create or save a file in their workspace, wrap the file contents in "
    '<ods_write_file path="filename.ext">...contents...</ods_write_file>.'
)

MAX_CONVERSATIONS = 200
MAX_CONVERSATION_STORE_BYTES = 16 * 1024 * 1024
MAX_WORKSPACE_FILE_BYTES = 4 * 1024 * 1024
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_DOCUMENT_CHARS = 80_000

_SAFE_RELATIVE_PATH = re.compile(r"^[A-Za-z0-9_.\-/]{1,180}$")
_WRITE_FILE_BLOCK_RE = re.compile(
    r'<ods_write_file\s+path="([^"\r\n]{1,180})"\s*>\r?\n?(.*?)</ods_write_file>',
    re.DOTALL,
)

TEXT_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".csv",
        ".tsv",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".log",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".html",
        ".css",
        ".ps1",
        ".sh",
        ".bat",
        ".cmd",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".cs",
        ".rs",
        ".go",
        ".java",
        ".sql",
        ".xml",
    }
)


def resolve_native_llm_endpoint() -> str:
    """Return the validated loopback URL for the native llama-server process."""
    raw = (
        os.environ.get("ODS_NATIVE_LLM_URL", "").strip()
        or config.read_live_env_value("ODS_NATIVE_LLM_URL").strip()
        or "http://127.0.0.1:8080"
    )
    parsed = urlparse(raw)
    if parsed.scheme != "http":
        raise ValueError(f"ODS_NATIVE_LLM_URL must use http:// loopback, got {raw!r}")
    host = (parsed.hostname or "").strip().lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(f"ODS_NATIVE_LLM_URL must target loopback, got {raw!r}")
    norm_host = "127.0.0.1" if host == "localhost" else host
    port = parsed.port or 8080
    if norm_host == "::1":
        return f"http://[::1]:{port}"
    return f"http://{norm_host}:{port}"


def resolve_native_llm_api_key() -> str:
    """Resolve the owner API key used to authenticate against native llama-server."""
    for env_key in ("ODS_NATIVE_LLM_API_KEY", "LLAMA_SERVER_API_KEY"):
        val = os.environ.get(env_key, "").strip() or config.read_live_env_value(env_key).strip()
        if val:
            return val

    candidate_paths: list[Path] = []
    if config.DATA_DIR:
        candidate_paths.append(Path(config.DATA_DIR) / "native-runtime" / "api-key")
        candidate_paths.append(Path(config.DATA_DIR) / "portal-runtime" / "api-key")

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        base = Path(local_app_data) / "ODS"
        candidate_paths.append(base / "lemonade" / "portal-runtime" / "api-key")
        candidate_paths.append(base / "native-runtime" / "api-key")

    for path in candidate_paths:
        try:
            if path.is_file():
                key = path.read_text(encoding="utf-8").strip()
                if key:
                    return key
        except OSError:
            continue
    return ""


def _native_llm_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    api_key = resolve_native_llm_api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def get_native_models_directories() -> list[Path]:
    """Return candidate directories where local .gguf models are stored."""
    dirs: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        norm = str(p).replace("/", "\\").lower()
        if norm not in seen:
            seen.add(norm)
            dirs.append(p)

    env_models_dir = os.environ.get("ODS_MODELS_DIR", "").strip()
    if env_models_dir:
        _add(Path(env_models_dir))
    if config.DATA_DIR:
        _add(Path(config.DATA_DIR) / "models")
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        _add(Path(local_app_data) / "ODS" / "models")
        _add(Path(local_app_data) / "ODS" / "lemonade" / "models")
    return dirs


def list_local_gguf_models() -> list[dict[str, Any]]:
    """List verified/present .gguf model files across native model directories."""
    models: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for directory in get_native_models_directories():
        if not directory.is_dir():
            continue
        try:
            for item in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
                if not item.is_file() or not item.name.lower().endswith(".gguf"):
                    continue
                if item.name.lower().startswith("mmproj"):
                    continue
                key = item.name.lower()
                if key in seen_names:
                    continue
                seen_names.add(key)
                stat = item.stat()
                stamp_path = Path(f"{item}.sha256.json")
                sha256 = None
                verified = False
                if stamp_path.is_file():
                    try:
                        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
                        if int(stamp.get("size", -1)) == stat.st_size:
                            sha256 = stamp.get("sha256")
                            verified = bool(sha256)
                    except (OSError, ValueError, TypeError):
                        pass
                models.append(
                    {
                        "id": item.name,
                        "filename": item.name,
                        "path": str(item),
                        "sizeBytes": stat.st_size,
                        "sizeGB": round(stat.st_size / (1024**3), 2),
                        "sha256": sha256,
                        "verified": verified,
                    }
                )
        except OSError:
            continue
    return models


async def get_native_llm_status() -> dict[str, Any]:
    """Probe the native llama-server process and return unified status."""
    endpoint = resolve_native_llm_endpoint()
    headers = _native_llm_headers()
    timeout = httpx.Timeout(connect=0.5, read=1.5, write=1.5, pool=1.5)

    healthy = False
    loaded_model: str | None = None
    context_length = DEFAULT_CONTEXT_WINDOW
    vision_supported = False
    detail = "Native llama-server is not reachable"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            health_resp = await client.get(f"{endpoint}/health", headers=headers)
            if health_resp.status_code == 200:
                healthy = True
                models_resp, props_resp = await asyncio.gather(
                    client.get(f"{endpoint}/v1/models", headers=headers),
                    client.get(f"{endpoint}/props", headers=headers),
                    return_exceptions=True,
                )
                if isinstance(models_resp, httpx.Response) and models_resp.status_code == 200:
                    data = models_resp.json().get("data", [])
                    if isinstance(data, list) and data and isinstance(data[0], dict):
                        loaded_model = str(data[0].get("id") or "").strip() or None
                if isinstance(props_resp, httpx.Response) and props_resp.status_code == 200:
                    props = props_resp.json()
                    if isinstance(props, dict):
                        default_gen = props.get("default_generation_settings")
                        if isinstance(default_gen, dict) and isinstance(default_gen.get("n_ctx"), int):
                            context_length = max(512, int(default_gen["n_ctx"]))
                        elif isinstance(props.get("n_ctx"), int):
                            context_length = max(512, int(props["n_ctx"]))
                        if not loaded_model and isinstance(props.get("model_alias"), str):
                            loaded_model = props["model_alias"].strip() or None
                        elif not loaded_model and isinstance(props.get("model_path"), str):
                            loaded_model = Path(props["model_path"]).name or None
                        modalities = props.get("modalities")
                        if isinstance(modalities, dict) and modalities.get("vision") is True:
                            vision_supported = True
                        elif bool(props.get("clip_model_path") or props.get("mmproj")):
                            vision_supported = True
                detail = (
                    f"Ready on {loaded_model} ({context_length} ctx)"
                    if loaded_model
                    else "Native llama-server is healthy"
                )
            elif health_resp.status_code == 503:
                detail = "Native llama-server is loading the model..."
    except (httpx.HTTPError, OSError, ValueError):
        healthy = False

    if not loaded_model:
        env_model = (
            os.environ.get("GGUF_FILE", "").strip()
            or config.read_live_env_value("GGUF_FILE").strip()
            or os.environ.get("LLM_MODEL", "").strip()
            or config.read_live_env_value("LLM_MODEL").strip()
        )
        if env_model and healthy:
            loaded_model = env_model

    gpu_pref = read_windows_user_gpu_preference()
    gpu_info = get_gpu_info()
    gpu_summary = None
    if gpu_info:
        gpu_summary = {
            "name": gpu_info.name,
            "backend": gpu_info.gpu_backend,
            "vramTotalMB": gpu_info.memory_total_mb,
            "vramUsedMB": gpu_info.memory_used_mb if gpu_info.memory_usage_available else None,
            "utilizationPercent": gpu_info.utilization_percent if gpu_info.utilization_available else None,
            "temperatureC": gpu_info.temperature_c if gpu_info.temperature_available else None,
            "powerW": gpu_info.power_w,
        }

    return {
        "available": healthy and bool(loaded_model),
        "healthy": healthy,
        "status": "ready" if (healthy and loaded_model) else ("loading" if healthy else "offline"),
        "model": loaded_model,
        "contextLength": context_length,
        "visionSupported": vision_supported,
        "endpoint": endpoint,
        "detail": detail,
        "gpuPreference": gpu_pref,
        "gpu": gpu_summary,
        "localModels": list_local_gguf_models(),
    }


# ============================================================================
# Document & PDF Text Extraction
# ============================================================================


def _extract_pdf_text_bytes(raw_bytes: bytes) -> str:
    """Extract readable text from a PDF file using pypdf (if available) or built-in stream parser."""
    try:
        import pypdf  # type: ignore
        import io

        reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
        joined = "\n\n".join(p.strip() for p in pages if p.strip())
        if joined:
            return joined
    except Exception:
        pass

    # Pure-Python fallback for standard PDF text objects (BT ... ET / Tj / TJ)
    # including FlateDecode compressed content streams.
    chunks: list[str] = []

    def _collect_text_ops(stream_text: str) -> None:
        for bt_block in re.findall(r"BT\b(.*?)ET\b", stream_text, flags=re.DOTALL):
            for literal in re.findall(r"\(([^()\\]*(?:\\.[^()\\]*)*)\)\s*Tj", bt_block):
                cleaned = (
                    literal.replace(r"\(", "(")
                    .replace(r"\)", ")")
                    .replace(r"\n", "\n")
                    .replace(r"\r", "\r")
                    .replace(r"\t", "\t")
                    .replace(r"\\", "\\")
                )
                if cleaned.strip():
                    chunks.append(cleaned)
            for array_body in re.findall(r"\[(.*?)\]\s*TJ", bt_block, flags=re.DOTALL):
                parts = re.findall(r"\(([^()\\]*(?:\\.[^()\\]*)*)\)", array_body)
                if parts:
                    line = "".join(
                        p.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
                        for p in parts
                    )
                    if line.strip():
                        chunks.append(line)

    # 1. Inspect uncompressed text blocks
    _collect_text_ops(raw_bytes.decode("latin-1", errors="ignore"))

    # 2. Decompress FlateDecode streams
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw_bytes, flags=re.DOTALL):
        stream_data = match.group(1)
        for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                decompressed = zlib.decompress(stream_data, wbits)
                _collect_text_ops(decompressed.decode("latin-1", errors="ignore"))
                break
            except Exception:
                continue

    return "\n".join(chunks).strip()


def extract_document_text(filename: str, raw_bytes: bytes, content_type: str = "") -> dict[str, Any]:
    """Extract bounded UTF-8 text from an uploaded document, code file, or PDF."""
    safe_name = Path(filename or "document.txt").name
    ext = Path(safe_name).suffix.lower()
    mime = (content_type or "").split(";", 1)[0].strip().lower()

    if len(raw_bytes) > MAX_DOCUMENT_BYTES:
        raise ValueError(f"Document {safe_name!r} exceeds the {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB limit")

    if ext == ".pdf" or mime == "application/pdf":
        text = _extract_pdf_text_bytes(raw_bytes)
        if not text:
            raise ValueError(f"Could not extract readable text from PDF {safe_name!r}")
    elif ext in TEXT_EXTENSIONS or mime.startswith("text/") or mime in {"application/json", "application/xml"}:
        if b"\x00" in raw_bytes[:4096]:
            raise ValueError(f"File {safe_name!r} appears to be a binary file, not UTF-8 text")
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = raw_bytes.decode("utf-8", errors="replace")
    else:
        raise ValueError(f"Unsupported document format for {safe_name!r} ({mime or ext})")

    truncated = False
    if len(text) > MAX_DOCUMENT_CHARS:
        text = text[:MAX_DOCUMENT_CHARS] + "\n\n[... document truncated ...]"
        truncated = True

    return {
        "filename": safe_name,
        "characters": len(text),
        "truncated": truncated,
        "text": text,
    }


# ============================================================================
# Local Workspace File Tool
# ============================================================================


def get_workspace_root() -> Path:
    """Return the sandboxed workspace directory for files created by local chat."""
    base = Path(config.DATA_DIR) if config.DATA_DIR else (Path.home() / "ods" / "data")
    root = base / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def write_workspace_file(relative_path: str, content: str) -> dict[str, Any]:
    """Write a UTF-8 file inside the sandboxed workspace directory."""
    cleaned = (relative_path or "").strip().replace("\\", "/").lstrip("/")
    if (
        not cleaned
        or not _SAFE_RELATIVE_PATH.fullmatch(cleaned)
        or ".." in cleaned.split("/")
        or ":" in cleaned
    ):
        raise ValueError(f"Unsafe workspace file path: {relative_path!r}")

    root = get_workspace_root()
    target = (root / cleaned).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Workspace path escapes root: {relative_path!r}") from exc

    encoded = content.encode("utf-8")
    if len(encoded) > MAX_WORKSPACE_FILE_BYTES:
        raise ValueError(f"Workspace file {cleaned!r} exceeds size limit")

    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    temp_path.write_bytes(encoded)
    temp_path.replace(target)

    sha256 = hashlib.sha256(encoded).hexdigest()
    return {
        "path": cleaned,
        "sizeBytes": len(encoded),
        "sha256": sha256,
        "downloadUrl": f"/api/local-chat/workspace/files/{cleaned}",
    }


def execute_workspace_file_blocks(reply_text: str) -> list[dict[str, Any]]:
    """Parse and execute any <ods_write_file path="...">...</ods_write_file> blocks in reply_text."""
    delivered: list[dict[str, Any]] = []
    if not reply_text or "<ods_write_file" not in reply_text:
        return delivered
    for match in _WRITE_FILE_BLOCK_RE.finditer(reply_text):
        rel_path, body = match.group(1), match.group(2)
        try:
            record = write_workspace_file(rel_path, body)
            delivered.append(record)
        except ValueError as exc:
            logger.warning("Refused workspace file write %r: %s", rel_path, exc)
    return delivered


# ============================================================================
# Server-Persisted Conversations
# ============================================================================


def _conversations_file_path() -> Path:
    base = Path(config.DATA_DIR) if config.DATA_DIR else (Path.home() / "ods" / "data")
    conv_dir = base / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)
    return conv_dir / "conversations.json"


def load_persisted_conversations() -> list[dict[str, Any]]:
    """Load persisted conversations from disk."""
    path = _conversations_file_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("conversations"), list):
            return data["conversations"]
        if isinstance(data, list):
            return data
    except (OSError, ValueError, TypeError):
        logger.warning("Could not read persisted conversations from %s", path)
    return []


def save_persisted_conversations(conversations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Atomically persist conversations (including per-conversation contextStart) to disk."""
    bounded = list(conversations[:MAX_CONVERSATIONS])
    payload = {
        "schemaVersion": 1,
        "updatedAt": int(time.time() * 1000),
        "conversations": bounded,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_CONVERSATION_STORE_BYTES:
        raise ValueError("Conversations payload exceeds maximum storage limit")

    path = _conversations_file_path()
    tmp = path.with_name(f".conversations.tmp-{os.getpid()}-{int(time.time() * 1000)}")
    tmp.write_bytes(raw)
    tmp.replace(path)
    return bounded


# ============================================================================
# Direct Native SSE Streaming
# ============================================================================


async def stream_native_chat_completion(
    messages: list[dict[str, Any]],
    *,
    system_prompt: str = DEFAULT_NATIVE_SYSTEM_PROMPT,
    context_start: int = 0,
    context_window: int | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    model_name: str | None = None,
) -> AsyncIterator[bytes]:
    """Stream chat completions directly from native llama-server with KV-cache prefix reuse."""
    endpoint = resolve_native_llm_endpoint()
    headers = _native_llm_headers()
    headers["Content-Type"] = "application/json"
    headers["Accept"] = "text/event-stream"

    effective_window = context_window or DEFAULT_CONTEXT_WINDOW
    budget: BudgetedContext = plan_context_budget(
        messages,
        system_prompt=system_prompt,
        context_window=effective_window,
        context_start=context_start,
        reserve_output_tokens=max_tokens or 1024,
    )

    request_body: dict[str, Any] = {
        "messages": budget.messages,
        "stream": True,
        "cache_prompt": True,
        "temperature": temperature,
        "max_tokens": max_tokens or budget.max_completion_tokens,
    }
    if model_name:
        request_body["model"] = model_name

    accumulated_text: list[str] = []
    timeout = httpx.Timeout(connect=3.0, read=300.0, write=15.0, pool=5.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            f"{endpoint}/v1/chat/completions",
            headers=headers,
            json=request_body,
        ) as response:
            if response.status_code != 200:
                err_bytes = await response.aread()
                err_text = err_bytes.decode("utf-8", errors="replace")[:512]
                error_frame = {
                    "error": {
                        "message": f"Native llama-server returned HTTP {response.status_code}: {err_text}",
                        "status": response.status_code,
                    }
                }
                yield f"data: {json.dumps(error_frame)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
                return

            async for line in response.aiter_lines():
                if not line:
                    continue
                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        parsed = json.loads(data_str)
                        choices = parsed.get("choices")
                        if isinstance(choices, list) and choices:
                            delta = choices[0].get("delta") or {}
                            piece = delta.get("content")
                            if isinstance(piece, str) and piece:
                                accumulated_text.append(piece)
                    except (ValueError, TypeError):
                        pass
                    yield f"data: {data_str}\n\n".encode("utf-8")

    full_reply = "".join(accumulated_text)
    delivered_files = execute_workspace_file_blocks(full_reply)

    meta_frame = {
        "odsMeta": {
            "contextStart": budget.context_start,
            "estimatedPromptTokens": budget.estimated_prompt_tokens,
            "contextWindow": budget.context_window,
            "usablePromptBudget": budget.usable_prompt_budget,
            "truncated": budget.truncated,
            "droppedMessages": budget.dropped_messages,
            "deliveredFiles": delivered_files,
        }
    }
    yield f"data: {json.dumps(meta_frame)}\n\n".encode("utf-8")
    yield b"data: [DONE]\n\n"
