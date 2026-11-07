"""The gateway planner matches what Pixel's renderer and the ODS overlay write."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
from pixel_connections import planner, plugins  # noqa: E402

INSTALL = "/home/owner/.local/share/pixel"
EXTENSION = "/opt/ods/extensions/services/pixel-agent/plugin"
READ_ONLY = {"calendar.read": True, "calendar.write": False, "calendar.direct": False,
             "gmail.read": True, "gmail.drafts": False}


def gateway(capabilities=None, zone=None, chosen=()):
    return dict(planner.limbs(capabilities), userTimezone=zone, plugins=list(chosen))


def disconnected_config():
    """The shape of a disconnected ODS render after the overlay, trimmed to what the planner reads."""
    deny = ["discord", "message", *planner.SOURCE_TOOLS, "pixel_frontier_plan_review"]
    allow = sorted(["read", "write", "exec", "pixel_ods_status", "pixel_ops_run", "update_plan"])
    return {
        "plugins": {"allow": ["pixel-ods", "searxng", "llama-cpp", "pixel-operations-broker"],
                    "entries": {"pixel-ods": {"enabled": True}, "llama-cpp": {"enabled": True},
                                "pixel-operations-broker": {"enabled": True}},
                    "load": {"paths": [EXTENSION, f"{INSTALL}/current/plugin-ops"]}},
        "tools": {"alsoAllow": list(allow), "sandbox": {"tools": {"allow": list(allow)}}, "profile": "coding"},
        "agents": {"defaults": {"bootstrapMaxChars": 32000},
                   "list": [{"id": "pixel", "tools": {"deny": deny}}]},
    }


class PlannerTests(unittest.TestCase):
    def test_count_tools_extend_only_their_read_groups_and_keep_legacy_order(self):
        legacy = {
            "GMAIL_TOOLS": ("pixel_gmail_inbox", "pixel_gmail_sent", "pixel_gmail_search",
                            "pixel_gmail_read", "pixel_gmail_thread"),
            "GMAIL_DRAFT_TOOLS": ("pixel_gmail_draft_propose",),
            "CALENDAR_READ_TOOLS": ("pixel_calendar_list", "pixel_calendar_get"),
            "CALENDAR_WRITE_TOOLS": ("pixel_calendar_propose_create", "pixel_calendar_propose_update",
                                     "pixel_calendar_propose_delete"),
            "SOCIAL_TOOLS": ("pixel_social_feed", "pixel_social_search"),
            "WEB_TOOLS": ("pixel_web_browse",),
        }
        added = {"pixel_gmail_count", "pixel_calendar_count"}
        for name, expected in legacy.items():
            actual = getattr(planner, name)
            self.assertEqual(tuple(tool for tool in actual if tool not in added), expected, name)
        self.assertEqual(set(planner.GMAIL_TOOLS) & added, {"pixel_gmail_count"})
        self.assertEqual(set(planner.CALENDAR_READ_TOOLS) & added, {"pixel_calendar_count"})
        self.assertEqual(tuple(tool for tool in planner.SOURCE_TOOLS if tool not in added),
                         ("pixel_limb_status", *(tool for group in legacy.values() for tool in group)))
        self.assertEqual(len(planner.SOURCE_TOOLS), len(set(planner.SOURCE_TOOLS)))

    def test_email_and_calendar_count_grants_remain_independent_of_other_limbs(self):
        for email in (False, True):
            for calendar in (False, True):
                for social in (False, True):
                    for web in (False, True):
                        with self.subTest(email=email, calendar=calendar, social=social, web=web):
                            config = disconnected_config()
                            retained = ((planner.SOCIAL_TOOLS if social else ()) +
                                        (planner.WEB_TOOLS if web else ()))
                            config["tools"]["alsoAllow"] += retained
                            target = gateway({"gmail.read": email, "calendar.read": calendar})
                            after = planner.plan_config(config, target, install_dir=INSTALL)
                            for names in (after["tools"]["alsoAllow"],
                                          after["tools"]["sandbox"]["tools"]["allow"]):
                                self.assertEqual("pixel_gmail_count" in names, email)
                                self.assertEqual("pixel_calendar_count" in names, calendar)
                                self.assertTrue(set(retained) <= set(names))
                                self.assertFalse(set(planner.GMAIL_DRAFT_TOOLS +
                                                     planner.CALENDAR_WRITE_TOOLS) & set(names))
                            deny = after["agents"]["list"][0]["tools"]["deny"]
                            self.assertEqual("pixel_gmail_count" in deny, not email)
                            self.assertEqual("pixel_calendar_count" in deny, not calendar)
                            self.assertEqual(after["tools"]["profile"], config["tools"]["profile"])

    def test_capabilities_map_to_the_five_limb_flags(self):
        off = dict(email=False, emailDrafts=False, calendar=False, calendarWrite=False, calendarDirect=False)
        self.assertEqual(planner.limbs(None), off)
        self.assertEqual(planner.limbs(READ_ONLY), dict(off, email=True, calendar=True))
        # Direct changes need writes, writes need Calendar read, and drafts need Gmail read.
        self.assertFalse(planner.limbs(dict(READ_ONLY, **{"calendar.direct": True}))["calendarDirect"])
        self.assertFalse(planner.limbs({"calendar.write": True})["calendarWrite"])
        self.assertFalse(planner.limbs({"gmail.drafts": True})["emailDrafts"])
        everything = dict(READ_ONLY, **{"calendar.write": True, "calendar.direct": True, "gmail.drafts": True})
        self.assertEqual(planner.limbs(everything), dict(email=True, emailDrafts=True, calendar=True,
                                                         calendarWrite=True, calendarDirect=True))

    def test_targets_are_exact(self):
        for bad in ({}, dict(gateway(), extra=True), dict(gateway(), email="yes"),
                    dict(gateway(), calendarWrite=True), dict(gateway(), emailDrafts=True),
                    dict(gateway(), emailDrafts="yes"), dict(gateway(), userTimezone="../etc"),
                    dict(gateway(), userTimezone="Not a zone")):
            with self.subTest(bad=bad), self.assertRaises(planner.PlanError):
                planner.target(bad)

    def test_a_target_journaled_before_gmail_drafts_has_drafts_off(self):
        legacy = {key: value for key, value in gateway(READ_ONLY).items() if key not in ("emailDrafts", "plugins")}
        self.assertEqual(planner.target(legacy), gateway(READ_ONLY))
        self.assertEqual(planner.plan_config(disconnected_config(), legacy, install_dir=INSTALL),
                         planner.plan_config(disconnected_config(), gateway(READ_ONLY), install_dir=INSTALL))
        # One journaled before the plugin catalog loads none.
        self.assertEqual(planner.target({key: value for key, value in gateway(READ_ONLY).items() if key != "plugins"}),
                         gateway(READ_ONLY))

    def test_a_chosen_plugin_loads_with_its_reviewed_configuration_and_unloads_whole(self):
        before = planner.plan_config(disconnected_config(), gateway(READ_ONLY), install_dir=INSTALL)
        after = planner.plan_config(before, gateway(READ_ONLY, chosen=["memory-wiki"]), install_dir=INSTALL)
        self.assertEqual(after["plugins"]["allow"], before["plugins"]["allow"] + ["memory-wiki"])
        self.assertEqual(after["plugins"]["entries"]["memory-wiki"],
                         {"enabled": True, "config": plugins.MEMORY_WIKI["config"]})
        self.assertEqual(set(after["tools"]["alsoAllow"]) - set(before["tools"]["alsoAllow"]),
                         set(plugins.MEMORY_WIKI["tools"]))
        self.assertEqual(after["tools"]["sandbox"]["tools"]["allow"], sorted(after["tools"]["sandbox"]["tools"]["allow"]))
        self.assertEqual(plugins.enabled_in(after), ["memory-wiki"])
        # A Google change keeps the plugin, and turning the plugin off restores the configuration exactly.
        connected = planner.plan_config(after, gateway(dict(READ_ONLY, **{"calendar.write": True}),
                                                       chosen=["memory-wiki"]), install_dir=INSTALL)
        self.assertEqual(plugins.enabled_in(connected), ["memory-wiki"])
        self.assertEqual(planner.plan_config(after, gateway(READ_ONLY), install_dir=INSTALL), before)

    def test_only_catalog_plugins_in_catalog_order_can_be_chosen(self):
        for chosen in (["browser"], ["memory-wiki", "memory-wiki"], "memory-wiki", [7]):
            with self.subTest(chosen=chosen), self.assertRaises(planner.PlanError):
                planner.target(dict(gateway(), plugins=chosen))

    def test_gmail_drafts_add_only_the_draft_tool(self):
        drafts = dict(READ_ONLY, **{"gmail.drafts": True})
        before = planner.plan_config(disconnected_config(), gateway(READ_ONLY), install_dir=INSTALL)
        after = planner.plan_config(before, gateway(drafts), install_dir=INSTALL)
        self.assertEqual(set(after["tools"]["alsoAllow"]) - set(before["tools"]["alsoAllow"]),
                         set(planner.GMAIL_DRAFT_TOOLS))
        self.assertFalse(set(planner.GMAIL_DRAFT_TOOLS) & set(after["agents"]["list"][0]["tools"]["deny"]))
        # Turning drafts off denies the tool again; Gmail read stays.
        off = planner.plan_config(after, gateway(READ_ONLY), install_dir=INSTALL)
        self.assertEqual(off["tools"]["alsoAllow"], before["tools"]["alsoAllow"])
        self.assertIn(planner.GMAIL_DRAFT_TOOLS[0], off["agents"]["list"][0]["tools"]["deny"])

    def test_connecting_adds_the_plugin_and_only_the_granted_tools(self):
        before = disconnected_config()
        after = planner.plan_config(before, gateway(READ_ONLY, "America/Chicago"), install_dir=INSTALL)
        wanted = ["pixel_limb_status", *planner.GMAIL_TOOLS, *planner.CALENDAR_READ_TOOLS]
        plugins = after["plugins"]
        self.assertEqual(plugins["allow"], ["pixel-ods", "searxng", "llama-cpp", planner.PLUGIN_ID, "pixel-operations-broker"])
        self.assertEqual(plugins["entries"][planner.PLUGIN_ID], {"enabled": True})
        self.assertEqual(plugins["load"]["paths"], [EXTENSION, f"{INSTALL}/current/plugin", f"{INSTALL}/current/plugin-ops"])
        self.assertEqual(after["tools"]["alsoAllow"], sorted(before["tools"]["alsoAllow"] + wanted))
        self.assertEqual(after["tools"]["sandbox"]["tools"]["allow"], sorted(before["tools"]["alsoAllow"] + wanted))
        deny = after["agents"]["list"][0]["tools"]["deny"]
        self.assertEqual(deny, [name for name in before["agents"]["list"][0]["tools"]["deny"] if name not in wanted])
        self.assertTrue(set(planner.CALENDAR_WRITE_TOOLS) <= set(deny))
        self.assertEqual(after["agents"]["defaults"]["userTimezone"], "America/Chicago")
        self.assertEqual(before, disconnected_config(), "planning must not change its input")
        self.assertEqual(planner.plan_config(after, gateway(READ_ONLY, "America/Chicago"), install_dir=INSTALL), after)

    def test_disconnecting_appends_the_tools_to_deny_in_render_order(self):
        connected = planner.plan_config(disconnected_config(), gateway(READ_ONLY), install_dir=INSTALL)
        after = planner.plan_config(connected, gateway(), install_dir=INSTALL)
        self.assertNotIn(planner.PLUGIN_ID, after["plugins"]["allow"])
        self.assertNotIn(planner.PLUGIN_ID, after["plugins"]["entries"])
        self.assertEqual(after["plugins"]["load"]["paths"], [EXTENSION, f"{INSTALL}/current/plugin-ops"])
        self.assertEqual(after["tools"]["alsoAllow"], disconnected_config()["tools"]["alsoAllow"])
        restored = [name for name in disconnected_config()["agents"]["list"][0]["tools"]["deny"]
                    if name not in ("pixel_limb_status", *planner.GMAIL_TOOLS, *planner.CALENDAR_READ_TOOLS)]
        restored += ["pixel_limb_status", *planner.GMAIL_TOOLS, *planner.CALENDAR_READ_TOOLS]
        self.assertEqual(after["agents"]["list"][0]["tools"]["deny"], restored)
        self.assertNotIn("userTimezone", after["agents"]["defaults"])

    def test_calendar_writes_add_the_proposal_tools(self):
        writes = dict(READ_ONLY, **{"calendar.write": True})
        after = planner.plan_config(disconnected_config(), gateway(writes), install_dir=INSTALL)
        self.assertTrue(set(planner.CALENDAR_WRITE_TOOLS) <= set(after["tools"]["alsoAllow"]))
        self.assertFalse(set(planner.CALENDAR_WRITE_TOOLS) & set(after["agents"]["list"][0]["tools"]["deny"]))

    def test_a_live_web_limb_keeps_the_plugin_after_google_disconnects(self):
        config = disconnected_config()
        config["tools"]["alsoAllow"] = sorted(config["tools"]["alsoAllow"] + ["pixel_limb_status", "pixel_web_browse"])
        config["plugins"]["allow"].insert(3, planner.PLUGIN_ID)
        after = planner.plan_config(config, gateway(), install_dir=INSTALL)
        self.assertIn(planner.PLUGIN_ID, after["plugins"]["allow"])
        self.assertIn("pixel_web_browse", after["tools"]["alsoAllow"])

    def test_a_configuration_outside_the_rendered_shape_is_refused(self):
        broken = []
        for path in (("plugins", "allow"), ("tools", "alsoAllow"), ("agents", "list")):
            value = disconnected_config()
            value[path[0]][path[1]] = "not a list"
            broken.append(value)
        no_llama = disconnected_config()
        no_llama["plugins"]["allow"].remove("llama-cpp")
        broken.append(no_llama)
        two = disconnected_config()
        two["agents"]["list"].append(copy.deepcopy(two["agents"]["list"][0]))
        broken.append(two)
        for value in broken:
            with self.subTest(value=value), self.assertRaises(planner.PlanError):
                planner.plan_config(value, gateway(READ_ONLY), install_dir=INSTALL)

    def test_the_gateway_environment_changes_only_its_limb_flags(self):
        text = ("OPENCLAW_STATE_DIR='/home/owner/.openclaw'\nPIXEL_CALENDAR_DIRECT_ENABLED='0'\n"
                "PIXEL_LIMB_EMAIL_ENABLED='0'\nPIXEL_LIMB_CALENDAR_ENABLED='0'\n"
                "PIXEL_LIMB_CALENDAR_WRITE_ENABLED='0'\nPIXEL_LIMB_WEB_ENABLED='0'\n")
        planned = planner.plan_gateway_env(text, gateway(READ_ONLY))
        self.assertEqual(planned, text.replace("PIXEL_LIMB_EMAIL_ENABLED='0'", "PIXEL_LIMB_EMAIL_ENABLED='1'")
                         .replace("PIXEL_LIMB_CALENDAR_ENABLED='0'", "PIXEL_LIMB_CALENDAR_ENABLED='1'"))
        self.assertEqual(planner.gateway_limbs(planned), planner.limbs(READ_ONLY))
        self.assertEqual(planner.plan_gateway_env(planned, gateway()), text)
        for bad in (text.rstrip("\n"), text + "export X=1\n", text + "PIXEL_LIMB_EMAIL_ENABLED='1'\n",
                    text.replace("PIXEL_LIMB_EMAIL_ENABLED='0'\n", "")):
            with self.subTest(bad=bad), self.assertRaises(planner.PlanError):
                planner.plan_gateway_env(bad, gateway(READ_ONLY))

    def test_the_drafts_flag_is_set_where_pixel_wrote_it(self):
        drafts = gateway(dict(READ_ONLY, **{"gmail.drafts": True}))
        # Pixel 4.3.35 writes the flag; an earlier Pixel has none, which means drafts are off.
        text = ("PIXEL_LIMB_EMAIL_DRAFTS_ENABLED='0'\nPIXEL_CALENDAR_DIRECT_ENABLED='0'\nPIXEL_LIMB_EMAIL_ENABLED='0'\n"
                "PIXEL_LIMB_CALENDAR_ENABLED='0'\nPIXEL_LIMB_CALENDAR_WRITE_ENABLED='0'\n")
        planned = planner.plan_gateway_env(text, drafts)
        self.assertIn("PIXEL_LIMB_EMAIL_DRAFTS_ENABLED='1'\n", planned)
        self.assertEqual(planner.gateway_limbs(planned), planner.limbs(dict(READ_ONLY, **{"gmail.drafts": True})))
        self.assertEqual(planner.plan_gateway_env(planned, gateway(READ_ONLY)),
                         text.replace("PIXEL_LIMB_EMAIL_ENABLED='0'", "PIXEL_LIMB_EMAIL_ENABLED='1'")
                         .replace("PIXEL_LIMB_CALENDAR_ENABLED='0'", "PIXEL_LIMB_CALENDAR_ENABLED='1'"))
        earlier = text.replace("PIXEL_LIMB_EMAIL_DRAFTS_ENABLED='0'\n", "")
        self.assertFalse(planner.gateway_limbs(earlier)["emailDrafts"])
        self.assertNotIn("DRAFTS", planner.plan_gateway_env(earlier, gateway(READ_ONLY)))
        with self.assertRaisesRegex(planner.PlanError, "update ODS"):
            planner.plan_gateway_env(earlier, drafts)
        with self.assertRaises(planner.PlanError):
            planner.gateway_limbs(text.replace("DRAFTS_ENABLED='0'", "DRAFTS_ENABLED='yes'"))


GATEWAY_ENV = ("OPENCLAW_STATE_DIR='/home/owner/.openclaw'\nPIXEL_CALENDAR_DIRECT_ENABLED='0'\n"
               "PIXEL_LIMB_EMAIL_ENABLED='0'\nPIXEL_LIMB_CALENDAR_ENABLED='0'\nPIXEL_LIMB_CALENDAR_WRITE_ENABLED='0'\n")


@unittest.skipUnless(sys.platform.startswith("linux"), "the installer converges connected apps on Linux and WSL")
class ConvergeTests(unittest.TestCase):
    """The installer step that finishes a connect or disconnect the coordinator could not."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.config, self.env = root / "openclaw.json", root / "gateway.env"
        self.generated, self.pixel_env = root / "generated-gateway.env", root / "pixel.env"
        self.write(self.config, json.dumps(disconnected_config()))
        self.write(self.env, GATEWAY_ENV)
        self.write(self.pixel_env, "PIXEL_USER_TIMEZONE='America/Chicago'\n")

    def write(self, path, text, mode=0o600):
        path.write_text(text)
        path.chmod(mode)

    def converge(self):
        return subprocess.run([sys.executable, "-I", str(ROOT / "bin/pixel_connections/converge.py"), str(self.config),
                               str(self.env), str(self.generated), str(self.pixel_env), INSTALL],
                              capture_output=True, text=True)

    def test_a_connection_the_coordinator_could_not_finish_is_finished_by_the_update(self):
        connected = planner.plan_gateway_env(GATEWAY_ENV, gateway(READ_ONLY))
        self.write(self.generated, connected)
        result = self.converge()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "changed\n", ""))
        expected = planner.plan_config(disconnected_config(), gateway(READ_ONLY, "America/Chicago"), install_dir=INSTALL)
        self.assertEqual(json.loads(self.config.read_text()), expected)
        self.assertEqual(self.env.read_text(), connected)
        for path in (self.config, self.env):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.converge().stdout, "unchanged\n")
        # Disconnecting converges the same way.
        self.write(self.generated, GATEWAY_ENV)
        self.assertEqual(self.converge().stdout, "changed\n")
        self.assertNotIn(planner.PLUGIN_ID, json.loads(self.config.read_text())["plugins"]["allow"])
        self.assertEqual(self.env.read_text(), GATEWAY_ENV)

    def test_unsafe_or_unexpected_files_stop_the_update_unchanged(self):
        self.write(self.generated, planner.plan_gateway_env(GATEWAY_ENV, gateway(READ_ONLY)))
        before = self.config.read_bytes()
        self.config.chmod(0o644)
        result = self.converge()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe Pixel file: openclaw.json", result.stderr)
        self.config.chmod(0o600)
        self.write(self.config, json.dumps(dict(disconnected_config(), plugins={"allow": "not a list"})))
        result = self.converge()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside the connected-apps shape", result.stderr)
        self.write(self.config, before.decode())
        self.write(self.env, GATEWAY_ENV + "export X=1\n")
        self.assertNotEqual(self.converge().returncode, 0)
        self.assertEqual(self.config.read_bytes(), before)


