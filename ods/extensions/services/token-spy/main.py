"""
Token Spy — API Monitor — Authenticated LLM API Proxy.

Captures per-turn token usage and system prompt breakdown, streams SSE through
without buffering. Single or multi-instance deployment, sharing SQLite database.

Supports Anthropic, Moonshot, OpenAI, and generic OpenAI-compatible APIs.
"""

import asyncio
import ipaddress
import json
import logging
import os
import re
import secrets
import shlex
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response, Security
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from filters import apply_filters
from providers import ProviderRegistry
from routed_telemetry import (
    TelemetryValidationError,
    routed_event_to_usage,
    validate_routed_event,
)

# Database backend selection: sqlite (default) or postgres
DB_BACKEND = os.environ.get("DB_BACKEND", "sqlite").lower()

if DB_BACKEND == "postgres":
    from db_postgres import init_db, log_usage, query_report, query_session_status, query_summary, query_usage, query_recent_events
else:
    from db import init_db, log_usage, query_report, query_session_status, query_summary, query_usage, query_recent_events

# ── Configuration ────────────────────────────────────────────────────────────

AGENT_NAME = os.environ.get("AGENT_NAME", "unknown")
START_TIME = time.time()

# Provider configuration
API_PROVIDER = os.environ.get("API_PROVIDER", "anthropic").lower()
UPSTREAM_BASE_URL = os.environ.get("UPSTREAM_BASE_URL", "")
UPSTREAM_API_KEY = os.environ.get("UPSTREAM_API_KEY", "")

# Dual upstream support — route by protocol/endpoint
# Anthropic Messages API (/v1/messages) -> ANTHROPIC_UPSTREAM
# OpenAI Chat Completions (/v1/chat/completions) -> OPENAI_UPSTREAM
ANTHROPIC_UPSTREAM = os.environ.get("ANTHROPIC_UPSTREAM", "https://api.anthropic.com")
OPENAI_UPSTREAM = os.environ.get("OPENAI_UPSTREAM", "")

# Backwards compatibility for internal deployment
if not UPSTREAM_BASE_URL:
    if API_PROVIDER == "anthropic":
        UPSTREAM_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    elif API_PROVIDER == "moonshot":
        UPSTREAM_BASE_URL = os.environ.get("MOONSHOT_BASE_URL", "https://api.moonshot.ai")
    elif API_PROVIDER == "openai":
        UPSTREAM_BASE_URL = "https://api.openai.com"
    else:
        UPSTREAM_BASE_URL = "https://api.anthropic.com"  # Default

# If no explicit OPENAI_UPSTREAM, derive from context:
# - If primary provider is anthropic, openai requests go through upstream too
# - If primary provider is moonshot/openai, that becomes the openai upstream
if not OPENAI_UPSTREAM:
    if API_PROVIDER in ("moonshot", "openai"):
        OPENAI_UPSTREAM = UPSTREAM_BASE_URL
    else:
        OPENAI_UPSTREAM = UPSTREAM_BASE_URL  # fallback: same upstream

LOCAL_PROVIDER_NAMES = {"local", "ollama", "llama", "llama.cpp", "llama-server", "vllm"}
LOCAL_UPSTREAM_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal", "llama-server", "ollama"}


def _is_local_upstream_url(url: str) -> bool:
    """Return true only for hostnames/IPs that indicate a self-hosted runtime."""
    try:
        hostname = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if not hostname:
        return False
    if hostname in LOCAL_UPSTREAM_HOSTS or hostname.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def _provider_uses_local_runtime(provider_name: str) -> bool:
    provider = (provider_name or "").lower()
    if provider in LOCAL_PROVIDER_NAMES:
        return True
    if provider == "anthropic":
        return _is_local_upstream_url(ANTHROPIC_UPSTREAM)
    return _is_local_upstream_url(OPENAI_UPSTREAM) or _is_local_upstream_url(UPSTREAM_BASE_URL)

# Cost per million tokens by model prefix (longer prefixes matched first)
# USD per 1M tokens — input, output, cache_read, cache_write
COST_PER_MILLION = {
    # Anthropic Claude models
    "claude-opus-4-6": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
    "claude-opus-4-5": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
    "claude-opus-4-1": {"input": 15.0, "output": 75.0, "cache_read": 1.50, "cache_write": 18.75},
    "claude-opus-4": {"input": 15.0, "output": 75.0, "cache_read": 1.50, "cache_write": 18.75},
    "claude-sonnet-4": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_read": 0.10, "cache_write": 1.25},
    "claude-haiku-3-5": {"input": 0.80, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
    "claude-haiku": {"input": 0.80, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
    # Moonshot Kimi models
    "kimi-k2-0711": {"input": 0.60, "output": 3.0, "cache_read": 0.10, "cache_write": 0.60},
    "kimi-k2-0905": {"input": 0.60, "output": 2.50, "cache_read": 0.15, "cache_write": 0.60},
    "kimi-k2-thinking": {"input": 0.60, "output": 2.50, "cache_read": 0.15, "cache_write": 0.60},
    "kimi-k2": {"input": 0.60, "output": 2.50, "cache_read": 0.15, "cache_write": 0.60},
    # OpenAI models
    "gpt-4o": {"input": 2.50, "output": 10.0, "cache_read": 1.25, "cache_write": 0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cache_read": 0.075, "cache_write": 0},
    "gpt-4-turbo": {"input": 10.0, "output": 30.0, "cache_read": 0, "cache_write": 0},
    "gpt-4": {"input": 30.0, "output": 60.0, "cache_read": 0, "cache_write": 0},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50, "cache_read": 0, "cache_write": 0},
}

# ── Dynamic Settings ─────────────────────────────────────────────────────────
# Persistent settings stored in data/settings.json. Editable via dashboard or API.
# Per-agent overrides fall back to global defaults when set to null.

SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "data", "settings.json")

_DEFAULT_SETTINGS = {
    "session_char_limit": 200_000,
    "poll_interval_minutes": 5,
    "agents": {},
    "filters": {
        "enabled": False,
        "log_details": True,
        "tools": {
            "enabled": False,
            "mode": "allowlist",
            "allowlist": [
                "exec", "read", "write", "edit", "apply_patch",
                "web_fetch", "web_search", "process", "memory_search",
                "memory_get",
            ],
            "blocklist": [],
        },
        "system_prompt": {
            "enabled": False,
            "mode": "strip_sections",
            "strip_sections": [
                "## Heartbeats", "## Silent Replies", "## OpenClaw Self-Update",
                "## OpenClaw CLI Quick Reference", "## Reactions",
                "## Sandbox", "## Model Aliases",
            ],
            "custom_replacement": None,
            "max_chars": None,
        },
        "history": {
            "enabled": False,
            "max_pairs": 20,
            "always_keep_system": True,
            "always_keep_last_n": 6,
            "truncate_tool_results_chars": 2000,
            "drop_old_tool_calls": True,
            "drop_old_tool_calls_after_pairs": 8,
            "max_total_chars": None,
        },
    },
}


def _ensure_agent_in_settings(settings: dict, agent_name: str):
    """Ensure the current agent has an entry in settings."""
    if "agents" not in settings:
        settings["agents"] = {}
    if agent_name not in settings["agents"]:
        settings["agents"][agent_name] = {"session_char_limit": None, "poll_interval_minutes": None}
    return settings


def load_settings() -> dict:
    """Load settings from disk, merging with defaults for missing keys."""
    try:
        with open(SETTINGS_PATH, "r") as f:
            data = json.load(f)
        # Merge defaults for any missing top-level keys
        for k, v in _DEFAULT_SETTINGS.items():
            if k not in data:
                data[k] = v
        # Ensure current agent exists in settings
        data = _ensure_agent_in_settings(data, AGENT_NAME)
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        data = dict(_DEFAULT_SETTINGS)
        data = _ensure_agent_in_settings(data, AGENT_NAME)
        return data


def save_settings(data: dict):
    """Persist settings to disk via atomic write (write to temp + rename)."""
    settings_dir = os.path.dirname(SETTINGS_PATH)
    os.makedirs(settings_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=settings_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, SETTINGS_PATH)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get_agent_setting(agent: str, key: str):
    """Get a setting for a specific agent, falling back to global default."""
    settings = load_settings()
    agent_settings = settings.get("agents", {}).get(agent, {})
    val = agent_settings.get(key)
    if val is not None:
        return val
    return settings.get(key, _DEFAULT_SETTINGS.get(key))


def get_filter_settings(agent: str) -> dict:
    """Get merged filter config: agent-level overrides on top of global defaults.

    Agent value of null inherits from global. Returns the full filters dict.
    """
    settings = load_settings()
    global_filters = settings.get("filters", _DEFAULT_SETTINGS["filters"])

    # Deep-merge agent-level filter overrides
    agent_filters = settings.get("agents", {}).get(agent, {}).get("filters")
    if not agent_filters:
        return global_filters

    # Merge: agent values override global, null inherits global
    merged = {}
    for key, global_val in global_filters.items():
        agent_val = agent_filters.get(key)
        if agent_val is None:
            merged[key] = global_val
        elif isinstance(global_val, dict) and isinstance(agent_val, dict):
            # One level deeper merge for sub-categories (tools, system_prompt, history)
            sub_merged = dict(global_val)
            for sk, sv in agent_val.items():
                if sv is not None:
                    sub_merged[sk] = sv
            merged[key] = sub_merged
        else:
            merged[key] = agent_val

    return merged


logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s [{AGENT_NAME}] %(levelname)s %(message)s",
)
log = logging.getLogger("token-monitor")

# ── Authentication ───────────────────────────────────────────────────────────

TOKEN_SPY_API_KEY = os.environ.get("TOKEN_SPY_API_KEY", "")
if not TOKEN_SPY_API_KEY:
    _key_file = Path("/app/data/token-spy-api-key.txt")
    try:
        TOKEN_SPY_API_KEY = _key_file.read_text().strip()
    except FileNotFoundError:
        TOKEN_SPY_API_KEY = secrets.token_urlsafe(32)
        _key_file.parent.mkdir(parents=True, exist_ok=True)
        _key_file.write_text(TOKEN_SPY_API_KEY)
        _key_file.chmod(0o600)
        log.warning(
            "TOKEN_SPY_API_KEY not set. Generated key and wrote to %s (mode 0600).",
            _key_file,
        )

_security_scheme = HTTPBearer(auto_error=False)


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials = Security(_security_scheme),
):
    """Verify API key for protected endpoints."""
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Provide Bearer token in Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Compared as UTF-8 bytes: compare_digest raises TypeError on non-ASCII
    # str, which would turn an unauthenticated request into a 500 not a 403.
    if not secrets.compare_digest(
        credentials.credentials.encode("utf-8"), TOKEN_SPY_API_KEY.encode("utf-8")
    ):
        raise HTTPException(status_code=403, detail="Invalid API key.")
    return credentials.credentials


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="Token Spy — API Monitor", docs_url=None, redoc_url=None)

# Anthropic upstream client (for /v1/messages)
_anthropic_client: httpx.AsyncClient | None = None
# OpenAI-format upstream client (for /v1/chat/completions — Moonshot, OpenAI, etc.)
_openai_client: httpx.AsyncClient | None = None

_CLIENT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=30.0)
_CLIENT_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10)


def get_http_client() -> httpx.AsyncClient:
    """Get the Anthropic upstream client (Messages API)."""
    global _anthropic_client
    if _anthropic_client is None or _anthropic_client.is_closed:
        _anthropic_client = httpx.AsyncClient(
            base_url=ANTHROPIC_UPSTREAM,
            timeout=_CLIENT_TIMEOUT,
            limits=_CLIENT_LIMITS,
        )
    return _anthropic_client


def get_moonshot_client() -> httpx.AsyncClient:
    """Get the OpenAI-format upstream client (Chat Completions API)."""
    global _openai_client
    if _openai_client is None or _openai_client.is_closed:
        _openai_client = httpx.AsyncClient(
            base_url=OPENAI_UPSTREAM,
            timeout=_CLIENT_TIMEOUT,
            limits=_CLIENT_LIMITS,
        )
    return _openai_client


