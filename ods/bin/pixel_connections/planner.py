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
import re
from typing import Any

from . import plugins as portal_plugins

PLUGIN_ID = "pixel-source-broker"
# render-config.mjs allSourceTools, in its order: newly inactive tools are appended
# to the agent's deny list in this order.
SOURCE_TOOLS = (
    "pixel_limb_status",
    "pixel_gmail_inbox", "pixel_gmail_sent", "pixel_gmail_search", "pixel_gmail_read", "pixel_gmail_thread",
    "pixel_gmail_draft_propose",
    "pixel_calendar_list", "pixel_calendar_get", "pixel_calendar_propose_create",
    "pixel_calendar_propose_update", "pixel_calendar_propose_delete",
    "pixel_social_feed", "pixel_social_search", "pixel_web_browse",
)
GMAIL_TOOLS = SOURCE_TOOLS[1:6]
GMAIL_DRAFT_TOOLS = SOURCE_TOOLS[6:7]
CALENDAR_READ_TOOLS = SOURCE_TOOLS[7:9]
CALENDAR_WRITE_TOOLS = SOURCE_TOOLS[9:12]
SOCIAL_TOOLS = SOURCE_TOOLS[12:14]
WEB_TOOLS = SOURCE_TOOLS[14:]
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
    if not isinstance(value, dict) or set(value) not in (keys, keys - SINCE_PLUGINS,
                                                         keys - SINCE_PLUGINS - SINCE_DRAFTS):
        raise PlanError("invalid connection target")
    value = {"emailDrafts": False, "plugins": [], **value}
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


def plan_config(config: Any, gateway: dict[str, Any], *, install_dir: str) -> dict[str, Any]:
    """The live configuration with the Source Broker rendered for ``gateway``."""
    gateway = target(gateway)
    plugin_path = f"{install_dir}/current/plugin"
    later_paths = (f"{install_dir}/current/plugin-ops", f"{install_dir}/current/plugin-frontier")
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
