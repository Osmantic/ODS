"""How an owner opens and uses an extension, derived from its manifest.

Everything here is a declaration read from the extension's own definition:
whether it has a web page, where ODS publishes it, where its documentation
lives and which settings it declares. Only the presence of a setting is ever
reported, never its value, and nothing here proves the service works.
"""
import re
from urllib.parse import urlsplit

KINDS = ("web", "api", "none")
_ENV_KEY = re.compile(r"[A-Z][A-Z0-9_]{1,127}")
_HOST = re.compile(r"[a-z0-9][a-z0-9_.-]{0,62}")
_UI_PATH = re.compile(r"/[A-Za-z0-9/_.~%-]{0,255}")
_PORT = re.compile(r"[0-9]{1,5}")
# Setting names that only other processes read (databases, signing keys).
_INTERNAL_MARKERS = ("_DB_", "DATABASE", "POSTGRES", "MONGO", "REDIS", "_RPC_", "JWT", "SECRET",
                     "ENCRYPTION", "SALT", "PEPPER", "KEY_BASE", "SEED", "_SMTP_", "_IV")
_SIGN_IN_MARKERS = ("PASSWORD", "UI_AUTH", "TOKEN", "EMAIL", "_USER", "USERNAME", "SETUP_CODE")
_API_KEY_MARKERS = ("API_KEY", "MASTER_KEY")


def _port(value, *, allow_zero=False):
    if type(value) is int:
        port = value
    elif isinstance(value, str) and _PORT.fullmatch(value.strip()):
        port = int(value.strip())
    else:
        return None
    return port if (0 if allow_zero else 1) <= port <= 65535 else None


def launch_types(features) -> set:
    """The ``launch.type`` values the manifest's features declare."""
    if not isinstance(features, list):
        return set()
    return {feature["launch"].get("type") for feature in features
            if isinstance(feature, dict) and isinstance(feature.get("launch"), dict)}


def usage_kind(service: dict, features) -> str:
    """``web`` for a page to open, ``api`` for a network service, ``none`` otherwise.

    ``external_link: false`` and features that only launch ``none`` both mean
    the service has no page for a person. Without either declaration the
    manifest schema's default applies: the service is a dashboard quick link.
    """
    if not _port(service.get("port")):
        return "none"
    if service.get("external_link") is False:
        return "api"
    kinds = launch_types(features)
    if "service" in kinds or "internal" in kinds:
        return "web"
    if kinds and kinds <= {"none"}:
        return "api"
    return "web"


def ui_path(service: dict) -> str:
    """The declared page path, or ``/`` when it is absent or not a plain path."""
    value = service.get("ui_path")
    if (isinstance(value, str) and _UI_PATH.fullmatch(value)
            and not value.startswith("//") and ".." not in value.split("/")):
        return value
    return "/"


def https_url(value) -> str | None:
    """A plain https URL without credentials, or None."""
    if not isinstance(value, str) or not value or len(value) > 512:
        return None
    if any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value:
        return None
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        return None
    if (parsed.scheme != "https" or not parsed.hostname or port == 0
            or parsed.username is not None or parsed.password is not None):
        return None
    return value


def docs_url(service: dict, provenance) -> str | None:
    """The manifest's ``docs_url``, else the upstream repository it was built from."""
    declared = https_url(service.get("docs_url"))
    if declared:
        return declared
    if isinstance(provenance, dict):
        return https_url(provenance.get("repository"))
    return None


def host_port(service_id: str, service: dict, read_env) -> int | None:
    """The published host port: the extension's own port setting, else the default.

    Only a setting named for this extension (``<ID>_...``) is read, and only
    as a port number. A service that publishes no host port (default 0) has
    none, whatever the setting says.
    """
    default = _port(service.get("external_port_default", service.get("port")), allow_zero=True)
    if not default:
        return None
    key = service.get("external_port_env")
    prefix = re.sub(r"[^A-Za-z0-9]+", "_", service_id).upper() + "_"
    if isinstance(key, str) and _ENV_KEY.fullmatch(key) and key.startswith(prefix):
        configured = _port(read_env(key))
        if configured:
            return configured
    return default


def internal_url(service_id: str, service: dict) -> str | None:
    """Where other ODS services on ``ods-network`` reach this one."""
    port = _port(service.get("port"))
    if not port:
        return None
    host = service.get("default_host")
    host = host if isinstance(host, str) and _HOST.fullmatch(host) else service_id
    return f"http://{host}:{port}"


def setting_role(key: str) -> str:
    """``internal``, ``api_key``, ``sign_in`` or ``setting``, from the setting's name.

    A name is only a hint; the manifest's description says what it is for.
    """
    name = key.upper()
    if any(marker in name for marker in _INTERNAL_MARKERS):
        return "internal"
    if any(marker in name for marker in _API_KEY_MARKERS):
        return "api_key"
    if any(marker in name for marker in _SIGN_IN_MARKERS):
        return "sign_in"
    return "setting"


def settings(fields: list) -> list:
    """Declared settings with presence only, from ``configuration_fields``."""
    return [{"key": field["key"], "description": field["description"], "secret": field["secret"],
             "required": field["required"], "configured": field["configured"],
             "role": setting_role(field["key"])} for field in fields]


def guide(service_id: str, service: dict, features, provenance, fields: list, read_env) -> dict:
    """Owner-facing facts for opening and using one extension."""
    return {
        "schemaVersion": 1,
        "kind": usage_kind(service, features),
        "uiPath": ui_path(service),
        "hostPort": host_port(service_id, service, read_env),
        "internalUrl": internal_url(service_id, service),
        "docsUrl": docs_url(service, provenance),
        "settings": settings(fields),
    }
