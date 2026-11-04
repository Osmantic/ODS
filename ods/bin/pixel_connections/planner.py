"""The gateway side of a connection, exactly as Pixel's renderer would write it.

A connection changes only the Source Broker plugin's entries, which of its tools
Portal may call, the owner's time zone, and five limb flags in ``gateway.env``.
The same transaction loads the reviewed plugins the owner turned on (plugins.py).
Pixel refuses to re-apply the release that is already active, so ODS applies this
transformation to the live files instead. It mirrors ``scripts/render-config.mjs``
followed by the ODS runtime overlay, which sorts both tool allow lists; a test
renders both states with Pixel's own renderer and requires the same result.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any

from . import plugins as portal_plugins
from . import native_apps

PLUGIN_ID = "pixel-source-broker"
# render-config.mjs allSourceTools, in its order: newly inactive tools are appended
# to the agent's deny list in this order.
GMAIL_TOOLS = (
    "pixel_gmail_inbox", "pixel_gmail_sent", "pixel_gmail_search", "pixel_gmail_read", "pixel_gmail_thread",
    "pixel_gmail_count",
)
GMAIL_DRAFT_TOOLS = ("pixel_gmail_draft_propose",)
CALENDAR_READ_TOOLS = ("pixel_calendar_list", "pixel_calendar_get", "pixel_calendar_count")
CALENDAR_WRITE_TOOLS = (
    "pixel_calendar_propose_create", "pixel_calendar_propose_update", "pixel_calendar_propose_delete",
)
SOCIAL_TOOLS = ("pixel_social_feed", "pixel_social_search")
WEB_TOOLS = ("pixel_web_browse",)
SOURCE_TOOLS = (
    "pixel_limb_status", *GMAIL_TOOLS, *GMAIL_DRAFT_TOOLS, *CALENDAR_READ_TOOLS, *CALENDAR_WRITE_TOOLS,
    *SOCIAL_TOOLS, *WEB_TOOLS,
)
LIMB_KEYS = {
    "email": "PIXEL_LIMB_EMAIL_ENABLED",
    "emailDrafts": "PIXEL_LIMB_EMAIL_DRAFTS_ENABLED",
    "calendar": "PIXEL_LIMB_CALENDAR_ENABLED",
    "calendarWrite": "PIXEL_LIMB_CALENDAR_WRITE_ENABLED",
    "calendarDirect": "PIXEL_CALENDAR_DIRECT_ENABLED",
}
# Pixel 4.3.35 added Gmail drafts. A gateway environment or a journaled target
# from before it has no drafts flag, which means drafts are off.
SINCE_DRAFTS = frozenset({"emailDrafts"})
# A target journaled before the plugin catalog loads no plugin.
SINCE_PLUGINS = frozenset({"plugins"})
TIME_ZONE = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-/]{0,63}$")
# Pixel's configure quotes every value, escaping a quote inside it as '\''.
ENV_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)='((?:[^']|'\\'')*)'$")


class PlanError(ValueError):
    """The live configuration is outside the shape Pixel renders."""


DEPLOYMENT_KEYS = frozenset({'schemaVersion', 'surface', 'ownerUid', 'ownerGid', 'home', 'configPath',
    'stateDir', 'gatewayEnvPath', 'answersPath', 'runtimeRoot', 'sourceRoot', 'nodePath', 'entrypointPath',
    'launcherPath', 'hostPythonPath', 'pluginPaths', 'runtimeCustodySha256', 'bundleSha256',
    'gatewayPlist', 'gatewayPlistSha256'})
DEPLOYMENT_PLUGINS = ('pixel-source-broker', 'pixel-operations-broker', 'pixel-frontier-broker')
NATIVE_RUNTIME_ROOT = Path('/usr/local/libexec/ods-pixel-runtimes')
_HEX = re.compile(r'[a-f0-9]{64}\Z')


@dataclass(frozen=True)
class ConnectionDeployment:
    """Internal Root/owner value, never a path-bearing public request or grant."""

    encoded: bytes

    @classmethod
    def parse(cls, value):
        if (type(value) is not dict or set(value) != DEPLOYMENT_KEYS or type(value['schemaVersion']) is not int
                or value['schemaVersion'] != 1 or value['surface'] != 'darwin'
                or type(value['ownerUid']) is not int or value['ownerUid'] <= 0
                or type(value['ownerGid']) is not int or value['ownerGid'] < 0):
            raise PlanError('connection-deployment-invalid')
        for key in DEPLOYMENT_KEYS - {'schemaVersion', 'surface', 'ownerUid', 'ownerGid', 'pluginPaths'}:
            item = value[key]
            if key.endswith('Sha256'):
                valid = type(item) is str and _HEX.fullmatch(item)
            else:
                valid = (type(item) is str and item.startswith('/') and item != '/'
                         and not any(ord(c) < 32 for c in item)
                         and all(part not in ('', '.', '..') for part in item.split('/')[1:]))
            if not valid:
                raise PlanError('connection-deployment-invalid')
        plugins = value['pluginPaths']
        if (type(plugins) is not dict or set(plugins) != set(DEPLOYMENT_PLUGINS)
                or any(type(path) is not str or not re.fullmatch(r'/[^\x00-\x1f]+/plugins/[0-9]+', path)
                       or any(part in ('', '.', '..') for part in path.split('/')[1:]) for path in plugins.values())
                or len(set(plugins.values())) != len(plugins)):
            raise PlanError('connection-deployment-invalid')
        config_parent = Path(value['configPath']).parent
        if (value['gatewayEnvPath'] != str(config_parent / 'gateway.env')
                or value['answersPath'] != str(config_parent / 'onboarding.json')
                or value['entrypointPath'] != str(Path(value['runtimeRoot']) / 'openclaw.mjs')
                or any(Path(path).parent.parent != Path(value['nodePath']).parent for path in plugins.values())):
            raise PlanError('connection-deployment-invalid')
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
        if len(raw) > 8192:
            raise PlanError('connection-deployment-invalid')
        return cls(raw)

    @property
    def document(self):
        return json.loads(self.encoded)

    @property
    def checksum(self):
        return hashlib.sha256(self.encoded).hexdigest()


def _native_bundle_plugins(runtime, *, _operation=None):
    """Use the protected published inventory, never owner config load.paths."""
    from pixel_macos_custody import protected_bytes, protected_tree_metadata
    root = Path(runtime['node']).parent
    if root.parent != NATIVE_RUNTIME_ROOT or not _HEX.fullmatch(root.name) or Path(runtime['runtimeRoot']) != root / 'runtime':
        raise PlanError('connection-runtime-bundle-unavailable')
    if _operation is not None:
        from pixel_access_bridge import _native_runtime_bundle_cache
        _native_runtime_bundle_cache(_operation, root)
    protected_tree_metadata(root)
    helper = Path(runtime['sourceRoot']) / 'installers/macos/lib/pixel-runtime-bundle.py'
    helper_body = protected_bytes(helper)
    spec = importlib.util.spec_from_file_location('_connection_bundle', helper)
    module = importlib.util.module_from_spec(spec)
    if _operation is None:
        spec.loader.exec_module(module)
    else:
        from pixel_access_bridge import _native_runtime_code
        exec(_native_runtime_code(_operation, helper, helper_body), module.__dict__)
    # Older protected helpers remain cold. Inspect the trusted signature, never
    # catch TypeError and retry a call that may already have done real work.
    import inspect
    parameter = inspect.signature(module.verify).parameters.get('_operation')
    if (_operation is not None and parameter is not None
            and parameter.kind is inspect.Parameter.KEYWORD_ONLY and parameter.default is None):
        manifest, checksum = module.verify(root, expected_digest=root.name, _operation=_operation)
    else:
        manifest, checksum = module.verify(root, expected_digest=root.name)
    paths = {}
    for relative in manifest['plugins']:
        metadata = json.loads(protected_bytes(root / relative / 'openclaw.plugin.json'))
        plugin_id = metadata.get('id') if type(metadata) is dict else None
        if plugin_id in DEPLOYMENT_PLUGINS:
            if plugin_id in paths:
                raise PlanError('connection-runtime-plugin-ambiguous')
            paths[plugin_id] = str(root / relative)
    if set(paths) != set(DEPLOYMENT_PLUGINS):
        raise PlanError('connection-runtime-plugin-unavailable')
    return paths, checksum


def native_deployment(runtime, runtime_checksum, *, uid, gid, environment, plist, plist_sha256, _operation=None):
    """Derive one value from already-qualified runtime and launchd inputs.

    Sidefiles are a future installer publication beside the selected config.
    Their absence refuses; preparation answers and Linux paths are never adopted.
    """
    from .native_mcp import _parents, _read
    plugins, bundle_checksum = _native_bundle_plugins(runtime, _operation=_operation)
    home, config, state_dir = (environment[key] for key in ('HOME', 'OPENCLAW_CONFIG_PATH', 'OPENCLAW_STATE_DIR'))
    config_parent = Path(config).parent
    env_path, answers_path = config_parent / 'gateway.env', config_parent / 'onboarding.json'
    for path in (Path(home), Path(config), Path(state_dir), env_path, answers_path):
        if path.resolve(strict=True) != path:
            raise PlanError('connection-deployment-path-changed')
        _parents(path, uid)
    for path in (Path(home), Path(state_dir)):
        info = path.lstat()
        if not path.is_dir() or info.st_uid != uid or info.st_mode & 0o077:
            raise PlanError('connection-deployment-owner-changed')
    # Metadata and contents are checked again in the owner, before any state-dir
    # preparation. Do not create/chmod/adopt incomplete publications.
    _read(env_path, owners=(uid,), private=True)
    _read(answers_path, owners=(uid,), private=True)
    return ConnectionDeployment.parse({'schemaVersion': 1, 'surface': 'darwin', 'ownerUid': uid, 'ownerGid': gid,
        'home': home, 'configPath': config, 'stateDir': state_dir, 'gatewayEnvPath': str(env_path),
        'answersPath': str(answers_path), 'runtimeRoot': runtime['runtimeRoot'], 'sourceRoot': runtime['sourceRoot'],
        'nodePath': runtime['node'], 'entrypointPath': str(Path(runtime['runtimeRoot']) / 'openclaw.mjs'),
        'launcherPath': runtime['launcher'], 'hostPythonPath': runtime['hostPython'], 'pluginPaths': plugins,
        'runtimeCustodySha256': runtime_checksum, 'bundleSha256': bundle_checksum,
        'gatewayPlist': str(plist), 'gatewayPlistSha256': plist_sha256})


def require_native_deployment(value, *, uid, gid, home, config_path):
    """Owner-side requalification, not trust in a checksum supplied on a pipe."""
    from pixel_access_bridge import gateway_runtime_environment
    from pixel_macos_custody import protected_bytes
    from pixel_provider.runtime_custody import RuntimeCustody
    import plistlib
    deployment = ConnectionDeployment.parse(value)
    doc = deployment.document
    if (doc['ownerUid'], doc['ownerGid'], doc['home'], doc['configPath']) != (uid, gid, home, config_path):
        raise PlanError('connection-deployment-owner-changed')
    runtime, checksum = RuntimeCustody(SimpleNamespace(binary=doc['launcherPath'])).inspect()
    raw = protected_bytes(doc['gatewayPlist'])
    actual = native_deployment(runtime, checksum, uid=uid, gid=gid,
        environment=gateway_runtime_environment(plistlib.loads(raw)), plist=doc['gatewayPlist'],
        plist_sha256=hashlib.sha256(raw).hexdigest())
    if actual != deployment:
        raise PlanError('connection-deployment-changed')
    return deployment


def limbs(capabilities: dict[str, bool] | None) -> dict[str, bool]:
    """Gateway limb flags for a Google connection's capabilities; all off without one."""
    capabilities = capabilities or {}
    email = capabilities.get("gmail.read") is True
    calendar = capabilities.get("calendar.read") is True
    write = calendar and capabilities.get("calendar.write") is True
    return {"email": email, "emailDrafts": email and capabilities.get("gmail.drafts") is True, "calendar": calendar,
            "calendarWrite": write, "calendarDirect": write and capabilities.get("calendar.direct") is True}