def _build_anthropic_upstream_headers(request: Request) -> dict[str, str]:
    """Build upstream headers for Anthropic-style requests.

    Token Spy auth stays on the proxy boundary. The Bearer token used to
    authenticate to Token Spy is never forwarded upstream.
    """
    headers: dict[str, str] = {}
    for key in (
        "x-api-key",
        "anthropic-version",
        "content-type",
        "anthropic-beta",
        "anthropic-dangerous-direct-browser-access",
        "user-agent",
        "x-app",
        "accept",
    ):
        val = request.headers.get(key)
        if val:
            headers[key] = val

    if "x-api-key" not in headers:
        if UPSTREAM_API_KEY:
            headers["x-api-key"] = UPSTREAM_API_KEY
        elif API_PROVIDER == "anthropic":
            raise HTTPException(
                status_code=500,
                detail="Token Spy is missing UPSTREAM_API_KEY for Anthropic upstream requests.",
            )

    return headers


def _build_openai_upstream_headers(request: Request) -> dict[str, str]:
    """Build upstream headers for OpenAI-compatible requests."""
    headers: dict[str, str] = {}
    for key in ("content-type", "accept", "user-agent", "openai-organization", "openai-project"):
        val = request.headers.get(key)
        if val:
            headers[key] = val

    if UPSTREAM_API_KEY:
        headers["authorization"] = f"Bearer {UPSTREAM_API_KEY}"
    elif API_PROVIDER in ("openai", "moonshot"):
        raise HTTPException(
            status_code=500,
            detail=f"Token Spy is missing UPSTREAM_API_KEY for {API_PROVIDER} upstream requests.",
        )

    return headers


def _uses_openai_upstream() -> bool:
    """Return True when the configured provider speaks OpenAI-style APIs."""
    return API_PROVIDER in ("openai", "moonshot", "local", "ollama", "vllm", "llama-server")


_db_available = True

@app.on_event("startup")
def on_startup():
    global _db_available
    try:
        init_db()
        _db_available = True
    except Exception as e:
        _db_available = False
        log.error(f"Database unavailable -- running in degraded mode (file-based session monitoring only): {e}")
    db_status = "connected" if _db_available else "DEGRADED"
    log.info(f"Token monitor started for agent={AGENT_NAME}, provider={API_PROVIDER}, anthropic_upstream={ANTHROPIC_UPSTREAM}, openai_upstream={OPENAI_UPSTREAM}, db={db_status}")
    # Start background polling for remote agents (A16 etc.)
    # Only the first instance (port 9110) runs the poller to avoid duplicates.
    import asyncio
    asyncio.get_event_loop().create_task(_poll_remote_agents())


async def _poll_remote_agents():
    """Periodically check remote and local-model agent sessions and auto-reset if needed."""
    await asyncio.sleep(10)  # initial delay to let things settle
    while True:
        try:
            # Poll remote agents (SSH-based)
            for agent in REMOTE_AGENTS:
                status = await asyncio.to_thread(_get_remote_session_status, agent)
                chars = status.get("current_history_chars", 0)
                limit = get_agent_setting(agent, "session_char_limit")
                if limit is None or limit <= 0:
                    limit = AUTO_RESET_HISTORY_CHARS
                rec = status.get("recommendation", "healthy")
                tool_results = status.get("tool_results", 0)
                needs_reset = chars >= limit or rec == "reset_recommended"
                if needs_reset:
                    reason = f"tool loop ({tool_results} calls)" if tool_results >= 480 else f"history {chars:,} >= {limit:,}"
                    log.warning(f"[REMOTE-POLL] {agent}: auto-reset — {reason}")
                    _kill_session(agent, reason=f"auto-reset ({reason})")
                    _last_auto_reset[agent] = time.time()
                elif chars > 0:
                    log.info(f"[REMOTE-POLL] {agent}: {chars:,} / {limit:,} chars ({chars*100//limit}%)")
            # Poll local-model agents (file-based, no proxy traffic)
            for agent in AGENT_SESSION_DIRS:
                if agent == AGENT_NAME or agent in REMOTE_AGENTS:
                    continue  # skip agents that go through this proxy instance
                status = _get_local_session_status(agent)
                if not status:
                    continue
                chars = status.get("current_history_chars", 0)
                limit = get_agent_setting(agent, "session_char_limit")
                if limit is None or limit <= 0:
                    limit = AUTO_RESET_HISTORY_CHARS
                rec = status.get("recommendation", "healthy")
                tool_results = status.get("tool_results", 0)
                needs_reset = chars >= limit or rec == "reset_recommended"
                if needs_reset:
                    reason = f"tool loop ({tool_results} calls)" if tool_results >= 480 else f"history {chars:,} >= {limit:,}"
                    log.warning(f"[LOCAL-POLL] {agent}: auto-reset — {reason}")
                    _kill_session(agent, reason=f"auto-reset ({reason})")
                    _last_auto_reset[agent] = time.time()
                elif chars > 0:
                    log.info(f"[LOCAL-POLL] {agent}: {chars:,} / {limit:,} chars ({chars*100//limit}%)")
        except Exception as e:
            log.error(f"[POLL] Error: {e}")
        await asyncio.sleep(60)


@app.on_event("shutdown")
async def on_shutdown():
    if _anthropic_client and not _anthropic_client.is_closed:
        await _anthropic_client.aclose()
    if _openai_client and not _openai_client.is_closed:
        await _openai_client.aclose()


# ── Analysis ─────────────────────────────────────────────────────────────────

# Map of known workspace filenames to their DB column names
WORKSPACE_FILE_MAP = {
    "AGENTS.md": "workspace_agents_chars",
    "SOUL.md": "workspace_soul_chars",
    "TOOLS.md": "workspace_tools_chars",
    "IDENTITY.md": "workspace_identity_chars",
    "USER.md": "workspace_user_chars",
    "HEARTBEAT.md": "workspace_heartbeat_chars",
    "BOOTSTRAP.md": "workspace_bootstrap_chars",
}


def analyze_system_prompt(system_blocks: list) -> dict:
    """Break down the system prompt into source categories by parsing markdown structure."""
    if not system_blocks:
        return {"system_prompt_total_chars": 0, "base_prompt_chars": 0}

    # Combine all system text blocks
    text = "\n".join(
        b.get("text", "") if isinstance(b, dict) else str(b)
        for b in system_blocks
    )
    result = {"system_prompt_total_chars": len(text)}

    # Initialize all workspace columns to 0
    for col in WORKSPACE_FILE_MAP.values():
        result.setdefault(col, 0)
    result["skill_injection_chars"] = 0

    # Extract workspace files from "# Project Context" section.
    # OpenClaw injects files as: "## FILENAME.md\n\n<full file content>\n\n"
    # The file content can contain its own ## headings, so we can't split on ## generically.
    # Instead, find each "## KNOWNFILE.md" marker and measure until the next known marker.
    ctx_match = re.search(r"^# Project Context\b", text, re.MULTILINE)
    if ctx_match:
        after_ctx = text[ctx_match.start():]
        # Build list of all known file markers: ## AGENTS.md, ## SOUL.md, etc.
        # Also include ## Silent Replies, ## Heartbeats, ## Runtime as end markers
        all_file_names = list(WORKSPACE_FILE_MAP.keys())
        end_markers = ["Silent Replies", "Heartbeats", "Runtime"]

        # Find positions of all ## FILENAME.md markers within the context
        file_positions = []
        for fname in all_file_names:
            pattern = re.compile(r"^## " + re.escape(fname) + r"\s*$", re.MULTILINE)
            m = pattern.search(after_ctx)
            if m:
                content_start = m.end() + 1  # skip the newline after header
                file_positions.append((m.start(), content_start, fname))

        # Also find end-of-context markers (sections that follow workspace files)
        for marker in end_markers:
            pattern = re.compile(r"^## " + re.escape(marker) + r"\b", re.MULTILINE)
            m = pattern.search(after_ctx)
            if m:
                file_positions.append((m.start(), m.start(), None))  # None = end marker

        # Sort by position
        file_positions.sort(key=lambda x: x[0])

        # Measure each file's content: from content_start to the next marker's start
        for i, (pos, content_start, fname) in enumerate(file_positions):
            if fname is None:
                continue  # end marker
            # Find next marker position
            if i + 1 < len(file_positions):
                content_end = file_positions[i + 1][0]
            else:
                content_end = len(after_ctx)
            content = after_ctx[content_start:content_end]
            col = WORKSPACE_FILE_MAP.get(fname)
            if col:
                result[col] += len(content)
            else:
                result.setdefault("workspace_other_chars", 0)
                result["workspace_other_chars"] = result.get("workspace_other_chars", 0) + len(content)

    # Extract skills section (## Skills (mandatory) ... until next ## at same level)
    skills_match = re.search(
        r"^## Skills \(mandatory\)\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL
    )
    if skills_match:
        result["skill_injection_chars"] = len(skills_match.group(0))

    # Base prompt = total minus workspace files and skills
    accounted = sum(
        v for k, v in result.items()
        if k.startswith("workspace_") or k == "skill_injection_chars"
    )
    result["base_prompt_chars"] = max(0, result["system_prompt_total_chars"] - accounted)

    return result


def analyze_messages(messages: list) -> dict:
    """Break down conversation history metrics."""
    if not messages:
        return {
            "message_count": 0,
            "user_message_count": 0,
            "assistant_message_count": 0,
            "conversation_history_chars": 0,
        }

    user_count = 0
    assistant_count = 0
    for m in messages:
        role = m.get("role", "")
        if role == "user":
            user_count += 1
        elif role == "assistant":
            assistant_count += 1

    return {
        "message_count": len(messages),
        "user_message_count": user_count,
        "assistant_message_count": assistant_count,
        "conversation_history_chars": len(json.dumps(messages, separators=(",", ":"))),
    }


def estimate_cost(model: str, input_tokens: int, output_tokens: int,
                  cache_read: int, cache_write: int, provider_name: str = "anthropic") -> float:
    """Estimate USD cost based on model and token counts.

    Uses the provider plugin system for pricing data. Falls back to hardcoded
    COST_PER_MILLION if provider lookup fails for backwards compatibility.
    """
    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
    }

    # Try provider-based cost calculation first
    provider = ProviderRegistry.get_or_none(provider_name)
    if provider:
        return provider.calculate_cost(usage, model)

    # Fallback to hardcoded rates for backwards compatibility
    rates = None
    model_lower = (model or "").lower()
    for prefix, r in COST_PER_MILLION.items():
        if prefix in model_lower:
            rates = r
            break
    if not rates:
        return 0.0

    return (
        input_tokens * rates["input"] / 1_000_000
        + output_tokens * rates["output"] / 1_000_000
        + cache_read * rates["cache_read"] / 1_000_000
        + cache_write * rates["cache_write"] / 1_000_000
    )


def classify_cost_source(cost: float, provider_name: str, agent_name: str) -> str:
    """Classify where a usage row's cost came from without guessing billing."""
    if cost > 0:
        return "priced_from_tokens"
    if provider_name == "local" or agent_name in LOCAL_MODEL_AGENTS:
        return "local_zero_cost"
    return "untracked"


# ── Message Cap Helper ────────────────────────────────────────────────────────

# ── Proxy Endpoint ───────────────────────────────────────────────────────────

@app.post("/v1/messages", dependencies=[Depends(verify_api_key)])
async def proxy_messages(request: Request):
    """Authenticated proxy for Anthropic /v1/messages with metrics capture."""
    start = time.time()

    # Read and parse request body
    raw_body = await request.body()
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        body = {}

    model = body.get("model", "unknown")
    system_blocks = body.get("system", [])
    messages = body.get("messages", [])
    tools = body.get("tools", [])
    is_streaming = body.get("stream", False)

    # Analyze request
    sys_analysis = analyze_system_prompt(
        system_blocks if isinstance(system_blocks, list) else [{"text": system_blocks}]
    )
    msg_analysis = analyze_messages(messages)

    log.info(
        f"→ {model} | msgs={msg_analysis['message_count']} | "
        f"sys={sys_analysis['system_prompt_total_chars']}ch | "
        f"tools={len(tools)} | stream={is_streaming} | "
        f"body={len(raw_body)}B"
    )

    forward_headers = _build_anthropic_upstream_headers(request)

    client = get_http_client()

    if is_streaming:
        return await _handle_streaming(
            client, raw_body, forward_headers, model, sys_analysis, msg_analysis,
            tools, start,
        )
    else:
        return await _handle_non_streaming(
            client, raw_body, forward_headers, model, sys_analysis, msg_analysis,
            tools, start,
        )


