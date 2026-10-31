"""Reviewed OpenClaw plugins the owner can turn on for Portal, as data.

Each entry is a plugin that ships inside the OpenClaw package Pixel installs
(vendor/pixel/RELEASE-MANIFEST.json, openclawPackage), reviewed at exactly that
package: its code, shared chunks included, comes from those pinned bytes, and
a test fails when Pixel moves to another OpenClaw until the entry is reviewed
again. Nothing is downloaded or installed at runtime. Each entry carries the
configuration it was reviewed with; the owner turns a plugin on or off, never
changes that configuration.

Pixel's renderer knows none of these plugins, so ODS adds the chosen ones: the
runtime overlay (installers/lib/pixel-runtime-budget.py) on every render, and
the planner on the live configuration. Both run load_portal_plugins.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Iterable

# The OpenClaw package these entries were reviewed in.
OPENCLAW = "2026.6.33"
OPENCLAW_SHA256 = "96880d5a9fe3ef6b3d2ec0ac016e48f99fff7b1af4e22b7c814db6357c163952"
PLUGIN_ID = re.compile(r"^[a-z][a-z0-9-]{1,62}$")

MEMORY_WIKI: dict[str, Any] = {
    "id": "memory-wiki",
    "label": "Knowledge wiki",
    "summary": ("Portal keeps a wiki of what it learns about your people, projects and decisions, notes where "
                "each fact came from, and checks the wiki before it answers."),
    "details": [
        "The pages stay on this machine, in ~/.openclaw/wiki/portal, as Markdown you can open in Obsidian.",
        "Portal can't use the wiki to reach the internet or run programs.",
        "Turning it off keeps the pages; Portal stops using them.",
    ],
    "tools": ["wiki_apply", "wiki_get", "wiki_lint", "wiki_search", "wiki_status"],
    # Reviewed: docs/pixel/plugins/memory-wiki.md. The vault sits beside Portal's
    # workspace, never inside it: the plugin runs on the host and follows links a
    # sandboxed Portal could otherwise plant in its own workspace.
    "config": {
        "vaultMode": "isolated",
        "vault": {"path": "~/.openclaw/wiki/portal", "renderMode": "obsidian"},
        "obsidian": {"enabled": False, "useOfficialCli": False, "openAfterWrites": False},
        "bridge": {"enabled": False},
        "unsafeLocal": {"allowPrivateMemoryCoreAccess": False, "paths": []},
        "ingest": {"autoCompile": True, "maxConcurrentJobs": 1, "allowUrlIngest": False},
        "search": {"backend": "local", "corpus": "wiki"},
        "context": {"includeCompiledDigestPrompt": False},
        "render": {"preserveHumanBlocks": True, "createBacklinks": True, "createDashboards": True},
    },
}

PLUGINS: tuple[dict[str, Any], ...] = (MEMORY_WIKI,)
IDS = tuple(plugin["id"] for plugin in PLUGINS)


def public_catalog() -> list[dict[str, Any]]:
    """What Settings shows for each plugin; never its configuration."""
    return [{key: copy.deepcopy(plugin[key]) for key in ("id", "label", "summary", "details", "tools")}
            for plugin in PLUGINS]


def answers(enabled: Iterable[str]) -> list[dict[str, Any]] | None:
    """The onboarding entries that load these plugins, in catalog order; None loads none."""
    chosen = set(enabled)
    entries = [{"id": plugin["id"], "tools": list(plugin["tools"]), "config": copy.deepcopy(plugin["config"])}
               for plugin in PLUGINS if plugin["id"] in chosen]
    return entries or None


def valid_answers(value: Any) -> bool:
    """Exactly what answers() writes for some catalog plugins."""
    if value is None:
        return True
    if type(value) is not list or not value or any(type(item) is not dict for item in value):
        return False
    chosen = [item.get("id") for item in value]
    return len(set(chosen)) == len(chosen) and value == answers(item for item in chosen if item in IDS)


def enabled_in(config: dict[str, Any]) -> list[str]:
    """The catalog plugins a configuration loads, in catalog order."""
    plugins = config.get("plugins") if isinstance(config, dict) else None
    allow = plugins.get("allow") if isinstance(plugins, dict) else None
    entries = plugins.get("entries") if isinstance(plugins, dict) else None
    if not isinstance(allow, list) or not isinstance(entries, dict):
        return []
    return [plugin_id for plugin_id in IDS
            if plugin_id in allow and isinstance(entries.get(plugin_id), dict)
            and entries[plugin_id].get("enabled") is True]


def clear(config: dict[str, Any]) -> None:
    """Unload every catalog plugin and withdraw its tools; Pixel's own entries stay as they are."""
    names = {name for plugin in PLUGINS for name in plugin["tools"]}
    plugins, tools = config["plugins"], config["tools"]
    plugins["allow"] = [plugin_id for plugin_id in plugins["allow"] if plugin_id not in IDS]
    for plugin_id in IDS:
        plugins["entries"].pop(plugin_id, None)
    tools["alsoAllow"] = [name for name in tools["alsoAllow"] if name not in names]
    tools["sandbox"]["tools"]["allow"] = [name for name in tools["sandbox"]["tools"]["allow"] if name not in names]


def load_portal_plugins(config, chosen):
    """Load each chosen plugin with its reviewed configuration, and let Portal call its tools.

    installers/lib/pixel-runtime-budget.py carries an identical copy (it runs
    without ODS's modules); tests/test_pixel_plugins.py keeps them identical.
    """
    plugins = config["plugins"]
    tools = config["tools"]
    agent = next(item for item in config["agents"]["list"] if item.get("id") == "pixel")
    names = [name for item in chosen for name in item["tools"]]
    for item in chosen:
        if item["id"] not in plugins["allow"]:
            plugins["allow"].append(item["id"])
        plugins["entries"][item["id"]] = {"enabled": True, "config": copy.deepcopy(item["config"])}
    tools["alsoAllow"] = sorted(set(tools["alsoAllow"]) | set(names))
    tools["sandbox"]["tools"]["allow"] = sorted(set(tools["sandbox"]["tools"]["allow"]) | set(names))
    agent["tools"]["deny"] = [name for name in agent["tools"]["deny"] if name not in names]