def target(value: Any) -> dict[str, Any]:
    """Validate a gateway target, the five limb flags, an optional time zone and the plugins, and return it whole.

    A target journaled before Gmail drafts comes back with drafts off, and one
    from before the plugin catalog with no plugin.
    """
    keys = {*LIMB_KEYS, "userTimezone", "plugins"}
    if not isinstance(value, dict) or set(value) - {'nativeApps'} not in (keys, keys - SINCE_PLUGINS,
                                                         keys - SINCE_PLUGINS - SINCE_DRAFTS):
        raise PlanError("invalid connection target")
    value = {"emailDrafts": False, "plugins": [], **value}
    if 'nativeApps' in value:
        try:
            value['nativeApps'] = native_apps.choices(value['nativeApps'])
        except native_apps.NativeAppError:
            raise PlanError('invalid native app target') from None
    if any(type(value[key]) is not bool for key in LIMB_KEYS):
        raise PlanError("invalid connection target")
    chosen = value["plugins"]
    if (type(chosen) is not list or any(type(item) is not str for item in chosen)
            or chosen != [plugin_id for plugin_id in portal_plugins.IDS if plugin_id in chosen]):
        raise PlanError("invalid connection target")
    if (value["emailDrafts"] and not value["email"] or value["calendarWrite"] and not value["calendar"]
            or value["calendarDirect"] and not value["calendarWrite"]):
        raise PlanError("invalid connection target")
    zone = value["userTimezone"]
    if zone is not None and (type(zone) is not str or not TIME_ZONE.fullmatch(zone) or ".." in zone):
        raise PlanError("invalid connection target")
    return value