async def _handle_streaming(client, raw_body, headers, model, sys_analysis,
                            msg_analysis, tools, start_time):
    """Stream SSE response through while capturing token metrics."""

    # State for capturing usage from SSE events
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "stop_reason": None,
    }

    async def stream_and_capture():
        current_event = None
        logged = False
        try:
            async with client.stream(
                "POST", "/v1/messages",
                content=raw_body,
                headers=headers,
            ) as upstream:
                async for line in upstream.aiter_lines():
                    # Yield line immediately for transparent passthrough
                    yield line + "\n"

                    # Parse SSE events
                    stripped = line.strip()
                    if stripped.startswith("event:"):
                        current_event = stripped[6:].strip()
                    elif stripped.startswith("data:") and current_event:
                        data_str = stripped[5:].strip()
                        if data_str == "[DONE]":
                            continue
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        if current_event == "message_start":
                            msg_usage = (data.get("message", {}).get("usage", {}))
                            usage["input_tokens"] = msg_usage.get("input_tokens", 0)
                            usage["cache_read_tokens"] = msg_usage.get("cache_read_input_tokens", 0)
                            usage["cache_write_tokens"] = msg_usage.get("cache_creation_input_tokens", 0)

                        elif current_event == "message_delta":
                            delta_usage = data.get("usage", {})
                            if delta_usage.get("output_tokens") is not None:
                                usage["output_tokens"] = delta_usage["output_tokens"]
                            stop = data.get("delta", {}).get("stop_reason")
                            if stop:
                                usage["stop_reason"] = stop

                        elif current_event == "message_stop":
                            # Stream complete — log metrics
                            _log_entry(
                                model, sys_analysis, msg_analysis, tools,
                                raw_body, usage, start_time,
                                provider_name="anthropic",
                            )
                            logged = True
        except httpx.HTTPStatusError as e:
            log.error(f"Upstream HTTP error: {e.response.status_code}")
            yield f"data: {json.dumps({'type': 'error', 'error': {'type': 'proxy_error', 'message': 'Upstream request failed'}})}\n\n"
        except Exception as e:
            log.error(f"Proxy stream error: {e}")
        finally:
            # Guarantee billing metrics are logged even on CancelledError
            # (which is a BaseException and bypasses 'except Exception')
            if not logged and usage["input_tokens"] > 0:
                _log_entry(
                    model, sys_analysis, msg_analysis, tools,
                    raw_body, usage, start_time,
                    provider_name="anthropic",
                )

    return StreamingResponse(
        stream_and_capture(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _handle_non_streaming(client, raw_body, headers, model, sys_analysis,
                                msg_analysis, tools, start_time):
    """Handle non-streaming requests (rare for OpenClaw, but support anyway)."""
    try:
        resp = await client.request(
            "POST", "/v1/messages",
            content=raw_body,
            headers=headers,
        )
    except Exception as e:
        log.error(f"Upstream request error: {e}")
        return JSONResponse(
            status_code=502,
            content={"error": {"type": "proxy_error", "message": "Upstream request failed"}},
        )

    try:
        data = resp.json()
    except Exception:
        log.warning("Failed to parse Anthropic response JSON — token usage will be recorded as zero")
        data = {}

    resp_usage = data.get("usage", {})
    usage = {
        "input_tokens": resp_usage.get("input_tokens", 0),
        "output_tokens": resp_usage.get("output_tokens", 0),
        "cache_read_tokens": resp_usage.get("cache_read_input_tokens", 0),
        "cache_write_tokens": resp_usage.get("cache_creation_input_tokens", 0),
        "stop_reason": data.get("stop_reason"),
    }

    _log_entry(model, sys_analysis, msg_analysis, tools, raw_body, usage, start_time, provider_name="anthropic")

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
    )


# ── OpenAI-Compatible Proxy (Moonshot/Kimi) ──────────────────────────────────

def _analyze_openai_messages(messages: list) -> dict:
    """Analyze OpenAI-format messages for metrics."""
    if not messages:
        return {
            "message_count": 0,
            "user_message_count": 0,
            "assistant_message_count": 0,
            "conversation_history_chars": 0,
            "system_prompt_total_chars": 0,
            "base_prompt_chars": 0,
        }
    user_count = 0
    assistant_count = 0
    system_chars = 0
    for m in messages:
        role = m.get("role", "")
        if role == "user":
            user_count += 1
        elif role == "assistant":
            assistant_count += 1
        elif role == "system":
            content = m.get("content", "")
            system_chars += len(content) if isinstance(content, str) else len(json.dumps(content))
    return {
        "message_count": len(messages),
        "user_message_count": user_count,
        "assistant_message_count": assistant_count,
        "conversation_history_chars": len(json.dumps(messages, separators=(",", ":"))),
        "system_prompt_total_chars": system_chars,
        "base_prompt_chars": system_chars,
    }


@app.post("/v1/chat/completions", dependencies=[Depends(verify_api_key)])
async def proxy_chat_completions(request: Request):
    """Authenticated proxy for OpenAI-compatible /v1/chat/completions."""
    start = time.time()

    raw_body = await request.body()
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        body = {}

    model = body.get("model", "unknown")
    messages = body.get("messages", [])
    tools = body.get("tools", [])
    is_streaming = body.get("stream", False)

    # Moonshot/Kimi doesn't support the "developer" role (OpenAI-specific).
    # Rewrite to "system" before forwarding.
    rewritten = False
    for m in messages:
        if m.get("role") == "developer":
            m["role"] = "system"
            rewritten = True
    if rewritten:
        body["messages"] = messages
        raw_body = json.dumps(body, separators=(",", ":")).encode()

    # ── Apply request filters (tool stripping, system prompt trimming, history) ──
    filter_result = None
    f_settings = get_filter_settings(AGENT_NAME)
    if f_settings.get("enabled"):
        body, filter_result = apply_filters(body, f_settings)
        raw_body = json.dumps(body, separators=(",", ":")).encode()
        messages = body.get("messages", [])
        tools = body.get("tools", [])

    msg_analysis = _analyze_openai_messages(messages)
    sys_analysis = {
        "system_prompt_total_chars": msg_analysis.pop("system_prompt_total_chars", 0),
        "base_prompt_chars": msg_analysis.pop("base_prompt_chars", 0),
    }

    # Debug: log message roles to diagnose ROLE_UNSPECIFIED errors
    roles = [m.get("role", "<MISSING>") for m in messages]
    log.info(
        f"→ [openai] {model} | msgs={msg_analysis['message_count']} | "
        f"sys={sys_analysis['system_prompt_total_chars']}ch | "
        f"tools={len(tools)} | stream={is_streaming} | "
        f"body={len(raw_body)}B | roles={roles}"
    )

    forward_headers = _build_openai_upstream_headers(request)

    client = get_moonshot_client()

    if is_streaming:
        return await _handle_openai_streaming(
            client, raw_body, forward_headers, model, sys_analysis, msg_analysis,
            tools, start, filter_result=filter_result,
        )
    else:
        return await _handle_openai_non_streaming(
            client, raw_body, forward_headers, model, sys_analysis, msg_analysis,
            tools, start, filter_result=filter_result,
        )


async def _handle_openai_streaming(client, raw_body, headers, model, sys_analysis,
                                   msg_analysis, tools, start_time, filter_result=None):
    """Stream OpenAI SSE response through while capturing token metrics."""
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "stop_reason": None,
    }

    async def stream_and_capture():
        logged = False
        try:
            async with client.stream(
                "POST", "/v1/chat/completions",
                content=raw_body,
                headers=headers,
            ) as upstream:
                if upstream.status_code >= 400:
                    err_body = b""
                    async for chunk in upstream.aiter_bytes():
                        err_body += chunk
                    log.error(f"Upstream {upstream.status_code}: {err_body[:2000].decode(errors='replace')}")
                    yield f"data: {err_body.decode(errors='replace')}\n\n"
                    return
                async for line in upstream.aiter_lines():
                    yield line + "\n"

                    stripped = line.strip()
                    if not stripped.startswith("data:"):
                        continue
                    data_str = stripped[5:].strip()
                    if data_str == "[DONE]":
                        _log_entry(
                            model, sys_analysis, msg_analysis, tools,
                            raw_body, usage, start_time,
                            provider_name="openai",
                            filter_result=filter_result,
                        )
                        logged = True
                        continue
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    # OpenAI streaming: usage comes in the final chunk
                    chunk_usage = data.get("usage")
                    if chunk_usage:
                        usage["input_tokens"] = chunk_usage.get("prompt_tokens", 0)
                        usage["output_tokens"] = chunk_usage.get("completion_tokens", 0)
                        usage["cache_read_tokens"] = chunk_usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)

                    choices = data.get("choices", [])
                    if choices:
                        finish = choices[0].get("finish_reason")
                        if finish:
                            usage["stop_reason"] = finish

        except httpx.HTTPStatusError as e:
            log.error(f"Upstream HTTP error: {e.response.status_code}")
            yield f"data: {json.dumps({'error': {'message': 'Upstream request failed', 'type': 'proxy_error'}})}\n\n"
        except Exception as e:
            log.error(f"Proxy stream error: {e}")
        finally:
            # Guarantee billing metrics are logged even on CancelledError
            if not logged and usage["input_tokens"] > 0:
                _log_entry(model, sys_analysis, msg_analysis, tools, raw_body, usage, start_time, provider_name="openai", filter_result=filter_result)

    return StreamingResponse(
        stream_and_capture(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _handle_openai_non_streaming(client, raw_body, headers, model, sys_analysis,
                                       msg_analysis, tools, start_time, filter_result=None):
    """Handle non-streaming OpenAI-format requests."""
    try:
        resp = await client.request(
            "POST", "/v1/chat/completions",
            content=raw_body,
            headers=headers,
        )
    except Exception as e:
        log.error(f"Upstream request error: {e}")
        return JSONResponse(
            status_code=502,
            content={"error": {"message": "Upstream request failed", "type": "proxy_error"}},
        )

    try:
        data = resp.json()
    except Exception:
        log.warning("Failed to parse OpenAI response JSON — token usage will be recorded as zero")
        data = {}

    resp_usage = data.get("usage", {})
    usage = {
        "input_tokens": resp_usage.get("prompt_tokens", 0),
        "output_tokens": resp_usage.get("completion_tokens", 0),
        "cache_read_tokens": resp_usage.get("prompt_tokens_details", {}).get("cached_tokens", 0),
        "cache_write_tokens": 0,
        "stop_reason": (data.get("choices", [{}])[0].get("finish_reason") if data.get("choices") else None),
    }

    _log_entry(model, sys_analysis, msg_analysis, tools, raw_body, usage, start_time, provider_name="openai", filter_result=filter_result)

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
    )


# ── Auto-Reset (External Compaction) ─────────────────────────────────────────

# Session directories for auto-reset feature (OpenClaw-specific)
# Format: AGENT_SESSION_DIRS='{"agent-name":"/path/to/sessions"}'
AGENT_SESSION_DIRS = {}
try:
    _dirs_json = os.environ.get("AGENT_SESSION_DIRS", "")
    if _dirs_json:
        AGENT_SESSION_DIRS = json.loads(_dirs_json)
except json.JSONDecodeError:
    log.warning("Invalid AGENT_SESSION_DIRS JSON, using empty dict")

# If no AGENT_SESSION_DIRS configured, auto-reset will only work via the
# token monitor's built-in history tracking (no file-based session management).
# To enable file-based session management, set AGENT_SESSION_DIRS as JSON:
#   AGENT_SESSION_DIRS='{"my-agent":"/path/to/sessions"}'

# Remote agents: run on different hosts, accessed via SSH.
# No remote agents configured by default.
REMOTE_AGENTS = {}

# Agents running local/self-hosted models ($0 cost, no cloud API).
# These get a "LOCAL" badge and $0 cost display on the dashboard.
# Set via env: LOCAL_MODEL_AGENTS='agent1,agent2'
LOCAL_MODEL_AGENTS = set(filter(None, os.environ.get("LOCAL_MODEL_AGENTS", "").split(",")))

# Threshold: auto-kill session when conversation history exceeds this (chars).
# 200K chars ≈ ~53K tokens — aggressive reset keeps sessions lean and costs low.
AUTO_RESET_HISTORY_CHARS = 200_000

# Cooldown: don't auto-reset the same agent more than once per 60 seconds
_last_auto_reset: dict[str, float] = {}



def _get_local_session_status(agent: str) -> dict:
    """Get session status for a local agent by reading JSONL files directly.
    Used for agents whose traffic doesn't pass through the token monitor proxy
    (e.g. agents using a local model via vLLM/Ollama)."""
    sessions_dir = AGENT_SESSION_DIRS.get(agent)
    if not sessions_dir:
        return None

    import glob
    files = sorted(glob.glob(os.path.join(sessions_dir, "*.jsonl")), key=os.path.getmtime, reverse=True)
    if not files:
        return None

    largest = files[0]
    try:
        with open(largest) as f:
            lines = f.readlines()
    except Exception:
        log.warning(f"[SESSION] Failed to read session file: {largest}")
        return None

    user_turns = 0
    assistant_turns = 0
    history_chars = 0
    tool_results = 0
    for line in lines:
        try:
            d = json.loads(line)
            if d.get("type") == "message":
                msg = d.get("message", {})
                if isinstance(msg, str):
                    msg = json.loads(msg)
                role = msg.get("role", "")
                if role == "user":
                    user_turns += 1
                elif role == "assistant":
                    assistant_turns += 1
                if role in ("toolResult", "tool") or msg.get("tool_call_id"):
                    tool_results += 1
                c = msg.get("content", "")
                if isinstance(c, list):
                    history_chars += sum(len(str(x)) for x in c)
                elif isinstance(c, str):
                    history_chars += len(c)
        except (json.JSONDecodeError, KeyError, TypeError):
            pass  # skip malformed JSONL lines

    limit = get_agent_setting(agent, "session_char_limit") or AUTO_RESET_HISTORY_CHARS
    if tool_results >= 480:
        rec = "reset_recommended"
    elif history_chars > limit:
        rec = "reset_recommended"
    elif history_chars > limit * 0.8:
        rec = "compact_soon"
    elif history_chars > limit * 0.6:
        rec = "monitor"
    else:
        rec = "healthy"

    # Use user turns if available; fall back to assistant turns for local-model
    # agents whose OpenClaw gateway doesn't log user messages in the JSONL.
    turns = user_turns if user_turns > 0 else assistant_turns

    return {
        "agent": agent,
        "current_session_turns": turns,
        "current_history_chars": history_chars,
        "last_turn_cost": 0,
        "avg_cost_last_5": 0,
        "cache_write_pct_last_5": 0,
        "cost_since_last_reset": 0,
        "turns_since_last_reset": turns,
        "recommendation": rec,
        "is_local_model": agent in LOCAL_MODEL_AGENTS,
        "tool_results": tool_results,
        "file_bytes": os.path.getsize(largest),
        "total_lines": len(lines),
        "session_files": len(files),
    }


def _get_local_accumulated_turns(agent: str) -> int:
    """Count total turns across ALL session files for a local-model agent,
    with a persistent accumulator to survive session file cleanup/purge.
    Unlike _get_local_session_status (current session only), this gives the
    lifetime accumulated turn count — important for cost-per-turn math when
    the agent runs at $0/token."""
    sessions_dir = AGENT_SESSION_DIRS.get(agent)
    if not sessions_dir:
        return 0

    # Count current turns from all session files on disk.
    # Use user turns if available; fall back to assistant turns for agents
    # whose OpenClaw gateway doesn't log user messages in the JSONL.
    import glob
    files = glob.glob(os.path.join(sessions_dir, "*.jsonl"))
    user_turns = 0
    assistant_turns = 0
    for fpath in files:
        try:
            with open(fpath) as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        if d.get("type") == "message":
                            msg = d.get("message", {})
                            if isinstance(msg, str):
                                msg = json.loads(msg)
                            role = msg.get("role", "")
                            if role == "user":
                                user_turns += 1
                            elif role == "assistant":
                                assistant_turns += 1
                    except (json.JSONDecodeError, KeyError, TypeError):
                        pass  # skip malformed JSONL lines
        except Exception:
            log.warning(f"[SESSION] Failed to read session file: {fpath}")
    current_file_turns = user_turns if user_turns > 0 else assistant_turns

    # Persistent accumulator — survives session purge (250KB/24h cleanup)
    acc_path = os.path.join(os.path.dirname(__file__), "data", f"{agent}-accumulated-turns.json")
    try:
        with open(acc_path) as f:
            acc = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        acc = {"total": 0, "last_file_turns": 0}

    last_file_turns = acc.get("last_file_turns", 0)
    total = acc.get("total", 0)

    if current_file_turns >= last_file_turns:
        # Normal growth or no change — add the delta
        total += (current_file_turns - last_file_turns)
    else:
        # Session files were purged (current < last) — add what's on disk now
        total += current_file_turns

    acc = {"total": total, "last_file_turns": current_file_turns}
    try:
        os.makedirs(os.path.dirname(acc_path), exist_ok=True)
        with open(acc_path, "w") as f:
            json.dump(acc, f)
    except Exception:
        log.warning(f"[SESSION] Failed to save accumulated turns for {agent}")

    return total


def _get_remote_session_status(agent: str) -> dict:
    """Get session status for a remote agent via SSH."""
    import subprocess
    remote = REMOTE_AGENTS.get(agent)
    if not remote:
        return {"agent": agent, "recommendation": "no_data", "current_session_turns": 0,
                "current_history_chars": 0, "last_turn_cost": 0, "avg_cost_last_5": 0,
                "cache_write_pct_last_5": 0, "cost_since_last_reset": 0, "turns_since_last_reset": 0}

    ssh_target = f"{remote['user']}@{remote['host']}"
    sessions_dir = remote["sessions_dir"]

    script = (
        "import json, os, glob, sys\n"
        "sdir = sys.argv[1]\n"
        "files = sorted(glob.glob(os.path.join(sdir, '*.jsonl')), key=os.path.getmtime, reverse=True)\n"
        "if not files:\n"
        "    print(json.dumps({'turns': 0, 'chars': 0, 'files': 0}))\n"
        "else:\n"
        "    largest = files[0]\n"
        "    with open(largest) as f:\n"
        "        lines = f.readlines()\n"
        "    turns = 0\n"
        "    history_chars = 0\n"
        "    tool_results = 0\n"
        "    for l in lines:\n"
        "        try:\n"
        "            d = json.loads(l)\n"
        "            if d.get('type') == 'message':\n"
        "                msg = d.get('message', {})\n"
        "                if isinstance(msg, str): msg = json.loads(msg)\n"
        "                role = msg.get('role', '')\n"
        "                if role == 'user': turns += 1\n"
        "                if role in ('toolResult', 'tool') or msg.get('tool_call_id'): tool_results += 1\n"
        "                c = msg.get('content', '')\n"
        "                if isinstance(c, list):\n"
        "                    history_chars += sum(len(str(x)) for x in c)\n"
        "                elif isinstance(c, str):\n"
        "                    history_chars += len(c)\n"
        "        except Exception:\n"
        "            pass  # skip corrupt JSONL lines\n"
        "    print(json.dumps({'turns': turns, 'chars': history_chars, 'tool_results': tool_results,"
        " 'file_bytes': os.path.getsize(largest), 'total_lines': len(lines), 'files': len(files)}))"
    )
    try:
        remote_cmd = f"python3 - {shlex.quote(sessions_dir)}"
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=3", "-o", "StrictHostKeyChecking=accept-new",
             ssh_target, remote_cmd],
            input=script, capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            log.warning(f"[REMOTE] SSH to {agent} failed: {result.stderr[:200]}")
            return {"agent": agent, "recommendation": "no_data", "current_session_turns": 0,
                    "current_history_chars": 0, "last_turn_cost": 0, "avg_cost_last_5": 0,
                    "cache_write_pct_last_5": 0, "cost_since_last_reset": 0, "turns_since_last_reset": 0}

        data = json.loads(result.stdout.strip())
        history_chars = data.get("chars", 0)
        turns = data.get("turns", 0)
        tool_results = data.get("tool_results", 0)

        limit = get_agent_setting(agent, "session_char_limit") or AUTO_RESET_HISTORY_CHARS
        if tool_results >= 480:
            rec = "reset_recommended"
            log.warning(f"[REMOTE] {agent}: tool loop detected ({tool_results} tool results in session)")
        elif history_chars > limit:
            rec = "reset_recommended"
        elif history_chars > limit * 0.8:
            rec = "compact_soon"
        elif history_chars > limit * 0.6:
            rec = "monitor"
        else:
            rec = "healthy"

        return {
            "agent": agent,
            "current_session_turns": turns,
            "current_history_chars": history_chars,
            "last_turn_cost": 0,
            "avg_cost_last_5": 0,
            "cache_write_pct_last_5": 0,
            "cost_since_last_reset": 0,
            "turns_since_last_reset": turns,
            "recommendation": rec,
            "is_local_model": agent in LOCAL_MODEL_AGENTS,
            "tool_results": tool_results,
        }
    except Exception as e:
        log.warning(f"[REMOTE] Failed to get session status for {agent}: {e}")
        return {"agent": agent, "recommendation": "no_data", "current_session_turns": 0,
                "current_history_chars": 0, "last_turn_cost": 0, "avg_cost_last_5": 0,
                "cache_write_pct_last_5": 0, "cost_since_last_reset": 0, "turns_since_last_reset": 0}


