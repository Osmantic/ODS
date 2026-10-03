"""Resolve setup inference and destination-bound credentials from one snapshot."""
from urllib.parse import urlsplit, urlunsplit

from config import read_live_env_values

KEYS = (
    "LLM_API_URL", "OLLAMA_URL", "LLM_API_BASE_PATH", "LLM_MODEL", "LLM_BACKEND",
    "GGUF_FILE", "ODS_MODEL_SWITCHBOARD", "LITELLM_KEY", "LITELLM_MASTER_KEY",
    "OPEN_WEBUI_LLM_BASE_URL", "OPEN_WEBUI_LLM_API_KEY", "OPEN_WEBUI_TASK_MODEL",
    "LEMONADE_CONTAINER_BASE_URL", "LEMONADE_BASE_URL", "LEMONADE_API_BASE_PATH",
    "LEMONADE_API_KEY", "LITELLM_LEMONADE_API_KEY", "LEMONADE_MODEL",
    "AMD_INFERENCE_RUNTIME", "AMD_INFERENCE_PORT", "ODS_MODE",
)


def api_base(raw: str, path: str = "/v1") -> str:
    raw = raw.strip().rstrip("/")
    if "\\" in raw or any(ord(c) < 32 for c in raw + path):
        raise ValueError("invalid inference URL")
    parsed = urlsplit(raw)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.port == 0):
        raise ValueError("invalid inference URL")
    path = "/" + (path.strip() or "/v1").strip("/")
    if any(c in path for c in "?#\\") or ".." in path.split("/"):
        raise ValueError("invalid inference API path")
    current = parsed.path.rstrip("/")
    if not current.endswith(("/v1", path)):
        current += path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, current, "", ""))


def same_endpoint(left: str, right: str) -> bool:
    def identity(url):
        value = urlsplit(url)
        return value.scheme, value.hostname, value.port or (443 if value.scheme == "https" else 80), value.path
    return identity(left) == identity(right)


def resolve_chat_route(default_url: str) -> tuple[str, str, dict[str, str]]:
    env = {key: value.strip() for key, value in read_live_env_values(KEYS).items()}
    lemonade_runtime = "lemonade" in {env["LLM_BACKEND"].lower(), env["AMD_INFERENCE_RUNTIME"].lower(), env["ODS_MODE"].lower()}
    base = api_base(env["LLM_API_URL"] or env["OLLAMA_URL"] or default_url,
                    env["LLM_API_BASE_PATH"] or ("/api/v1" if lemonade_runtime else "/v1"))
    target = urlsplit(base)
    local_gateway = target.scheme == "http" and target.hostname in ("litellm", "ods-litellm") and target.port == 4000
    if local_gateway:
        # LiteLLM's public API path is independent of its backend's path.
        base = urlunsplit((target.scheme, target.netloc, "/v1", "", ""))
    paired_webui = False
    if env["OPEN_WEBUI_LLM_BASE_URL"]:
        paired_webui = same_endpoint(base, api_base(env["OPEN_WEBUI_LLM_BASE_URL"]))
    lemonade_base = env["LEMONADE_CONTAINER_BASE_URL"] or env["LEMONADE_BASE_URL"]
    paired_lemonade = bool(lemonade_base) and same_endpoint(
        base, api_base(lemonade_base, env["LEMONADE_API_BASE_PATH"] or "/api/v1"))
    managed_lemonade = (
        lemonade_runtime and target.scheme == "http"
        and target.hostname in ("host.docker.internal", "llama-server", "ods-llama-server")
        and str(target.port) == (env["AMD_INFERENCE_PORT"] or "8080")
    )
    key = ""
    model = env["LLM_MODEL"] or "qwen3-coder-next"
    if paired_lemonade or managed_lemonade:
        model = env["LEMONADE_MODEL"] or ("extra."+env["GGUF_FILE"] if env["GGUF_FILE"] else model)
    if local_gateway:
        key = env["LITELLM_KEY"] or env["LITELLM_MASTER_KEY"]
        model = (env["OPEN_WEBUI_TASK_MODEL"] if paired_webui else "") or (
            "ods/current" if env["ODS_MODEL_SWITCHBOARD"] == "enabled" or env["LLM_BACKEND"] == "external" else "default")
    elif paired_webui:
        key = env["OPEN_WEBUI_LLM_API_KEY"]
        model = env["OPEN_WEBUI_TASK_MODEL"] or model
    elif paired_lemonade or managed_lemonade:
        key = env["LEMONADE_API_KEY"] or env["LITELLM_LEMONADE_API_KEY"]
    elif target.hostname in ("llama-server", "ods-llama-server", "host.docker.internal") and env["LLM_BACKEND"] != "external":
        model = env["GGUF_FILE"] or model
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return base + "/chat/completions", model, headers