def _list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or any(type(item) is not str for item in value):
        raise PlanError(f"{name} is not a list of names")
    return value


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PlanError(f"{name} is not an object")
    return value


def _shape(config: Any) -> tuple[dict, dict, dict, list, list, dict, dict]:
    config = _object(config, "configuration")
    plugins = _object(config.get("plugins"), "plugins")
    _list(plugins.get("allow"), "plugins.allow")
    _object(plugins.get("entries"), "plugins.entries")
    load = _object(plugins.get("load"), "plugins.load")
    _list(load.get("paths"), "plugins.load.paths")
    tools = _object(config.get("tools"), "tools")
    also_allow = _list(tools.get("alsoAllow"), "tools.alsoAllow")
    sandbox = _object(_object(tools.get("sandbox"), "tools.sandbox").get("tools"), "tools.sandbox.tools")
    sandbox_allow = _list(sandbox.get("allow"), "tools.sandbox.tools.allow")
    agents = _object(config.get("agents"), "agents")
    defaults = _object(agents.get("defaults"), "agents.defaults")
    selected = [item for item in _list_of_objects(agents.get("list")) if item.get("id") == "pixel"]
    if len(selected) != 1:
        raise PlanError("the configuration has no single Pixel agent")
    agent_tools = _object(selected[0].get("tools"), "Pixel agent tools")
    _list(agent_tools.get("deny"), "Pixel agent tools.deny")
    if "llama-cpp" not in plugins["allow"]:
        raise PlanError("plugins.allow has no llama-cpp entry to order against")
    return plugins, load, tools, also_allow, sandbox_allow, agent_tools, defaults