def _kill_remote_session(agent: str, reason: str = "dashboard") -> dict:
    """Kill the largest session for a remote agent via SSH."""
    import subprocess
    remote = REMOTE_AGENTS.get(agent)
    if not remote:
        return {"agent": agent, "action": "none", "reason": f"unknown remote agent: {agent}"}

    ssh_target = f"{remote['user']}@{remote['host']}"
    sessions_dir = remote["sessions_dir"]

    script = (
        "import os, glob, json, sys\n"
        "sdir = sys.argv[1]\n"
        "files = sorted(glob.glob(os.path.join(sdir, '*.jsonl')), key=os.path.getsize, reverse=True)\n"
        "if not files:\n"
        "    print(json.dumps({'action': 'none', 'reason': 'no sessions'}))\n"
        "else:\n"
        "    f = files[0]\n"
        "    size = os.path.getsize(f)\n"
        "    sid = os.path.basename(f).replace('.jsonl', '')\n"
        "    os.remove(f)\n"
        "    sj = os.path.join(sdir, 'sessions.json')\n"
        "    try:\n"
        "        with open(sj) as fh: data = json.load(fh)\n"
        "        for k in list(data.keys()):\n"
        "            if isinstance(data[k], dict) and data[k].get('sessionId') == sid: del data[k]\n"
        "        with open(sj, 'w') as fh: json.dump(data, fh, indent=2)\n"
        "    except Exception as e:\n"
        "        print(json.dumps({'warn': 'sessions.json update failed', 'error': str(e)}))\n"
        "    print(json.dumps({'action': 'killed', 'session_id': sid, 'size_bytes': size}))"
    )
    try:
        remote_cmd = f"python3 - {shlex.quote(sessions_dir)}"
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=3", "-o", "StrictHostKeyChecking=accept-new",
             ssh_target, remote_cmd],
            input=script, capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return {"agent": agent, "action": "none", "reason": f"SSH failed: {result.stderr[:100]}"}
        data = json.loads(result.stdout.strip())
        data["agent"] = agent
        if data.get("action") == "killed":
            log.warning(f"[RESET] Remote killed session {data.get('session_id')} for {agent} ({data.get('size_bytes')} bytes) — {reason}")
        return data
    except Exception as e:
        log.error(f"Remote session check failed for {agent}: {e}")
        return {"agent": agent, "action": "none", "reason": "Remote check failed"}

