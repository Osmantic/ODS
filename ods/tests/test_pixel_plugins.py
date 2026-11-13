"""The reviewed OpenClaw plugin catalog: pinned, locked down, and rendered the same way everywhere."""
import ast
import copy
import inspect
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
from pixel_connections import planner, plugins  # noqa: E402

OVERLAY = ROOT / "installers" / "lib" / "pixel-runtime-budget.py"


def overlay_function(name):
    source = OVERLAY.read_text(encoding="utf-8")
    node = next(item for item in ast.parse(source).body if isinstance(item, ast.FunctionDef) and item.name == name)
    return ast.get_source_segment(source, node)


def live_config():
    """A rendered configuration, trimmed to what loading a plugin touches."""
    return {"plugins": {"allow": ["pixel-ods", "llama-cpp"], "entries": {"pixel-ods": {"enabled": True}},
                        "load": {"paths": []}},
            "tools": {"alsoAllow": ["exec", "read"], "sandbox": {"tools": {"allow": ["exec", "read"]}}},
            "agents": {"list": [{"id": "pixel", "tools": {"deny": ["discord", "message", "wiki_search"]}}]}}


class CatalogTests(unittest.TestCase):
    def test_every_entry_was_reviewed_in_the_openclaw_package_pixel_installs(self):
        manifest = json.loads((ROOT / "vendor" / "pixel" / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
        # A Pixel that moves OpenClaw must bring a new review of every entry with it.
        self.assertEqual(manifest["openclaw"], plugins.OPENCLAW)
        self.assertEqual(manifest["openclawPackage"]["sha256"], plugins.OPENCLAW_SHA256)
        for plugin in plugins.PLUGINS:
            with self.subTest(plugin=plugin["id"]):
                review = ROOT / "docs" / "pixel" / "plugins" / f"{plugin['id']}.md"
                self.assertIn(plugins.OPENCLAW_SHA256, review.read_text(encoding="utf-8"))

    def test_entries_are_distinct_and_never_take_a_pixel_or_ods_name(self):
        self.assertEqual(len(set(plugins.IDS)), len(plugins.IDS))
        pixel_tools = set(planner.SOURCE_TOOLS) | {"discord", "message", "exec", "read", "write", "web_fetch",
                                                   "web_search", "memory_search", "memory_get"}
        for plugin in plugins.PLUGINS:
            with self.subTest(plugin=plugin["id"]):
                self.assertRegex(plugin["id"], plugins.PLUGIN_ID)
                self.assertEqual(plugin["tools"], sorted(set(plugin["tools"])))
                self.assertFalse(set(plugin["tools"]) & pixel_tools)
                self.assertFalse(any(name.startswith("pixel_") for name in plugin["tools"]))
                self.assertNotIn(plugin["id"], {"pixel-ods", "llama-cpp", "searxng", "discord", planner.PLUGIN_ID})

    def test_the_wiki_stays_isolated_outside_portals_workspace(self):
        config = plugins.MEMORY_WIKI["config"]
        # The plugin runs on the host and follows links Portal could plant in its own workspace.
        self.assertTrue(config["vault"]["path"].startswith("~/.openclaw/wiki/"))
        self.assertNotIn("workspace", config["vault"]["path"])
        self.assertEqual(config["vaultMode"], "isolated")
        self.assertEqual(config["bridge"], {"enabled": False})
        self.assertEqual(config["unsafeLocal"], {"allowPrivateMemoryCoreAccess": False, "paths": []})
        self.assertFalse(config["obsidian"]["enabled"] or config["obsidian"]["useOfficialCli"])
        self.assertFalse(config["ingest"]["allowUrlIngest"])
        self.assertEqual(config["search"], {"backend": "local", "corpus": "wiki"})
        self.assertFalse(config["context"]["includeCompiledDigestPrompt"])

    def test_settings_sees_what_each_plugin_does_never_its_configuration(self):
        for entry in plugins.public_catalog():
            self.assertEqual(set(entry), {"id", "label", "summary", "details", "tools"})
        self.assertNotIn("vaultMode", json.dumps(plugins.public_catalog()))


class RenderingTests(unittest.TestCase):
    def test_the_overlay_and_the_planner_load_plugins_with_the_same_code(self):
        self.assertEqual(overlay_function("load_portal_plugins"), inspect.getsource(plugins.load_portal_plugins).rstrip())

    def test_onboarding_entries_are_exactly_the_reviewed_ones(self):
        entries = plugins.answers(["memory-wiki"])
        self.assertEqual(entries, [{"id": "memory-wiki", "tools": plugins.MEMORY_WIKI["tools"],
                                    "config": plugins.MEMORY_WIKI["config"]}])
        self.assertIsNone(plugins.answers([]))
        self.assertIsNone(plugins.answers(["browser"]))
        self.assertTrue(plugins.valid_answers(entries) and plugins.valid_answers(None))
        changed = copy.deepcopy(entries)
        changed[0]["config"]["vaultMode"] = "unsafe-local"
        for bad in (changed, [dict(entries[0], id="browser")], entries + entries, [], "memory-wiki",
                    [dict(entries[0], tools=["exec"])]):
            with self.subTest(bad=bad):
                self.assertFalse(plugins.valid_answers(bad))

    def test_loading_is_exact_idempotent_and_clearing_restores_the_render(self):
        config = live_config()
        loaded = copy.deepcopy(config)
        plugins.load_portal_plugins(loaded, plugins.answers(["memory-wiki"]))
        self.assertEqual(loaded["plugins"]["allow"], ["pixel-ods", "llama-cpp", "memory-wiki"])
        self.assertEqual(loaded["plugins"]["entries"]["memory-wiki"],
                         {"enabled": True, "config": plugins.MEMORY_WIKI["config"]})
        self.assertEqual(loaded["tools"]["alsoAllow"], sorted(["exec", "read", *plugins.MEMORY_WIKI["tools"]]))
        self.assertNotIn("wiki_search", loaded["agents"]["list"][0]["tools"]["deny"])
        self.assertEqual(plugins.enabled_in(loaded), ["memory-wiki"])
        again = copy.deepcopy(loaded)
        plugins.load_portal_plugins(again, plugins.answers(["memory-wiki"]))
        self.assertEqual(again, loaded)
        plugins.clear(again)
        self.assertEqual(plugins.enabled_in(again), [])
        self.assertEqual((again["plugins"], again["tools"]), (config["plugins"], config["tools"]))
        # The reviewed configuration is copied, never shared with the catalog.
        loaded["plugins"]["entries"]["memory-wiki"]["config"]["vaultMode"] = "bridge"
        self.assertEqual(plugins.MEMORY_WIKI["config"]["vaultMode"], "isolated")


if __name__ == "__main__":
    unittest.main()