def _list_of_objects(value: Any) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise PlanError("agents.list is not a list of agents")
    return value


def active_tools(config: Any, gateway: dict[str, Any]) -> list[str]:
    """The Source Broker tools Pixel renders for ``gateway``, keeping the live social and web limbs."""
    _plugins, _load, _tools, also_allow, _sandbox, _agent, _defaults = _shape(config)
    social = all(name in also_allow for name in SOCIAL_TOOLS)
    web = all(name in also_allow for name in WEB_TOOLS)
    email, calendar = gateway["email"], gateway["calendar"]
    tools = ["pixel_limb_status"] if email or calendar or social or web else []
    if email:
        tools += GMAIL_TOOLS + (GMAIL_DRAFT_TOOLS if gateway["emailDrafts"] else ())
    if calendar:
        tools += CALENDAR_READ_TOOLS + (CALENDAR_WRITE_TOOLS if gateway["calendarWrite"] else ())
    if social:
        tools += SOCIAL_TOOLS
    if web:
        tools += WEB_TOOLS
    return list(tools)


def plan_config(config: Any, gateway: dict[str, Any], *, install_dir: str, plugin_paths=None) -> dict[str, Any]:
    """The live configuration with the Source Broker rendered for ``gateway``."""
    gateway = target(gateway)
    if plugin_paths is None:
        plugin_path = f"{install_dir}/current/plugin"
        later_paths = (f"{install_dir}/current/plugin-ops", f"{install_dir}/current/plugin-frontier")
    else:
        if type(plugin_paths) is not dict or set(plugin_paths) != set(DEPLOYMENT_PLUGINS):
            raise PlanError('connection-deployment-invalid')
        plugin_path = plugin_paths[PLUGIN_ID]
        later_paths = tuple(plugin_paths[name] for name in DEPLOYMENT_PLUGINS[1:])
    planned = copy.deepcopy(config)
    plugins, load, tools, also_allow, sandbox_allow, agent_tools, defaults = _shape(planned)
    previous = [name for name in SOURCE_TOOLS if name in also_allow]
    wanted = active_tools(planned, gateway)

    others = [name for name in also_allow if name not in SOURCE_TOOLS]
    tools["alsoAllow"] = sorted(set(others + wanted))
    tools["sandbox"]["tools"]["allow"] = sorted(set(
        [name for name in sandbox_allow if name not in SOURCE_TOOLS] + wanted))
    # The renderer keeps the live deny order without the active tools, then appends
    # every newly inactive tool in render order.
    deny = [name for name in agent_tools["deny"] if name not in wanted]
    deny += [name for name in SOURCE_TOOLS if name in previous and name not in wanted and name not in deny]
    agent_tools["deny"] = deny

    allow = [name for name in plugins["allow"] if name != PLUGIN_ID]
    entries = plugins["entries"]
    entries.pop(PLUGIN_ID, None)
    paths = [path for path in load["paths"] if path != plugin_path]
    if wanted:
        allow.insert(allow.index("llama-cpp") + 1, PLUGIN_ID)
        entries[PLUGIN_ID] = {"enabled": True}
        later = [index for index, path in enumerate(paths) if path in later_paths]
        paths.insert(later[0] if later else len(paths), plugin_path)
    plugins["allow"] = allow
    load["paths"] = paths

    if gateway["userTimezone"]:
        defaults["userTimezone"] = gateway["userTimezone"]
    else:
        defaults.pop("userTimezone", None)
    portal_plugins.clear(planned)
    portal_plugins.load_portal_plugins(planned, portal_plugins.answers(gateway["plugins"]) or [])
    if 'nativeApps' in gateway:
        try:
            native_apps.load_native_apps(planned, native_apps.answers(gateway['nativeApps']))
        except ValueError:
            raise PlanError('native app registry collision or invalid shape') from None
    return planned