def _kill_session(agent: str, reason: str = "manual") -> dict:
    """Kill the largest active session for an agent. Returns result dict."""
    import subprocess
    if agent in REMOTE_AGENTS:
        return _kill_remote_session(agent, reason)

    sessions_dir = AGENT_SESSION_DIRS.get(agent)
    if not sessions_dir:
        return {"agent": agent, "action": "none", "reason": f"unknown agent: {agent}"}

    result = subprocess.run(
        ["ls", "-S", f"{sessions_dir}/"],
        capture_output=True, text=True,
    )
    largest = None
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if line.endswith(".jsonl"):
            largest = line.replace(".jsonl", "")
            break

    if not largest:
        return {"agent": agent, "action": "none", "reason": "no active sessions found"}

    session_file = f"{sessions_dir}/{largest}.jsonl"
    try:
        size = os.path.getsize(session_file)
        os.remove(session_file)
    except FileNotFoundError:
        return {"agent": agent, "action": "none", "reason": "session file already gone"}

    # Clean sessions.json reference (best-effort)
    sessions_json = f"{sessions_dir}/sessions.json"
    try:
        with open(sessions_json, "r") as f:
            data = json.load(f)
        to_remove = [k for k, v in data.items() if isinstance(v, dict) and v.get("sessionId") == largest]
        for k in to_remove:
            del data[k]
        with open(sessions_json, "w") as f:
            json.dump(data, f, indent=2)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        log.warning(f"[RESET] Failed to clean sessions.json for {agent}")

    log.warning(f"[RESET] Killed session {largest} for {agent} ({size} bytes) — {reason}")
    return {"agent": agent, "action": "killed", "session_id": largest, "size_bytes": size}


def _auto_reset_check(agent: str, history_chars: int):
    """Check if session should be auto-reset based on history size.

    Uses dynamic settings from settings.json (editable via dashboard).
    Per-agent overrides take precedence over the global session_char_limit.
    """
    limit = get_agent_setting(agent, "session_char_limit")
    if limit is None or limit <= 0:
        limit = AUTO_RESET_HISTORY_CHARS  # fallback to hardcoded default
    if history_chars < limit:
        return

    # Cooldown: skip if we just reset this agent
    now = time.time()
    last = _last_auto_reset.get(agent, 0)
    if now - last < 60:
        return

    log.warning(
        f"[AUTO-RESET] {agent} history={history_chars:,} chars exceeds "
        f"{limit:,} threshold — killing session"
    )
    result = _kill_session(agent, reason=f"auto-reset (history={history_chars:,} chars)")
    if result.get("action") == "killed":
        _last_auto_reset[agent] = now
        log.warning(f"[AUTO-RESET] {agent} session killed: {result.get('session_id')}")


def _log_entry(model, sys_analysis, msg_analysis, tools, raw_body, usage, start_time,
               provider_name: str = None, filter_result=None):
    """Write a usage entry to SQLite.

    Args:
        provider_name: Provider name for cost calculation. Auto-detected from model if not provided.
        filter_result: FilterResult from request filtering, or None if filters disabled.
    """
    duration_ms = int((time.time() - start_time) * 1000)

    # Auto-detect provider from model name if not specified
    if not provider_name:
        model_lower = (model or "").lower()
        if "claude" in model_lower:
            provider_name = "anthropic"
        elif "kimi" in model_lower:
            provider_name = "openai"  # Moonshot uses OpenAI-compatible format
        elif "gpt" in model_lower:
            provider_name = "openai"
        else:
            provider_name = "anthropic"  # default

    cost = estimate_cost(
        model,
        usage["input_tokens"],
        usage["output_tokens"],
        usage["cache_read_tokens"],
        usage["cache_write_tokens"],
        provider_name=provider_name,
    )
    if cost == 0 and _provider_uses_local_runtime(provider_name):
        provider_name = "local"

    entry = {
        "agent": AGENT_NAME,
        "model": model,
        "provider_name": provider_name,
        "cost_source": classify_cost_source(cost, provider_name, AGENT_NAME),
        "request_body_bytes": len(raw_body),
        "tool_count": len(tools),
        "estimated_cost_usd": round(cost, 6),
        "duration_ms": duration_ms,
        **sys_analysis,
        **msg_analysis,
        **usage,
    }

    # Include filter metrics if filtering was applied
    if filter_result:
        entry["filter_chars_saved"] = filter_result.chars_saved
        entry["filter_tokens_saved"] = filter_result.estimated_tokens_saved
        entry["filter_tools_removed"] = filter_result.tools_removed

    try:
        log_usage(entry)
        filter_info = ""
        if filter_result and filter_result.chars_saved > 0:
            filter_info = f" | FILTERED saved≈{filter_result.estimated_tokens_saved:,}tok"
        log.info(
            f"← {model} | in={usage['input_tokens']} out={usage['output_tokens']} "
            f"cache_r={usage['cache_read_tokens']} cache_w={usage['cache_write_tokens']} | "
            f"${cost:.4f} | {duration_ms}ms{filter_info}"
        )
    except Exception as e:
        log.error(f"Failed to log usage: {e}")

    # Check if this agent needs an auto-reset
    history_chars = msg_analysis.get("conversation_history_chars", 0)
    _auto_reset_check(AGENT_NAME, history_chars)


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    uptime = int(time.time() - START_TIME)
    limit = get_agent_setting(AGENT_NAME, "session_char_limit") or 200_000
    return {
        "status": "ok",
        "agent": AGENT_NAME,
        "uptime_seconds": uptime,
        "session_char_limit": limit,
    }


# ── API Endpoints ────────────────────────────────────────────────────────────


@app.post("/api/ingest/routed", dependencies=[Depends(verify_api_key)])
async def ingest_routed_telemetry(request: Request):
    """Store metadata-only usage emitted by the ODS model router."""
    raw_body = await request.body()
    if len(raw_body) > 16 * 1024:
        raise HTTPException(status_code=413, detail="Telemetry event is too large.")
    try:
        payload = json.loads(raw_body)
        event = validate_routed_event(payload)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body.") from exc
    except TelemetryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not _db_available:
        raise HTTPException(status_code=503, detail="Usage database is unavailable.")

    await asyncio.to_thread(log_usage, routed_event_to_usage(event))
    return JSONResponse({"status": "accepted"}, status_code=202)


@app.get("/api/filter-stats", dependencies=[Depends(verify_api_key)])
def api_filter_stats():
    """Current filter configuration and summary."""
    f_settings = get_filter_settings(AGENT_NAME)
    enabled = f_settings.get("enabled", False)

    tools_cfg = f_settings.get("tools", {})
    tool_info = {}
    if tools_cfg.get("enabled"):
        mode = tools_cfg.get("mode", "blocklist")
        if mode == "allowlist":
            tool_info = {"mode": "allowlist", "kept": tools_cfg.get("allowlist", [])}
        else:
            tool_info = {"mode": "blocklist", "blocked": tools_cfg.get("blocklist", [])}

    return {
        "filters_enabled": enabled,
        "agent": AGENT_NAME,
        "tools": tool_info,
        "system_prompt": {
            "enabled": f_settings.get("system_prompt", {}).get("enabled", False),
            "mode": f_settings.get("system_prompt", {}).get("mode"),
        },
        "history": {
            "enabled": f_settings.get("history", {}).get("enabled", False),
            "max_pairs": f_settings.get("history", {}).get("max_pairs"),
            "truncate_tool_results_chars": f_settings.get("history", {}).get("truncate_tool_results_chars"),
        },
    }


@app.get("/api/settings", dependencies=[Depends(verify_api_key)])
def api_get_settings():
    """Current settings. Per-agent values of null inherit the global default."""
    settings = load_settings()
    for agent_name, agent_cfg in settings.get("agents", {}).items():
        agent_cfg["_effective_session_char_limit"] = get_agent_setting(agent_name, "session_char_limit")
        agent_cfg["_effective_poll_interval_minutes"] = get_agent_setting(agent_name, "poll_interval_minutes")
    return settings


@app.post("/api/settings", dependencies=[Depends(verify_api_key)])
async def api_update_settings(request: Request):
    """Update settings. Accepts partial updates (only provided keys are changed).

    Example body:
      {"session_char_limit": 150000}
      {"agents": {"my-agent": {"session_char_limit": 100000}}}
      {"poll_interval_minutes": 3}
    """
    body = await request.json()
    settings = load_settings()

    if "session_char_limit" in body:
        val = body["session_char_limit"]
        if val is not None:
            val = int(val)
            if val < 10000:
                return JSONResponse({"error": "session_char_limit must be >= 10000"}, status_code=400)
        settings["session_char_limit"] = val

    if "poll_interval_minutes" in body:
        val = body["poll_interval_minutes"]
        if val is not None:
            val = int(val)
            if val < 1 or val > 60:
                return JSONResponse({"error": "poll_interval_minutes must be 1-60"}, status_code=400)
        settings["poll_interval_minutes"] = val

    # Deep-merge filter settings (hot-reloadable)
    if "filters" in body:
        existing_filters = settings.get("filters", dict(_DEFAULT_SETTINGS["filters"]))
        new_filters = body["filters"]
        for key, val in new_filters.items():
            if isinstance(val, dict) and isinstance(existing_filters.get(key), dict):
                existing_filters[key].update(val)
            else:
                existing_filters[key] = val
        settings["filters"] = existing_filters

    if "agents" in body:
        for agent_name, agent_updates in body["agents"].items():
            if agent_name not in settings.get("agents", {}):
                settings.setdefault("agents", {})[agent_name] = {}
            for key in ("session_char_limit", "poll_interval_minutes"):
                if key in agent_updates:
                    val = agent_updates[key]
                    if val is not None:
                        val = int(val)
                    settings["agents"][agent_name][key] = val
            # Per-agent filter overrides
            if "filters" in agent_updates:
                settings["agents"][agent_name]["filters"] = agent_updates["filters"]

    save_settings(settings)

    new_poll = settings.get("poll_interval_minutes", 5)
    _update_timer_interval(new_poll)

    log.info(f"[SETTINGS] Updated: {body}")
    return api_get_settings()


def _update_timer_interval(minutes: int):
    """Best-effort update of the systemd timer interval."""
    import subprocess
    timer_path = os.environ.get("SESSION_TIMER_PATH", "/etc/systemd/system/openclaw-session-cleanup.timer")
    try:
        with open(timer_path, "r") as f:
            timer_content = f.read()
        import re as _re
        new_content = _re.sub(
            r"OnUnitActiveSec=\d+min",
            f"OnUnitActiveSec={minutes}min",
            timer_content,
        )
        if new_content != timer_content:
            with open(timer_path, "w") as f:
                f.write(new_content)
            subprocess.run(["systemctl", "daemon-reload"], capture_output=True)
            subprocess.run(["systemctl", "restart", "openclaw-session-cleanup.timer"], capture_output=True)
            log.info(f"[SETTINGS] Timer updated to {minutes}min")
    except Exception as e:
        log.warning(f"[SETTINGS] Could not update timer: {e} (may need sudo)")

@app.get("/api/usage", dependencies=[Depends(verify_api_key)])
def api_usage(agent: str | None = None, hours: int = 24, limit: int = 200):
    return query_usage(agent=agent, hours=hours, limit=limit)


@app.get("/api/report", dependencies=[Depends(verify_api_key)])
def api_report(start: str, end: str):
    """Aggregate real usage and cost metrics for an inclusive date range."""
    try:
        return query_report(start=start, end=end)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.get("/token-usage", dependencies=[Depends(verify_api_key)])
def token_usage_alias(agent: str | None = None, hours: int = 24, limit: int = 200):
    """Alias for /api/usage — returns recent token usage events."""
    return query_usage(agent=agent, hours=hours, limit=limit)