@unittest.skipUnless(shutil.which("node"), "executes the actual legacy Source config migration")
class SourceConfigMigrationTests(unittest.TestCase):
    def test_count_ids_extend_the_legacy_source_surface_without_changing_other_policy(self):
        source = ("pixel_limb_status", *planner.GMAIL_TOOLS, *planner.CALENDAR_READ_TOOLS,
                  *planner.CALENDAR_WRITE_TOOLS, *planner.SOCIAL_TOOLS)
        original = {
            "plugins": {"allow": ["owner-plugin", "pixel-google-workspace"],
                        "entries": {"owner-plugin": {"enabled": False, "config": {"keep": "owner-value"}},
                                    "pixel-google-workspace": {"enabled": True}},
                        "load": {"paths": ["/owner/custom-plugin", "/legacy/source"]}},
            "tools": {"alsoAllow": ["read", "owner_custom"],
                      "sandbox": {"tools": {"allow": ["read", "pixel_calendar_create"],
                                            "deny": ["owner_sandbox_deny"]}}},
            "agents": {"defaults": {"userTimezone": "Europe/Berlin"},
                       "list": [{"id": "pixel", "tools": {"deny": ["owner_pixel_deny"]}},
                                {"id": "another-agent", "tools": {"deny": ["owner_other_deny"]}}]},
            "mcp": {"servers": {"owner": {"enabled": False, "command": "/usr/bin/true"}}},
        }
        with tempfile.TemporaryDirectory(prefix="ods-count-source-migration-") as directory:
            path = Path(directory) / "openclaw.json"
            path.write_text(json.dumps(original))
            arguments = ["node", str(ROOT / "vendor/pixel/scripts/migrate-source-broker-config.mjs"),
                         str(path), "/current/source", "/legacy/source"]
            for _ in range(2):
                result = subprocess.run(arguments, capture_output=True, text=True, check=True)
                self.assertEqual(result.stderr, "")
                actual = json.loads(path.read_text())
                self.assertEqual(actual["tools"]["alsoAllow"], original["tools"]["alsoAllow"])
                self.assertEqual(actual["tools"]["sandbox"]["tools"],
                                 {"allow": ["read", *source], "deny": ["owner_sandbox_deny"]})
                self.assertEqual(actual["agents"]["list"][0], original["agents"]["list"][0])
                self.assertEqual(actual["agents"]["list"][1]["tools"]["deny"],
                                 ["owner_other_deny", *source])
                self.assertEqual(actual["agents"]["defaults"], original["agents"]["defaults"])
                self.assertEqual(actual["mcp"], original["mcp"])
                self.assertEqual(actual["plugins"]["entries"]["owner-plugin"],
                                 original["plugins"]["entries"]["owner-plugin"])
                self.assertFalse(set(planner.GMAIL_DRAFT_TOOLS + planner.WEB_TOOLS) &
                                 set(actual["tools"]["sandbox"]["tools"]["allow"]))
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)