def gateway_limbs(text: str) -> dict[str, bool]:
    """The five limb flags a gateway environment file sets."""
    values = _env_values(text)
    return {name: values.get(key) == "1" for name, key in LIMB_KEYS.items()}


def _env_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = ENV_LINE.fullmatch(line)
        if match is None:
            raise PlanError("gateway environment has a line Pixel does not write")
        if match.group(1) in values:
            raise PlanError("gateway environment repeats a key")
        values[match.group(1)] = match.group(2)
    for name, key in LIMB_KEYS.items():
        if values.get(key) not in ("0", "1") and not (name in SINCE_DRAFTS and key not in values):
            raise PlanError(f"gateway environment has no 0 or 1 value for {key}")
    return values


def plan_gateway_env(text: str, gateway: dict[str, Any]) -> str:
    """The gateway environment with only the five limb flags set for ``gateway``."""
    gateway = target(gateway)
    if not text.endswith("\n"):
        raise PlanError("gateway environment does not end with a newline")
    values = _env_values(text)
    if gateway["emailDrafts"] and LIMB_KEYS["emailDrafts"] not in values:
        raise PlanError("this Pixel has no Gmail drafts; update ODS to turn them on")
    wanted = {key: "1" if gateway[name] else "0" for name, key in LIMB_KEYS.items()}
    lines = []
    for line in text.splitlines():
        key = ENV_LINE.fullmatch(line).group(1)
        lines.append(f"{key}='{wanted[key]}'" if key in wanted else line)
    return "\n".join(lines) + "\n"