@app.get("/api/summary", dependencies=[Depends(verify_api_key)])
def api_summary(hours: int = 24):
    try:
        result = query_summary(hours=hours) if _db_available else []
    except Exception as e:
        log.warning(f"DB summary query failed: {e}")
        result = []
    # Tag DB results for local-model agents
    for r in result:
        if r.get("agent") in LOCAL_MODEL_AGENTS:
            r["is_local_model"] = True
    # Inject agents with session dirs that don't appear in DB results yet.
    # This covers both local-model agents and cloud agents
    # whose traffic hasn't been recorded in the current time window.
    tracked_agents = {r.get("agent") for r in result}
    for agent_name, sessions_dir in AGENT_SESSION_DIRS.items():
        if agent_name not in tracked_agents:
            local = _get_local_session_status(agent_name)
            accumulated_turns = _get_local_accumulated_turns(agent_name)
            if accumulated_turns > 0 or (local and local.get("current_session_turns", 0) > 0):
                current_chars = local.get("current_history_chars", 0) if local else 0
                is_local = agent_name in LOCAL_MODEL_AGENTS
                result.append({
                    "agent": agent_name,
                    "turns": accumulated_turns,
                    "total_input_tokens": current_chars // 4,
                    "total_output_tokens": 0,
                    "total_cost": 0,
                    "total_cache_read": 0,
                    "total_cache_write": 0,
                    "avg_input_tokens": (current_chars // 4) // max(accumulated_turns, 1),
                    "is_local_model": is_local,
                })
    return result


@app.get("/api/session-status", dependencies=[Depends(verify_api_key)])
def api_session_status(agent: str | None = None):
    """Current session health and cost recommendation for an agent."""
    target = agent or AGENT_NAME
    limit = get_agent_setting(target, "session_char_limit") or 200_000
    if target in REMOTE_AGENTS:
        result = _get_remote_session_status(target)
        result["session_char_limit"] = limit
        return result
    # If DB is unavailable, skip the query and go straight to file-based reader
    if not _db_available:
        result = {"recommendation": "no_data"}
    else:
        try:
            result = query_session_status(target, char_limit=limit)
        except Exception as e:
            log.warning(f"DB query failed for {target}, falling back to file reader: {e}")
            result = {"recommendation": "no_data"}
    # If DB has no data, try reading session files directly (local model agents)
    if result.get("recommendation") == "no_data" and target in AGENT_SESSION_DIRS:
        local_result = _get_local_session_status(target)
        if local_result:
            local_result["session_char_limit"] = limit
            return local_result
    result["session_char_limit"] = limit
    if target in LOCAL_MODEL_AGENTS:
        result["is_local_model"] = True
    return result


@app.post("/api/reset-session", dependencies=[Depends(verify_api_key)])
def api_reset_session(agent: str):
    """Kill the largest active session for an agent (safety valve trigger)."""
    if not AGENT_SESSION_DIRS.get(agent) and agent not in REMOTE_AGENTS:
        return JSONResponse(
            {"error": f"Session reset not configured for agent: {agent}. Set AGENT_SESSION_DIRS env var."},
            status_code=400
        )
    return _kill_session(agent, reason="dashboard")


# ── Dashboard ────────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Token Spy — API Monitor</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, system-ui, sans-serif; background: #0d1117; color: #e6edf3; padding: 20px; }
  h1 { margin-bottom: 4px; font-size: 1.4em; }
  .subtitle { color: #8b949e; margin-bottom: 20px; font-size: 0.9em; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 20px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
  .card h3 { font-size: 0.85em; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
  .card .value { font-size: 2em; font-weight: 700; }
  .card .sub { color: #8b949e; font-size: 0.85em; margin-top: 4px; }
  .chart-container { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; margin-bottom: 20px; }
  .chart-container h3 { font-size: 0.95em; margin-bottom: 12px; }
  .chart-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }
  canvas { width: 100%; height: 280px; max-height: 300px; display: block; }
  .chart-legend { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 12px; color: #8b949e; font-size: 0.8em; }
  .chart-legend-item { display: inline-flex; align-items: center; gap: 6px; }
  .chart-legend-swatch { width: 10px; height: 10px; border-radius: 999px; display: inline-block; }
  table { width: 100%; border-collapse: collapse; font-size: 0.85em; }
  th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #21262d; }
  th { color: #8b949e; font-weight: 600; }
  tr:hover { background: #1c2128; }
  .cost { color: #f0883e; }
  .tokens { color: #58a6ff; }
  .cache { color: #3fb950; }
  .refresh-btn { background: #238636; border: none; color: white; padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 0.85em; }
  .refresh-btn:hover { background: #2ea043; }
  .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
  .status-badge { display: inline-block; padding: 4px 10px; border-radius: 12px; font-size: 0.75em; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
  .status-healthy { background: #238636; color: #fff; }
  .status-monitor { background: #9e6a03; color: #fff; }
  .status-compact_soon { background: #da3633; color: #fff; }
  .status-reset_recommended { background: #f85149; color: #fff; animation: pulse 1.5s infinite; }
  .status-cache_unstable { background: #a371f7; color: #fff; }
  .status-no_data { background: #30363d; color: #8b949e; }
  .session-panel { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 20px; }
  .session-card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
  .session-card h3 { font-size: 0.95em; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; }
  .session-card.local-model { border-color: #3fb95066; background: linear-gradient(135deg, #161b22 0%, #0d1a12 100%); }
  .session-card.local-model h3 .agent-type { font-size: 0.65em; color: #3fb950; font-weight: 600; margin-left: 8px; background: #3fb95018; border: 1px solid #3fb95044; padding: 2px 8px; border-radius: 12px; letter-spacing: 0.5px; text-transform: uppercase; }
  .session-stat { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #21262d; font-size: 0.9em; }
  .session-stat:last-child { border-bottom: none; }
  .session-stat .label { color: #8b949e; }
  .reset-btn { background: #da3633; border: none; color: white; padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 0.8em; margin-top: 10px; width: 100%; font-weight: 600; }
  .reset-btn:hover { background: #f85149; }
  .reset-btn:disabled { background: #30363d; color: #8b949e; cursor: not-allowed; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.6; } }
  /* Settings panel */
  .settings-panel { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; margin-bottom: 20px; }
  .settings-panel h3 { font-size: 0.95em; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; }
  .settings-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }
  .setting-group { padding: 12px; background: #0d1117; border-radius: 6px; border: 1px solid #21262d; }
  .setting-group h4 { font-size: 0.85em; color: #58a6ff; margin-bottom: 10px; text-transform: uppercase; letter-spacing: 0.5px; }
  .setting-row { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; border-bottom: 1px solid #21262d; }
  .setting-row:last-child { border-bottom: none; }
  .setting-row label { color: #8b949e; font-size: 0.85em; }
  .setting-row input { background: #161b22; border: 1px solid #30363d; color: #e6edf3; padding: 4px 8px; border-radius: 4px; width: 100px; text-align: right; font-size: 0.9em; }
  .setting-row input:focus { border-color: #58a6ff; outline: none; }
  .setting-row .unit { color: #8b949e; font-size: 0.8em; margin-left: 4px; min-width: 40px; }
  .save-btn { background: #238636; border: none; color: white; padding: 8px 20px; border-radius: 6px; cursor: pointer; font-size: 0.85em; font-weight: 600; margin-top: 12px; }
  .save-btn:hover { background: #2ea043; }
  .save-btn:disabled { background: #30363d; color: #8b949e; cursor: not-allowed; }
  .save-status { color: #3fb950; font-size: 0.85em; margin-left: 12px; display: none; }
  @media (max-width: 768px) { .chart-row, .session-panel { grid-template-columns: 1fr; } }
  #login-overlay{position:fixed;top:0;left:0;width:100%;height:100%;background:#0d1117;display:flex;align-items:center;justify-content:center;z-index:9999}
  #login-box{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:32px;width:340px;text-align:center}
  #login-box h2{color:#c9d1d9;margin:0 0 16px}
  #login-box input{width:100%;padding:8px 12px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;margin-bottom:12px;box-sizing:border-box}
  #login-box button{width:100%;padding:8px;background:#238636;color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:600}
  #login-box button:hover{background:#2ea043}
  #login-error{color:#f85149;font-size:13px;margin-top:8px;display:none}
</style>
</head>
<body>
<div id="login-overlay" style="display:none"><div id="login-box"><h2>Token Spy</h2><input id="login-key" type="password" placeholder="API Key" onkeydown="if(event.key==='Enter')attemptLogin()"><button onclick="attemptLogin()">Sign In</button><div id="login-error"></div></div></div>

<div class="header">
  <div>
    <h1>Token Spy — API Monitor</h1>
    <div class="subtitle">Real-time token usage, cost tracking &amp; session control</div>
  </div>
  <div>
    <select id="hours-select" style="background:#21262d;color:#e6edf3;border:1px solid #30363d;padding:6px;border-radius:6px;margin-right:8px;">
      <option value="1">Last 1h</option>
      <option value="6">Last 6h</option>
      <option value="24" selected>Last 24h</option>
      <option value="168">Last 7d</option>
      <option value="8760">All Time</option>
    </select>
    <button class="refresh-btn" onclick="toggleSettings()" style="margin-right:6px;background:#21262d;border:1px solid #30363d;">\u2699 Settings</button>
    <button class="refresh-btn" onclick="loadAll()">Refresh</button>
  </div>
</div>

<div class="session-panel" id="session-panel"></div>

<div class="settings-panel" id="settings-panel" style="display:none;">
  <h3>Session Control Settings <span class="save-status" id="save-status-inline"></span></h3>
  <div class="settings-grid" id="settings-grid">
    <div class="setting-group">
      <h4>Global Defaults</h4>
      <div class="setting-row">
        <label>Session char limit</label>
        <div><input type="number" id="set-global-limit" step="10000" min="10000" oninput="updateTokenHint(this,'set-global-limit-tok')"> <span class="unit">chars</span> <span id="set-global-limit-tok" class="unit" style="color:#58a6ff"></span></div>
      </div>
      <div class="setting-row">
        <label>Poll frequency</label>
        <div><input type="number" id="set-global-poll" step="1" min="1" max="60"> <span class="unit">min</span></div>
      </div>
    </div>
    <!-- Agent-specific settings will be inserted here dynamically -->
  </div>
  <div style="margin-top:12px; display:flex; align-items:center;">
    <button class="save-btn" id="save-settings-btn" onclick="saveSettings()">Save Settings</button>
  </div>
</div>

<div class="grid" id="summary-cards"></div>

<div class="chart-row">
  <div class="chart-container">
    <h3>Cost Per Turn (Session Timeline)</h3>
    <canvas id="cost-chart" data-height="280"></canvas>
    <div class="chart-legend" id="cost-legend"></div>
  </div>
  <div class="chart-container">
    <h3>History Growth (chars)</h3>
    <canvas id="history-chart" data-height="280"></canvas>
    <div class="chart-legend" id="history-legend"></div>
  </div>
</div>

<div class="chart-row">
  <div class="chart-container">
    <h3>Token Usage Over Time</h3>
    <canvas id="tokens-chart" data-height="280"></canvas>
    <div class="chart-legend" id="tokens-legend"></div>
  </div>
  <div class="chart-container">
    <h3>Cost Breakdown by Type</h3>
    <canvas id="breakdown-chart" data-height="280"></canvas>
    <div class="chart-legend" id="breakdown-legend"></div>
  </div>
</div>

<div class="chart-container">
  <h3>Cumulative Cost Over Time</h3>
  <canvas id="cumulative-chart" data-height="260" style="height:260px;max-height:260px;"></canvas>
  <div class="chart-legend" id="cumulative-legend"></div>
</div>

<div class="chart-container">
  <h3>Recent Turns</h3>
  <table>
    <thead>
      <tr>
        <th>Time</th>
        <th>Agent</th>
        <th>Model</th>
        <th>Input Tok</th>
        <th>Output Tok</th>
        <th>Cache Read</th>
        <th>Cache Write</th>
        <th>Sys Prompt</th>
        <th>History</th>
        <th>Cost</th>
        <th>Duration</th>
      </tr>
    </thead>
    <tbody id="recent-table"></tbody>
  </table>
</div>

<script src="/dashboard-assets/charts.js"></script>
<script>
function _getApiKey(){return sessionStorage.getItem('token_spy_api_key')||''}
function _authHeaders(){return {Authorization:'Bearer '+_getApiKey()}}
function _clearAuth(){sessionStorage.removeItem('token_spy_api_key');document.getElementById('login-overlay').style.display='flex'}
async function _authFetch(url,opts={}){opts.headers=Object.assign({},opts.headers||{},_authHeaders());const r=await fetch(url,opts);if(r.status===401||r.status===403){_clearAuth();throw new Error('Unauthorized')}return r}
function attemptLogin(){const k=document.getElementById('login-key').value.trim();if(!k)return;fetch('/api/summary',{headers:{Authorization:'Bearer '+k}}).then(r=>{if(r.ok){sessionStorage.setItem('token_spy_api_key',k);document.getElementById('login-overlay').style.display='none';document.getElementById('login-error').style.display='none';loadAll()}else{document.getElementById('login-error').textContent='Invalid API key';document.getElementById('login-error').style.display='block'}}).catch(()=>{document.getElementById('login-error').textContent='Connection error';document.getElementById('login-error').style.display='block'})}
if(!_getApiKey()){document.getElementById('login-overlay').style.display='flex'}
let tokensChart = null, breakdownChart = null, costChart = null, historyChart = null, cumulativeChart = null;

function getHours() {
  return parseInt(document.getElementById('hours-select').value) || 24;
}

async function loadAll() {
  const hours = getHours();
  const [summaryRes, usageRes] = await Promise.all([
    _authFetch('/api/summary?hours=' + hours),
    _authFetch('/api/usage?hours=' + hours + '&limit=500'),
  ]);
  const summary = await summaryRes.json();
  const usage = await usageRes.json();
  // Dynamically discover agents from data (usage + summary)
  const agents = [...new Set([...usage.map(u => u.agent), ...summary.map(s => s.agent)])];
  // Fetch session status for each discovered agent
  const sessionPromises = agents.map(agent => _authFetch('/api/session-status?agent=' + encodeURIComponent(agent)));
  const sessionResults = await Promise.all(sessionPromises);
  const sessions = await Promise.all(sessionResults.map(r => r.json()));
  window._agents = agents;
  window._scl = sessions.reduce((max, s) => Math.max(max, s.session_char_limit || 200000), 200000);
  renderSessionPanel(sessions);
  renderSummary(summary);
  renderCostChart(usage);
  renderHistoryChart(usage);
  renderTokensChart(usage);
  renderBreakdownChart(usage);
  renderCumulativeChart(usage);
  renderTable(usage.slice(0, 50));
}

function parseTs(ts) {
  if (!ts) return new Date(NaN);
  return new Date(ts);
}

function formatTimeTick(ts) {
  if (!(ts instanceof Date) || Number.isNaN(ts.getTime())) return '\\u2014';
  if (getHours() >= 168) {
    return ts.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }
  return ts.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function fmt(n) {
  if (n == null) return '\\u2014';
  if (n >= 1000000) return (n/1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n/1000).toFixed(1) + 'K';
  return Math.round(n).toLocaleString();
}

function fmtCost(n) {
  if (n == null) return '$0.00';
  return '$' + n.toFixed(4);
}

function recLabel(rec) {
  const labels = {
    healthy: 'Healthy', monitor: 'Monitor', compact_soon: 'Compact Soon',
    reset_recommended: 'Reset Needed', cache_unstable: 'Cache Unstable', no_data: 'No Data'
  };
  return labels[rec] || rec;
}

async function resetSession(agent) {
  if (!confirm('Reset ' + agent + '? This will kill the active session and force a fresh start.')) return;
  const btn = document.getElementById('reset-' + agent);
  if (btn) { btn.disabled = true; btn.textContent = 'Resetting...'; }
  try {
    const res = await _authFetch('/api/reset-session?agent=' + encodeURIComponent(agent), { method: 'POST' });
    const data = await res.json();
    if (data.action === 'killed') {
      if (btn) { btn.textContent = 'Reset — restarting...'; }
      setTimeout(loadAll, 3000);
    } else {
      alert('Reset: ' + (data.reason || 'unknown'));
      if (btn) { btn.disabled = false; btn.textContent = 'Reset Session'; }
    }
  } catch (e) {
    alert('Reset failed: ' + e.message);
    if (btn) { btn.disabled = false; btn.textContent = 'Reset Session'; }
  }
}

function renderSessionPanel(sessions) {
  const el = document.getElementById('session-panel');
  el.innerHTML = sessions.map(s => {
    const rec = s.recommendation || 'no_data';
    const showReset = ['reset_recommended', 'compact_soon', 'monitor'].includes(rec);
    const isLocal = s.is_local_model;
    const cardClass = 'session-card' + (isLocal ? ' local-model' : '');
    const agentLabel = s.agent + (isLocal ? '<span class="agent-type">\u26A1 Self-Hosted</span>' : '');
    const limit = s.session_char_limit || 200000;
    const pct = limit > 0 ? Math.round((s.current_history_chars / limit) * 100) : 0;
    const barColor = pct > 80 ? '#da3633' : pct > 60 ? '#9e6a03' : '#238636';
    const historyWarn = s.current_history_chars > limit;
    return '<div class="' + cardClass + '">' +
      '<h3>' + agentLabel + ' <span class="status-badge status-' + rec + '">' + recLabel(rec) + '</span></h3>' +
      '<div class="session-stat"><span class="label">Session turns</span><span>' + s.current_session_turns + '</span></div>' +
      '<div class="session-stat"><span class="label">History size</span><span' + (historyWarn ? ' style="color:#da3633;font-weight:600"' : '') + '>' + fmt(s.current_history_chars) + ' / ' + fmt(limit) + ' (' + pct + '%)</span></div>' +
      '<div class="session-stat" style="font-size:0.8em;color:#8b949e;margin-top:-4px"><span class="label"></span><span>~' + fmt(Math.round(s.current_history_chars / 4)) + ' / ' + fmt(Math.round(limit / 4)) + ' tokens</span></div>' +
      '<div style="background:#21262d;border-radius:3px;height:4px;margin:2px 0 6px"><div style="background:' + barColor + ';height:100%;border-radius:3px;width:' + Math.min(pct, 100) + '%"></div></div>' +
      (isLocal ?
        '<div class="session-stat"><span class="label">Inference</span><span style="color:#3fb950">\u26A1 Local GPU \u2014 $0.00/token</span></div>'
      :
        '<div class="session-stat"><span class="label">Last turn cost</span><span class="cost">' + fmtCost(s.last_turn_cost) + '</span></div>' +
        '<div class="session-stat"><span class="label">Avg cost (last 5)</span><span class="cost">' + fmtCost(s.avg_cost_last_5) + '</span></div>' +
        '<div class="session-stat"><span class="label">Cache write %</span><span>' + (s.cache_write_pct_last_5 * 100).toFixed(1) + '%</span></div>' +
        '<div class="session-stat"><span class="label">Session total cost</span><span class="cost">' + fmtCost(s.cost_since_last_reset) + '</span></div>'
      ) +
      (showReset ? '<button class="reset-btn" id="reset-' + s.agent + '" onclick="resetSession(\\'' + s.agent + '\\')">Reset Session</button>' : '') +
    '</div>';
  }).join('');
}

function renderSummary(data) {
  const el = document.getElementById('summary-cards');
  if (!data.length) {
    el.innerHTML = '<div class="card"><h3>No data</h3><div class="value">\\u2014</div><div class="sub">No turns recorded in this period</div></div>';
    return;
  }
  let totalCost = 0, totalInput = 0, totalOutput = 0, totalTurns = 0, totalCacheRead = 0, totalCacheWrite = 0;
  data.forEach(d => {
    totalCost += d.total_cost || 0;
    totalInput += d.total_input_tokens || 0;
    totalOutput += d.total_output_tokens || 0;
    totalTurns += d.turns || 0;
    totalCacheRead += d.total_cache_read || 0;
    totalCacheWrite += d.total_cache_write || 0;
  });
  const totalCacheTokens = totalCacheRead + totalCacheWrite;
  const cacheReadPct = totalCacheTokens > 0 ? (totalCacheRead / totalCacheTokens * 100).toFixed(1) : '0';

  let html =
    '<div class="card"><h3>Total Cost</h3><div class="value cost">' + fmtCost(totalCost) + '</div><div class="sub">' + totalTurns + ' turns</div></div>' +
    '<div class="card"><h3>Avg Cost/Turn</h3><div class="value cost">' + fmtCost(totalCost / Math.max(totalTurns, 1)) + '</div><div class="sub">' + fmt(totalInput / Math.max(totalTurns, 1)) + ' in/turn</div></div>' +
    '<div class="card"><h3>Output Tokens</h3><div class="value tokens">' + fmt(totalOutput) + '</div><div class="sub">' + fmt(totalOutput / Math.max(totalTurns, 1)) + '/turn</div></div>' +
    '<div class="card"><h3>Cache Efficiency</h3><div class="value cache">' + cacheReadPct + '%</div><div class="sub">' + fmt(totalCacheRead) + ' reads / ' + fmt(totalCacheWrite) + ' writes</div></div>';
  data.forEach(d => {
    if (d.is_local_model) {
      html += '<div class="card" style="border-color:#3fb95044;background:linear-gradient(135deg,#161b22,#0d1a12)"><h3>' + d.agent.toUpperCase() + ' <span style="color:#3fb950;font-size:10px;background:#3fb95018;border:1px solid #3fb95044;padding:2px 7px;border-radius:10px;font-weight:600;letter-spacing:0.5px">\u26A1 SELF-HOSTED</span></h3><div class="value">' + d.turns + ' turns</div><div class="sub" style="color:#3fb950">$0.00 \u2014 local GPU | ~' + fmt(d.avg_input_tokens) + ' tokens/turn</div></div>';
    } else {
      html += '<div class="card"><h3>' + d.agent.toUpperCase() + '</h3><div class="value">' + d.turns + ' turns</div><div class="sub">' + fmtCost(d.total_cost) + ' | avg ' + fmt(d.avg_input_tokens) + ' in/turn</div></div>';
    }
  });
  el.innerHTML = html;
}

function renderCostChart(usage) {
  const canvas = document.getElementById('cost-chart');
  if (costChart) costChart.destroy();
  const sorted = [...usage].reverse();
  const colors = ['#58a6ff', '#f0883e', '#3fb950', '#a371f7', '#da3633', '#e6edf3'];
  const agents = [...new Set(usage.map(u => u.agent))];
  const series = agents.map((agent, i) => {
    const agentData = sorted.filter(u => u.agent === agent);
    return {
      label: agent,
      points: agentData.map(u => ({x: parseTs(u.timestamp), y: u.estimated_cost_usd || 0})),
      color: colors[i % colors.length],
      pointRadius: 2,
    };
  });
  costChart = TokenSpyCharts.line(canvas, {
    legendEl: document.getElementById('cost-legend'),
    series,
    yFormatter: v => '$' + v.toFixed(2),
    xFormatter: formatTimeTick,
  });
}

function renderHistoryChart(usage) {
  const canvas = document.getElementById('history-chart');
  if (historyChart) historyChart.destroy();
  const sorted = [...usage].reverse();
  const colors = ['#58a6ff', '#f0883e', '#3fb950', '#a371f7', '#da3633', '#e6edf3'];
  const bgColors = ['rgba(88,166,255,0.08)', 'rgba(240,136,62,0.08)', 'rgba(63,185,80,0.08)', 'rgba(163,113,247,0.08)', 'rgba(218,54,51,0.08)', 'rgba(230,237,243,0.08)'];
  const agents = [...new Set(usage.map(u => u.agent))];
  const series = agents.map((agent, i) => {
    const agentData = sorted.filter(u => u.agent === agent);
    return {
      label: agent,
      points: agentData.map(u => ({x: parseTs(u.timestamp), y: u.conversation_history_chars || 0})),
      color: colors[i % colors.length],
      fillColor: bgColors[i % bgColors.length],
      pointRadius: 1,
    };
  });
  historyChart = TokenSpyCharts.line(canvas, {
    legendEl: document.getElementById('history-legend'),
    series,
    yFormatter: v => v >= 1000 ? (v/1000).toFixed(0) + 'K' : String(Math.round(v)),
    xFormatter: formatTimeTick,
    thresholdLines: [
      { value: window._scl || 200000, color: '#f0883e', label: fmt(window._scl || 200000) + ' (~' + fmt(Math.round((window._scl || 200000)/4)) + ' tok) auto-reset' },
      { value: (window._scl || 200000) * 2.5, color: '#da3633', label: fmt((window._scl || 200000) * 2.5) + ' (~' + fmt(Math.round((window._scl || 200000)*2.5/4)) + ' tok) danger' },
    ],
  });
}

function renderTokensChart(usage) {
  const canvas = document.getElementById('tokens-chart');
  if (tokensChart) tokensChart.destroy();
  const sorted = [...usage].reverse();
  const labels = sorted.map(u => parseTs(u.timestamp));
  tokensChart = TokenSpyCharts.stackedBar(canvas, {
    legendEl: document.getElementById('tokens-legend'),
    labels,
    labelFormatter: formatTimeTick,
    datasets: [
      { label: 'Input', data: sorted.map(u => u.input_tokens || 0), color: '#58a6ff' },
      { label: 'Output', data: sorted.map(u => u.output_tokens || 0), color: '#f0883e' },
      { label: 'Cache Read', data: sorted.map(u => u.cache_read_tokens || 0), color: '#3fb950' },
      { label: 'Cache Write', data: sorted.map(u => u.cache_write_tokens || 0), color: '#da3633' },
    ],
  });
}

function renderBreakdownChart(usage) {
  const canvas = document.getElementById('breakdown-chart');
  if (breakdownChart) breakdownChart.destroy();
  let cacheRead = 0, cacheWrite = 0, input = 0, output = 0;
  usage.forEach(u => {
    cacheRead += (u.cache_read_tokens || 0);
    cacheWrite += (u.cache_write_tokens || 0);
    input += (u.input_tokens || 0);
    output += (u.output_tokens || 0);
  });
  const total = cacheRead + cacheWrite + input + output || 1;
  const pct = v => (v / total * 100).toFixed(1) + '%';
  breakdownChart = TokenSpyCharts.donut(canvas, {
    legendEl: document.getElementById('breakdown-legend'),
    labels: [
      'Cache Read ' + pct(cacheRead),
      'Cache Write ' + pct(cacheWrite),
      'Input ' + pct(input),
      'Output ' + pct(output),
    ],
    values: [cacheRead, cacheWrite, input, output],
    colors: ['#3fb950', '#da3633', '#58a6ff', '#f0883e'],
    centerText: fmt(total) + ' total',
  });
}

function renderCumulativeChart(usage) {
  const canvas = document.getElementById('cumulative-chart');
  if (cumulativeChart) cumulativeChart.destroy();
  const sorted = [...usage].reverse();
  const colors = ['#58a6ff', '#f0883e', '#3fb950', '#a371f7', '#da3633'];
  const agents = [...new Set(usage.map(u => u.agent))];
  // Build running totals per agent
  const running = {};
  agents.forEach(a => running[a] = 0);
  let runningTotal = 0;
  const agentData = {};
  agents.forEach(a => agentData[a] = []);
  const totalData = [];
  sorted.forEach(u => {
    const cost = u.estimated_cost_usd || 0;
    runningTotal += cost;
    const ts = parseTs(u.timestamp);
    totalData.push({x: ts, y: runningTotal});
    if (running[u.agent] !== undefined) {
      running[u.agent] += cost;
      agentData[u.agent].push({x: ts, y: running[u.agent]});
    }
  });
  const datasets = [
    { label: 'Total', points: totalData, color: '#e6edf3', lineWidth: 2, pointRadius: 0, fillColor: 'rgba(230,237,243,0.05)' },
    ...agents.map((agent, i) => ({
      label: agent,
      points: agentData[agent],
      color: colors[i % colors.length],
      lineWidth: 1.5,
      pointRadius: 0,
    }))
  ];
  cumulativeChart = TokenSpyCharts.line(canvas, {
    legendEl: document.getElementById('cumulative-legend'),
    series: datasets,
    yFormatter: v => '$' + v.toFixed(2),
    xFormatter: formatTimeTick,
  });
}

function renderTable(usage) {
  const el = document.getElementById('recent-table');
  el.innerHTML = usage.map(u => {
    const t = parseTs(u.timestamp).toLocaleString();
    const model = (u.model || '').startsWith('claude-') ? (u.model || '').replace('claude-', '').split('-2')[0] : (u.model || '');
    return '<tr>' +
      '<td>' + t + '</td>' +
      '<td>' + u.agent + '</td>' +
      '<td>' + model + '</td>' +
      '<td class="tokens">' + fmt(u.input_tokens) + '</td>' +
      '<td class="tokens">' + fmt(u.output_tokens) + '</td>' +
      '<td class="cache">' + fmt(u.cache_read_tokens) + '</td>' +
      '<td style="color:#da3633">' + fmt(u.cache_write_tokens) + '</td>' +
      '<td>' + fmt(u.system_prompt_total_chars) + '</td>' +
      '<td>' + fmt(u.conversation_history_chars) + '</td>' +
      '<td class="cost">' + fmtCost(u.estimated_cost_usd) + '</td>' +
      '<td>' + (u.duration_ms ? (u.duration_ms/1000).toFixed(1) + 's' : '\\u2014') + '</td>' +
    '</tr>';
  }).join('');
}


// ── Settings Panel ────────────────────────────────────────────────────────────

function updateTokenHint(input, hintId) {
  const hint = document.getElementById(hintId);
  if (!hint) return;
  const val = parseInt(input.value, 10);
  hint.textContent = val ? '(~' + fmt(Math.round(val / 4)) + ' tokens)' : '';
}

function toggleSettings() {
  const panel = document.getElementById('settings-panel');
  const showing = panel.style.display === 'none';
  panel.style.display = showing ? 'block' : 'none';
  if (showing) loadSettingsUI();
}

async function loadSettingsUI() {
  try {
    const res = await _authFetch('/api/settings');
    const s = await res.json();
    document.getElementById('set-global-limit').value = s.session_char_limit || '';
    document.getElementById('set-global-poll').value = s.poll_interval_minutes || '';
    window._scl = s.session_char_limit || 200000;
    // Dynamically build agent-specific settings
    const grid = document.getElementById('settings-grid');
    // Remove existing agent groups (keep global defaults which is first)
    while (grid.children.length > 1) {
      grid.removeChild(grid.lastChild);
    }
    const agents = Object.keys(s.agents || {});
    agents.forEach((agent, idx) => {
      const cfg = s.agents[agent];
      const div = document.createElement('div');
      div.className = 'setting-group';
      const safeId = agent.replace(/[^a-zA-Z0-9]/g, '-');
      div.innerHTML =
        '<h4>' + agent + ' Override</h4>' +
        '<div class="setting-row">' +
          '<label>Session char limit</label>' +
          '<div><input type="number" id="set-' + safeId + '-limit" step="10000" min="10000" placeholder="inherit" > <span class="unit">chars</span> <span id="set-' + safeId + '-limit-tok" class="unit" style="color:#58a6ff"></span></div>' +
        '</div>' +
        '<div class="setting-row">' +
          '<label>Poll frequency</label>' +
          '<div><input type="number" id="set-' + safeId + '-poll" step="1" min="1" max="60" placeholder="inherit"> <span class="unit">min</span></div>' +
        '</div>';
      grid.appendChild(div);
      // Set values
      document.getElementById('set-' + safeId + '-limit').value = cfg.session_char_limit != null ? cfg.session_char_limit : '';
      document.getElementById('set-' + safeId + '-poll').value = cfg.poll_interval_minutes != null ? cfg.poll_interval_minutes : '';
      if (cfg.session_char_limit != null) {
        updateTokenHint(document.getElementById('set-' + safeId + '-limit'), 'set-' + safeId + '-limit-tok');
      }
    });
    // Update token hint for global
    updateTokenHint(document.getElementById('set-global-limit'), 'set-global-limit-tok');
  } catch (e) {
    console.error('Failed to load settings:', e);
  }
}

async function saveSettings() {
  const btn = document.getElementById('save-settings-btn');
  const status = document.getElementById('save-status-inline');
  btn.disabled = true;
  btn.textContent = 'Saving...';

  const getVal = (id) => {
    const el = document.getElementById(id);
    if (!el) return null;
    const v = el.value;
    return v === '' ? null : parseInt(v, 10);
  };

  // Build agents object from current UI
  const agents = {};
  const groups = document.querySelectorAll('.setting-group');
  groups.forEach(g => {
    const h4 = g.querySelector('h4');
    if (!h4 || h4.textContent === 'Global Defaults') return;
    const agent = h4.textContent.replace(' Override', '');
    const safeId = agent.replace(/[^a-zA-Z0-9]/g, '-');
    agents[agent] = {
      session_char_limit: getVal('set-' + safeId + '-limit'),
      poll_interval_minutes: getVal('set-' + safeId + '-poll'),
    };
  });

  const body = {
    session_char_limit: getVal('set-global-limit'),
    poll_interval_minutes: getVal('set-global-poll'),
    agents: agents,
  };

  try {
    const res = await _authFetch('/api/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (res.ok) {
      status.textContent = '\u2705 Saved!';
      status.style.display = 'inline';
      status.style.color = '#3fb950';
      setTimeout(() => { status.style.display = 'none'; }, 3000);
      window._scl = body.session_char_limit || window._scl;
      loadAll();
    } else {
      const err = await res.json();
      status.textContent = '\u274c ' + (err.error || 'unknown error');
      status.style.display = 'inline';
      status.style.color = '#f85149';
    }
  } catch (e) {
    status.textContent = '\u274c ' + e.message;
    status.style.display = 'inline';
    status.style.color = '#f85149';
  }
  btn.disabled = false;
  btn.textContent = 'Save Settings';
}

document.getElementById('hours-select').addEventListener('change', loadAll);
if(_getApiKey()){loadAll()}
setInterval(function(){if(_getApiKey())loadAll()}, 30000);
</script>
</body>
</html>"""


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML


@app.get("/dashboard-assets/charts.js")
def dashboard_charts_asset():
    return FileResponse(Path(__file__).with_name("dashboard_charts.js"), media_type="application/javascript")


# ── SSE Token Events Stream ─────────────────────────────────────────────────

@app.get("/token_events", dependencies=[Depends(verify_api_key)])
async def token_events(request: Request):
    """Stream token usage events as Server-Sent Events."""
    async def event_stream():
        last_id = None
        while True:
            try:
                # Query recent events
                events = query_recent_events(limit=50, after_id=last_id)

                for event in events:
                    # Format event as SSE
                    event_data = {
                        "type": "token_usage",
                        "session_id": event.get("session_id", ""),
                        "model": event.get("model", ""),
                        "provider": event.get("provider", ""),
                        "input_tokens": event.get("input_tokens", 0),
                        "output_tokens": event.get("output_tokens", 0),
                        "total_tokens": event.get("total_tokens", 0),
                        "cost_usd": float(event.get("cost_usd", 0) or 0),
                        "timestamp": event.get("timestamp", ""),
                        "agent_name": event.get("agent_name", AGENT_NAME)
                    }

                    yield f"data: {json.dumps(event_data)}\n\n"
                    last_id = event.get("id")

                # Heartbeat to keep connection alive
                yield ":heartbeat\n\n"

                # Wait before next poll
                await asyncio.sleep(2)

            except Exception as e:
                log.error(f"SSE stream error: {e}")
                yield f"event: error\ndata: {json.dumps({'error': 'Stream error'})}\n\n"
                await asyncio.sleep(5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Catch-all for other endpoints ────────────────────────────────────────────

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"], dependencies=[Depends(verify_api_key)])
async def proxy_other(request: Request, path: str):
    """Forward any other authenticated requests to upstream."""
    # Use the correct upstream client based on provider
    if _uses_openai_upstream():
        client = get_moonshot_client()
        headers = _build_openai_upstream_headers(request)
    else:
        client = get_http_client()
        headers = _build_anthropic_upstream_headers(request)

    body = await request.body()
    try:
        resp = await client.request(
            method=request.method,
            url=f"/{path}",
            content=body if body else None,
            headers=headers,
        )
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=dict(resp.headers),
        )
    except Exception as e:
        log.error(f"Proxy passthrough error: {e}")
        return JSONResponse(status_code=502, content={"error": "Upstream request failed"})