def _env_file(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, _, quoted = line.partition("=")
        values[key] = quoted[1:-1].replace("'\\''", "'")
    return values


@unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("node"),
                     "connected apps ship on Linux and WSL; renders with Pixel's configure and Node.js")
class RenderCrossCheckTests(unittest.TestCase):
    """Pixel's configure and renderer plus the ODS overlay produce what the planner predicts."""

    @classmethod
    def setUpClass(cls):
        cls.workdir = tempfile.TemporaryDirectory()
        root = Path(cls.workdir.name)
        os.umask(0o022)
        cls.pixel = root / "pixel"
        shutil.copytree(ROOT / "vendor" / "pixel", cls.pixel, ignore=shutil.ignore_patterns(
            ".git", ".generated", ".env", "dist", "node_modules", "__pycache__"))
        cls.home = root / "home"
        (cls.home / ".openclaw").mkdir(parents=True, mode=0o700)
        cls.data = root / "data"
        cls.data.mkdir(mode=0o700)
        key = cls.data / "relay-key"
        key.write_text("test-only-relay-key")
        key.chmod(0o600)
        (cls.data / "operations-policy.json").write_text(json.dumps({"schemaVersion": 2, "targets": {}, "actions": {}}))
        (cls.data / "operations-policy.json").chmod(0o600)
        plugin = ROOT / "extensions" / "services" / "pixel-agent" / "plugin"
        digest = subprocess.run(["node", str(cls.pixel / "scripts" / "hash-gateway-extension.mjs"), str(plugin)],
                                check=True, capture_output=True, text=True).stdout.strip()
        cls.openclaw = cls.pixel / "tests" / "fixtures" / "bin" / "openclaw"
        cls.answers = cls.data / "onboarding.json"
        cls.contract = [str(cls.answers), str(cls.openclaw), str(cls.home), "Qwen3.5-9B", "16384", "4096", "false",
                        "ods/current", "Current", "4006", "18789", str(key), "8888", str(plugin), digest,
                        "searxng", "", ""]
        cls.install = str(cls.home / ".local" / "share" / "pixel")

    @classmethod
    def tearDownClass(cls):
        cls.workdir.cleanup()

    def setUp(self):
        (self.home / ".openclaw" / "openclaw.json").unlink(missing_ok=True)

    def onboard(self, record):
        arguments = list(self.contract)
        if record is not None:
            directory = Path(self.workdir.name) / "etc-ods"
            directory.mkdir(mode=0o755, exist_ok=True)
            path = directory / "portal-connections.json"
            path.write_text(json.dumps(record))
            path.chmod(0o640)
            arguments += [str(path), "present"]
        subprocess.run([sys.executable, "-I", str(ROOT / "installers" / "lib" / "pixel-onboarding.py"), *arguments],
                       cwd="/", check=True, capture_output=True, text=True)

    def render(self, record):
        """One installer render of ``record``: onboarding, configure, render, overlay; the result goes live."""
        self.onboard(record)
        env = dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / ".config"),
                   PATH=f"{self.openclaw.parent}{os.pathsep}{os.environ['PATH']}")
        result = subprocess.run(["node", "scripts/configure.mjs", "--answers", str(self.answers), "--force"],
                                cwd=self.pixel, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        env.update(_env_file(self.pixel / ".env"))
        env.update(PIXEL_PLUGIN_PATH=f"{self.install}/current/plugin",
                   PIXEL_OPS_PLUGIN_PATH=f"{self.install}/current/plugin-ops",
                   PIXEL_FRONTIER_PLUGIN_PATH=f"{self.install}/current/plugin-frontier")
        rendered = Path(self.workdir.name) / "rendered.json"
        result = subprocess.run(["node", "scripts/render-config.mjs", str(rendered)], cwd=self.pixel, env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        staged = subprocess.run([sys.executable, str(ROOT / "installers" / "lib" / "pixel-runtime-budget.py"),
                                 str(rendered), "3004", str(self.answers), str(self.home / ".openclaw")],
                                check=True, capture_output=True, text=True).stdout.strip()
        live = self.home / ".openclaw" / "openclaw.json"
        shutil.copyfile(rendered if staged == "unchanged" else staged, live)
        live.chmod(0o600)
        return json.loads(live.read_text()), (self.pixel / ".generated" / "gateway.env").read_text()

    def record(self, capabilities, zone="America/Chicago", chosen=()):
        google = {"status": "connected", "account": "owner@example.com", "calendarId": "primary",
                  "gmailWindowDays": 180, "capabilities": capabilities, "cloudModels": False,
                  "connectedAt": "2026-10-25T20:00:00Z"}
        value = {"schemaVersion": 1, "kind": "ods-portal-connections", "updatedAt": "2026-10-25T20:00:00Z",
                 "ownerTimeZone": zone, "connectors": {"google": google} if capabilities else {}}
        return dict(value, plugins=dict.fromkeys(chosen, True)) if chosen else value

    def assert_planned(self, before, after, capabilities, zone, chosen=()):
        config, env = before
        planned = planner.plan_config(config, gateway(capabilities, zone, chosen), install_dir=self.install)
        self.assertEqual(json.dumps(planned, indent=2, sort_keys=True), json.dumps(after[0], indent=2, sort_keys=True))
        self.assertEqual(planner.plan_gateway_env(env, gateway(capabilities, zone, chosen)), after[1])

    def test_connect_and_disconnect_match_the_renderer(self):
        writes = dict(READ_ONLY, **{"calendar.write": True})
        drafts = dict(writes, **{"gmail.drafts": True})
        calendar_only = dict(READ_ONLY, **{"gmail.read": False})
        gmail_only = dict(READ_ONLY, **{"calendar.read": False})
        wiki = ["memory-wiki"]
        disconnected = self.render(None)
        steps = [(READ_ONLY, "America/Chicago", ()), (writes, "America/Chicago", ()),
                 (drafts, "America/Chicago", ()), (drafts, "America/Chicago", wiki), (writes, "America/Chicago", wiki),
                 (None, "America/Chicago", wiki), (calendar_only, "Europe/Berlin", ()),
                 (gmail_only, "Europe/Berlin", ()), (None, "Europe/Berlin", ())]
        previous = disconnected
        for capabilities, zone, chosen in steps:
            with self.subTest(capabilities=capabilities, zone=zone, chosen=chosen):
                current = self.render(self.record(capabilities, zone, chosen))
                # Each step really changes what Pixel renders and ODS's overlay loads.
                self.assertEqual(planner.PLUGIN_ID in current[0]["plugins"]["allow"], bool(capabilities))
                self.assertEqual(planner.gateway_limbs(current[1]), planner.limbs(capabilities))
                self.assertEqual(current[0]["agents"]["defaults"]["userTimezone"], zone)
                self.assertEqual(plugins.enabled_in(current[0]), list(chosen))
                for tool, cap in (("pixel_gmail_count", "gmail.read"), ("pixel_calendar_count", "calendar.read")):
                    expected = bool(capabilities and capabilities.get(cap))
                    self.assertEqual(tool in current[0]["tools"]["alsoAllow"], expected)
                    self.assertEqual(tool in current[0]["tools"]["sandbox"]["tools"]["allow"], expected)
                    self.assertEqual(tool in current[0]["agents"]["list"][0]["tools"]["deny"], not expected)
                self.assert_planned(previous, current, capabilities, zone, chosen)
                previous = current

    def test_a_disconnected_render_is_a_fixed_point(self):
        disconnected = self.render(None)
        self.assert_planned(disconnected, self.render(None), None, None)

    def test_native_mcp_permissions_survive_render_and_connection_changes(self):
        initial, _ = self.render(None)
        native = {"servers": {"qa-project": {"transport": "stdio", "command": "/usr/bin/true",
                  "args": ["synthetic-only"], "env": {"QA_VALUE": "private-fixture"},
                  "toolFilter": {"include": ["project_status"], "exclude": ["project_delete"]}}}}
        initial["mcp"] = native
        initial["tools"]["alsoAllow"].append("qa-project__project_status")
        initial["tools"]["deny"] = ["qa-project__project_delete"]
        initial["tools"]["byProvider"] = {"restricted-provider": {"allow": ["read"]}}
        initial["tools"]["sandbox"]["tools"]["allow"].append("qa-project__project_status")
        initial["tools"]["sandbox"]["tools"]["deny"] = ["qa-project__project_delete"]
        initial["agents"]["list"][0]["tools"]["alsoAllow"] = ["qa-project__project_status"]
        live = self.home / ".openclaw/openclaw.json"
        live.write_text(json.dumps(initial))
        live.chmod(0o600)
        previous = self.render(None)
        for capabilities, chosen in ((READ_ONLY, ()), (READ_ONLY, ("memory-wiki",)),
                                     (None, ("memory-wiki",)), (None, ())):
            with self.subTest(capabilities=capabilities, chosen=chosen):
                current = self.render(self.record(capabilities, chosen=chosen))
                config = current[0]
                self.assertEqual(config["mcp"], native)
                self.assertIn("qa-project__project_status", config["tools"]["sandbox"]["tools"]["allow"])
                self.assertEqual(config["tools"]["sandbox"]["tools"]["deny"], ["qa-project__project_delete"])
                self.assertEqual(config["tools"]["deny"], ["qa-project__project_delete"])
                self.assertEqual(config["tools"]["byProvider"], initial["tools"]["byProvider"])
                self.assertEqual(config["agents"]["list"][0]["tools"]["alsoAllow"], ["qa-project__project_status"])
                self.assert_planned(previous, current, capabilities, "America/Chicago", chosen)
                previous = current

    def test_native_registry_alone_never_grants_mcp_and_restrictive_policy_is_retained(self):
        initial, _ = self.render(None)
        initial["mcp"] = {"servers": {"disabled-project": {"enabled": False,
                          "transport": "stdio", "command": "/usr/bin/true"}}}
        live = self.home / ".openclaw/openclaw.json"
        live.write_text(json.dumps(initial))
        live.chmod(0o600)
        unchanged, _ = self.render(None)
        self.assertEqual(unchanged["mcp"], initial["mcp"])
        self.assertEqual(unchanged["tools"], initial["tools"])
        self.assertEqual(unchanged["agents"]["list"][0]["tools"], initial["agents"]["list"][0]["tools"])
        # A restrictive list with no MCP names must not be discarded and turn
        # into the wider coding profile on the next render.
        unchanged["tools"]["allow"] = ["read"]
        unchanged["agents"]["list"][0]["tools"]["allow"] = ["read"]
        live.write_text(json.dumps(unchanged))
        config, _ = self.render(None)
        self.assertEqual(config["tools"]["allow"], ["read"])
        self.assertEqual(config["agents"]["list"][0]["tools"]["allow"], ["read"])


if __name__ == "__main__":
    unittest.main()
