"""Resolve setup inference and destination-bound credentials from one snapshot."""
from urllib.parse import urlsplit, urlunsplit

from config import read_live_env_values

KEYS = (
    "LLM_API_URL", "OLLAMA_URL", "LLM_API_BASE_PATH", "LLM_MODEL", "LLM_BACKEND",
    "GGUF_FILE", "ODS_MODEL_SWITCHBOARD", "LITELLM_KEY", "LITELLM_MASTER_KEY",
    "OPEN_WEBUI_LLM_BASE_URL", "OPEN_WEBUI_LLM_API_KEY", "OPEN_WEBUI_TASK_MODEL",
    "AMD_INFERENCE_LOCATION",
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
    backend = env["LLM_BACKEND"].lower()
    raw = env["LLM_API_URL"] or env["OLLAMA_URL"] or default_url
    if (env["AMD_INFERENCE_LOCATION"].lower() == "host" and backend != "external"
            and urlsplit(raw).hostname not in ("litellm", "ods-litellm")):
        # A Windows-hosted llama-server requires its key, which only LiteLLM
        # holds; setup chat reaches it through the gateway.
        raw = "http://litellm:4000"
    base = api_base(raw, env["LLM_API_BASE_PATH"] or "/v1")
    target = urlsplit(base)
    local_gateway = target.scheme == "http" and target.hostname in ("litellm", "ods-litellm") and target.port == 4000
    if local_gateway:
        # LiteLLM's public API path is independent of its backend's path.
        base = urlunsplit((target.scheme, target.netloc, "/v1", "", ""))
    paired_webui = False
    if env["OPEN_WEBUI_LLM_BASE_URL"]:
        paired_webui = same_endpoint(base, api_base(env["OPEN_WEBUI_LLM_BASE_URL"]))
    key = ""
    model = env["LLM_MODEL"] or "qwen3-coder-next"
    if local_gateway:
        key = env["LITELLM_KEY"] or env["LITELLM_MASTER_KEY"]
        model = (env["OPEN_WEBUI_TASK_MODEL"] if paired_webui else "") or (
            "ods/current" if env["ODS_MODEL_SWITCHBOARD"] == "enabled" or backend == "external" else "default")
    elif paired_webui:
        key = env["OPEN_WEBUI_LLM_API_KEY"]
        model = env["OPEN_WEBUI_TASK_MODEL"] or model
    elif target.hostname in ("llama-server", "ods-llama-server", "host.docker.internal") and backend != "external":
        # Every managed llama-server serves its model as --alias <GGUF_FILE>.
        model = env["GGUF_FILE"] or model
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return base + "/chat/completions", model, headers
