"""Shared configuration and manifest loading for Dream Server Dashboard API."""

import json
import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# --- Paths ---

INSTALL_DIR = os.environ.get("DREAM_INSTALL_DIR", os.path.expanduser("~/dream-server"))
DATA_DIR = os.environ.get("DREAM_DATA_DIR", os.path.expanduser("~/.dream-server"))
EXTENSIONS_DIR = Path(
    os.environ.get(
        "DREAM_EXTENSIONS_DIR",
        str(Path(INSTALL_DIR) / "extensions" / "services")
    )
)

DEFAULT_SERVICE_HOST = os.environ.get("SERVICE_HOST", "host.docker.internal")
GPU_BACKEND = os.environ.get("GPU_BACKEND", "nvidia")


def _read_env_from_file(key: str) -> str:
    """Read a variable from the .env file when not available in process environment."""
    env_path = Path(INSTALL_DIR) / ".env"
    try:
        for line in env_path.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return ""


# --- Manifest Loading ---


def _read_manifest_file(path: Path) -> dict[str, Any]:
    """Load a JSON or YAML extension manifest file."""
    text = path.read_text()
    if path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("Manifest root must be an object")
    return data


def load_extension_manifests(
    manifest_dir: Path, gpu_backend: str,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    """Load service and feature definitions from extension manifests.

    Returns a 3-tuple: (services, features, errors) where *errors* is a list
    of ``{"file": ..., "error": ...}`` dicts for manifests that failed to load.
    """
    services: dict[str, dict[str, Any]] = {}
    features: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    loaded = 0

    if not manifest_dir.exists():
        logger.info("Extension manifest directory not found: %s", manifest_dir)
        return services, features, errors

    manifest_files: list[Path] = []
    for item in sorted(manifest_dir.iterdir()):
        if item.is_dir():
            for name in ("manifest.yaml", "manifest.yml", "manifest.json"):
                candidate = item / name
                if candidate.exists():
                    manifest_files.append(candidate)
                    break
        elif item.suffix.lower() in (".yaml", ".yml", ".json"):
            manifest_files.append(item)

    for path in manifest_files:
        try:
            # Skip disabled extensions (compose.yaml.disabled convention)
            ext_dir = path.parent
            if (ext_dir / "compose.yaml.disabled").exists() or (ext_dir / "compose.yml.disabled").exists():
                logger.debug("Skipping disabled extension: %s", ext_dir.name)
                continue

            manifest = _read_manifest_file(path)
            if manifest.get("schema_version") != "dream.services.v1":
                logger.warning("Skipping manifest with unsupported schema_version: %s", path)
                errors.append({"file": str(path), "error": "Unsupported schema_version"})
                continue

            service = manifest.get("service")
            if isinstance(service, dict):
                service_id = service.get("id")
                if not service_id:
                    raise ValueError("service.id is required")
                supported = service.get("gpu_backends", ["amd", "nvidia", "apple"])
                if gpu_backend == "apple":
                    if service.get("type") == "host-systemd":
                        continue  # Linux-only service, not available on macOS
                    # All docker services run on macOS regardless of gpu_backends declaration
                elif gpu_backend not in supported and "all" not in supported:
                    continue

                host_env = service.get("host_env")
                default_host = service.get("default_host", "localhost")
                host = os.environ.get(host_env, default_host) if host_env else default_host

                ext_port_env = service.get("external_port_env")
                ext_port_default = service.get("external_port_default", service.get("port", 0))
                if ext_port_env:
                    val = os.environ.get(ext_port_env) or _read_env_from_file(ext_port_env)
                    external_port = int(val) if val else int(ext_port_default)
                else:
                    external_port = int(ext_port_default)

                services[service_id] = {
                    "host": host,
                    "port": int(service.get("port", 0)),
                    "external_port": external_port,
                    "health": service.get("health", "/health"),
                    "name": service.get("name", service_id),
                    "ui_path": service.get("ui_path", "/"),
                    "external_link": bool(service.get("external_link", True)),
                    "container_name": service.get("container_name", f"dream-{service_id}"),
                    "depends_on": service.get("depends_on", []),
                    "category": service.get("category", "optional"),
                    "host_network": bool(service.get("host_network", False)),
                    "setup_hook": service.get("setup_hook", ""),
                    "hooks": service.get("hooks", {}),
                    "gpu_backends": service.get("gpu_backends", []),
                    **({"type": service["type"]} if "type" in service else {}),
                    **({"health_port": int(service["health_port"])} if "health_port" in service else {}),
                }

            manifest_features = manifest.get("features", [])
            if isinstance(manifest_features, list):
                for feature in manifest_features:
                    if not isinstance(feature, dict):
                        continue
                    supported = feature.get("gpu_backends", ["amd", "nvidia", "apple"])
                    if gpu_backend != "apple" and gpu_backend not in supported and "all" not in supported:
                        continue
                    if feature.get("id") and feature.get("name"):
                        missing = [f for f in ("description", "icon", "category", "setup_time", "priority") if f not in feature]
                        if missing:
                            logger.warning("Feature '%s' in %s missing optional fields: %s", feature["id"], path, ", ".join(missing))
                        features.append(feature)

            loaded += 1
        except (yaml.YAMLError, json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as e:
            logger.warning("Failed loading manifest %s: %s", path, e)
            errors.append({"file": str(path), "error": str(e)})

    logger.info("Loaded %d extension manifests (%d services, %d features)", loaded, len(services), len(features))
    return services, features, errors


# --- Service Registry ---

MANIFEST_SERVICES, MANIFEST_FEATURES, MANIFEST_ERRORS = load_extension_manifests(EXTENSIONS_DIR, GPU_BACKEND)
SERVICES = MANIFEST_SERVICES
if not SERVICES:
    logger.error("No services loaded from manifests in %s — dashboard will have no services", EXTENSIONS_DIR)

# Lemonade serves at /api/v1 instead of llama.cpp's /v1. Override the
# health path so the dashboard poll loop hits the correct endpoint.
LLM_BACKEND = os.environ.get("LLM_BACKEND", "")
if LLM_BACKEND == "lemonade" and "llama-server" in SERVICES:
    SERVICES["llama-server"]["health"] = "/api/v1/health"
    logger.info("Lemonade backend detected — overriding llama-server health to /api/v1/health")

# --- Features ---

FEATURES = MANIFEST_FEATURES
if not FEATURES:
    logger.warning("No features loaded from manifests — check %s", EXTENSIONS_DIR)

# --- Workflow Config ---


def resolve_workflow_dir() -> Path:
    """Resolve canonical workflow directory with legacy fallback."""
    env_dir = os.environ.get("WORKFLOW_DIR")
    if env_dir:
        return Path(env_dir)
    canonical = Path(INSTALL_DIR) / "config" / "n8n"
    if canonical.exists():
        return canonical
    return Path(INSTALL_DIR) / "workflows"


WORKFLOW_DIR = resolve_workflow_dir()
WORKFLOW_CATALOG_FILE = WORKFLOW_DIR / "catalog.json"
DEFAULT_WORKFLOW_CATALOG = {"workflows": [], "categories": {}}

def _default_n8n_url() -> str:
    cfg = SERVICES.get("n8n", {})
    host = cfg.get("host", "n8n")
    port = cfg.get("port", 5678)
    return f"http://{host}:{port}"

N8N_URL = os.environ.get("N8N_URL", _default_n8n_url())
N8N_API_KEY = os.environ.get("N8N_API_KEY", "")

# --- Setup / Personas ---

SETUP_CONFIG_DIR = Path(DATA_DIR) / "config"

PERSONAS = {
    "general": {
        "name": "General Helper",
        "system_prompt": "You are a friendly and helpful AI assistant. You're knowledgeable, patient, and aim to be genuinely useful. Keep responses clear and conversational.",
        "icon": "\U0001f4ac"
    },
    "coding": {
        "name": "Coding Buddy",
        "system_prompt": "You are a skilled programmer and technical assistant. You write clean, well-documented code and explain technical concepts clearly. You're precise, thorough, and love solving problems.",
        "icon": "\U0001f4bb"
    },
    "creative": {
        "name": "Creative Writer",
        "system_prompt": "You are an imaginative creative writer and storyteller. You craft vivid descriptions, engaging narratives, and think outside the box. You're expressive and enjoy wordplay.",
        "icon": "\U0001f3a8"
    }
}

# --- Sidebar Icons ---

SIDEBAR_ICONS = {
    "open-webui": "MessageSquare",
    "n8n": "Network",
    "openclaw": "Bot",
    "hermes": "Bot",
    "hermes-proxy": "Shield",
    "opencode": "Code",
    "perplexica": "Search",
    "comfyui": "Image",
    "token-spy": "Terminal",
    "langfuse": "BarChart2",
}

# --- Extensions Portal ---

CATALOG_PATH = Path(os.environ.get(
    "DREAM_EXTENSIONS_CATALOG",
    str(Path(INSTALL_DIR) / "config" / "extensions-catalog.json")
))

EXTENSIONS_LIBRARY_DIR = Path(os.environ.get(
    "DREAM_EXTENSIONS_LIBRARY_DIR",
    str(Path(DATA_DIR) / "extensions-library")
))

USER_EXTENSIONS_DIR = Path(os.environ.get(
    "DREAM_USER_EXTENSIONS_DIR",
    str(Path(DATA_DIR) / "user-extensions")
))

def _load_core_service_ids() -> frozenset:
    core_ids_path = Path(INSTALL_DIR) / "config" / "core-service-ids.json"
    if core_ids_path.exists():
        try:
            return frozenset(json.loads(core_ids_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    # Fallback to hardcoded list
    return frozenset({
        "dashboard-api", "dashboard", "llama-server", "open-webui",
        "litellm", "langfuse", "hermes", "hermes-proxy", "n8n", "openclaw", "opencode",
        "perplexica", "searxng", "qdrant", "tts", "whisper",
        "embeddings", "token-spy", "comfyui", "ape", "privacy-shield",
    })


CORE_SERVICE_IDS = _load_core_service_ids()

# Always-on services defined in docker-compose.base.yml — never manageable via API.
# Distinct from CORE_SERVICE_IDS (the full built-in service allowlist).
ALWAYS_ON_SERVICES: frozenset = frozenset({"llama-server", "open-webui", "dashboard", "dashboard-api"})


def load_extension_catalog() -> list[dict]:
    """Load the static extensions catalog JSON. Returns empty list on failure."""
    if not CATALOG_PATH.exists():
        logger.info("Extensions catalog not found at %s", CATALOG_PATH)
        return []
    try:
        data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        return data.get("extensions", [])
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load extensions catalog: %s", e)
        return []


EXTENSION_CATALOG = load_extension_catalog()

# --- Host Agent ---

def _running_inside_container() -> bool:
    """Best-effort check for Docker/Podman/containerd runtime context."""
    if Path("/.dockerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8").lower()
    except OSError:
        return False
    return any(marker in cgroup for marker in ("docker", "containerd", "kubepods", "podman"))


def _detect_container_default_gateway(route_path: str = "/proc/net/route") -> str:
    """Return this container's default-gateway IP, or empty on failure.

    Reads /proc/net/route directly so the container image doesn't need
    iproute2 installed. The default route line has destination 00000000 and
    a little-endian-hex gateway in the 3rd field.

    Why this matters: dashboard-api runs on `dream-network` (a custom bridge,
    e.g. 172.18.0.0/16). On Linux, the dream-host-agent binds to that network's
    host-side gateway when it can, so targeting this container's default gateway
    is routable without depending on `host.docker.internal:host-gateway`, which
    Docker often resolves to the default bridge gateway (172.17.0.1). That
    default bridge address is unreachable from custom networks under Docker's
    default DOCKER-ISOLATION-STAGE-2 iptables rules.
    """
    try:
        with open(route_path, "r", encoding="utf-8") as f:
            for line in f.readlines()[1:]:
                fields = line.strip().split()
                # destination == 0.0.0.0 AND flags has RTF_GATEWAY (0x2)
                if len(fields) < 4 or fields[1] != "00000000":
                    continue
                gw_hex = fields[2]
                try:
                    flags = int(fields[3], 16)
                    gateway_raw = int(gw_hex, 16)
                except ValueError:
                    continue
                if not (flags & 0x2) or gateway_raw == 0 or len(gw_hex) != 8:
                    continue
                # Little-endian: 0100A8C0 -> 192.168.0.1
                return ".".join(
                    str(int(gw_hex[i:i + 2], 16)) for i in (6, 4, 2, 0)
                )
    except OSError:
        pass
    return ""


def _resolve_agent_host() -> str:
    """Pick the host name/IP to use for the dream-host-agent.

    Priority:
      1. DREAM_AGENT_HOST env (explicit operator override)
      2. The container's own default-gateway IP (works regardless of which
         Docker network the container is on)
      3. host.docker.internal (legacy fallback — broken on custom networks
         under default Docker iptables, but kept so explicit operator setups
         relying on it don't silently change)
    """
    explicit = os.environ.get("DREAM_AGENT_HOST", "").strip()
    if explicit:
        return explicit
    if _running_inside_container():
        gw = _detect_container_default_gateway()
        if gw:
            logger.info("Resolved DREAM_AGENT_HOST=%s via /proc/net/route", gw)
            return gw
    logger.warning(
        "Could not detect container default gateway; falling back to "
        "host.docker.internal. If host-agent calls time out, set "
        "DREAM_AGENT_HOST=<host-ip> in dashboard-api's environment."
    )
    return "host.docker.internal"


AGENT_HOST = _resolve_agent_host()
AGENT_PORT = int(os.environ.get("DREAM_AGENT_PORT", "7710"))
AGENT_URL = f"http://{AGENT_HOST}:{AGENT_PORT}"
DASHBOARD_API_KEY = os.environ.get("DASHBOARD_API_KEY", "")
# Prefer dedicated DREAM_AGENT_KEY; fall back to DASHBOARD_API_KEY for
# existing installs that haven't generated a separate key yet.
DREAM_AGENT_KEY = os.environ.get("DREAM_AGENT_KEY", "") or DASHBOARD_API_KEY


# --- Templates ---

TEMPLATES_DIR = Path(
    os.environ.get(
        "DREAM_TEMPLATES_DIR",
        str(Path(INSTALL_DIR) / "templates")
    )
)

_TEMPLATE_SCHEMA = None
try:
    import jsonschema as _jsonschema_mod
    _schema_path = Path(__file__).parent.parent.parent / "schema" / "service-template.v1.json"
    if _schema_path.exists():
        _TEMPLATE_SCHEMA = json.loads(_schema_path.read_text(encoding="utf-8"))
except ImportError:
    _jsonschema_mod = None


def load_templates() -> list[dict]:
    """Load service templates from YAML files. Returns empty list on failure."""
    if not TEMPLATES_DIR.exists():
        logger.info("Templates directory not found at %s", TEMPLATES_DIR)
        return []

    templates = []
    for path in sorted(TEMPLATES_DIR.iterdir()):
        if path.suffix.lower() not in (".yaml", ".yml"):
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                logger.warning("Skipping template %s: root is not a mapping", path.name)
                continue
            if data.get("schema_version") != "dream.templates.v1":
                logger.warning("Skipping template %s: unsupported schema_version", path.name)
                continue
            # Validate against JSON Schema if available
            if _TEMPLATE_SCHEMA is not None and _jsonschema_mod is not None:
                try:
                    _jsonschema_mod.validate(data, _TEMPLATE_SCHEMA)
                except _jsonschema_mod.ValidationError as ve:
                    logger.warning("Template validation failed for %s: %s", path.name, ve.message)
                    continue
            template = data.get("template")
            if not isinstance(template, dict) or not template.get("id") or not template.get("services"):
                logger.warning("Skipping template %s: missing required fields", path.name)
                continue
            templates.append(template)
        except (yaml.YAMLError, OSError, ValueError) as e:
            logger.warning("Failed loading template %s: %s", path.name, e)

    logger.info("Loaded %d service templates", len(templates))
    return templates


TEMPLATES = load_templates()
