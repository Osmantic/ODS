"""Roundtrip, isolation and malformed-baseline tests for access-mode changes."""
import copy
import json
import os
import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).parents[1] / "host" / "access_mode_config.py"
SPEC = importlib.util.spec_from_file_location("access_mode_config", MODULE_PATH)
assert SPEC and SPEC.loader
access_mode_config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(access_mode_config)
MigrationError = access_mode_config.MigrationError
capture_baseline = access_mode_config.capture_baseline
enable = access_mode_config.enable
restore = access_mode_config.restore

HERE = os.path.dirname(os.path.abspath(__file__))


def load_sample():
    with open(os.path.join(HERE, "access-mode-safe-config.json")) as f:
        return json.load(f)


def pixel_of(config):
    return [e for e in config["agents"]["list"] if e.get("id") == "pixel"][0]


class TestMigration(unittest.TestCase):
    # ---- previously passing behavior ----

    def test_roundtrip(self):
        original = load_sample()
        config = copy.deepcopy(original)
        config, baseline = enable(config)
        pixel = pixel_of(config)
        self.assertEqual(pixel["sandbox"]["mode"], "off")
        self.assertEqual(pixel["tools"]["exec"],
                         {"host": "gateway", "security": "full", "ask": "off"})
        self.assertIs(pixel["tools"]["fs"]["workspaceOnly"], False)
        self.assertNotIn("baseline", json.dumps(config))
        config, restored = restore(config, baseline)
        self.assertEqual(config, original)

    def test_no_non_schema_keys_inserted(self):
        original = load_sample()
        config = copy.deepcopy(original)
        config, baseline = enable(config)
        self.assertEqual(config["agents"]["list"][1], original["agents"]["list"][1])
        self.assertEqual(config["agents"]["defaults"], original["agents"]["defaults"])
        config, _ = restore(config, baseline)
        self.assertEqual(config, original)

    def test_repeated_enable_preserves_original_baseline(self):
        original = load_sample()
        config = copy.deepcopy(original)
        _, b1 = enable(config)
        _, b2 = enable(config, baseline=b1)
        self.assertIs(b1, b2)
        _, b3 = enable(copy.deepcopy(original))
        self.assertEqual(b1, b3)
        config, _ = restore(config, b1)
        self.assertEqual(config, original)

    def test_restore_preserves_unrelated_later_edits(self):
        original = load_sample()
        config = copy.deepcopy(original)
        _, baseline = enable(config)
        config["agents"]["list"][0]["model"] = "new-model"
        config["agents"]["list"][0]["note"] = "later"
        config["extra"] = 1
        config, restored = restore(config, baseline)
        self.assertTrue(restored)
        pixel = pixel_of(config)
        self.assertEqual(pixel["sandbox"]["mode"], "all")
        self.assertEqual(pixel["tools"]["exec"]["host"], "sandbox")
        self.assertEqual(pixel["model"], "new-model")
        self.assertEqual(pixel["note"], "later")
        self.assertEqual(config["extra"], 1)

    def test_missing_pixel_rejected_without_mutation(self):
        original = load_sample()
        config = copy.deepcopy(original)
        config["agents"]["list"] = [config["agents"]["list"][1]]
        snapshot = copy.deepcopy(config)
        with self.assertRaises(MigrationError):
            enable(config)
        self.assertEqual(config, snapshot)
        with self.assertRaises(MigrationError):
            restore(config, {"sandbox.mode": {"present": True, "value": "all"}})
        self.assertEqual(config, snapshot)

    def test_duplicate_pixel_rejected_without_mutation(self):
        config = load_sample()
        config["agents"]["list"].append(copy.deepcopy(pixel_of(config)))
        snapshot = copy.deepcopy(config)
        with self.assertRaises(MigrationError):
            enable(config)
        self.assertEqual(config, snapshot)

    def test_enable_malformed_shape_rejected_without_mutation(self):
        original = load_sample()
        config = copy.deepcopy(original)
        config["agents"]["list"][0]["tools"]["exec"] = {"host": {"deep": True}}
        config["agents"]["list"][0]["tools"]["fs"] = ["list"]
        snapshot = copy.deepcopy(config)
        with self.assertRaises(MigrationError) as cm:
            enable(config)
        msg = str(cm.exception)
        self.assertNotIn("deep", msg)
        self.assertNotIn("{", msg)
        self.assertIn("agents.list", msg)  # '[' appears only as path syntax
        self.assertNotIn("{'deep'", msg)
        self.assertEqual(config, snapshot)

    def test_absent_field_restored_to_absence(self):
        original = load_sample()
        config = copy.deepcopy(original)
        del config["agents"]["list"][0]["tools"]["fs"]
        expected = copy.deepcopy(config)
        _, baseline = enable(config)
        self.assertFalse(baseline["tools.fs.workspaceOnly"]["present"])
        config, _ = restore(config, baseline)
        self.assertEqual(config, expected)
        self.assertNotIn("fs", pixel_of(config)["tools"])

    # ---- regression 1: preexisting empty intermediates survive ----

    def test_empty_intermediates_survive_roundtrip(self):
        original = {"agents": {"list": [
            {"id": "pixel", "sandbox": {}, "tools": {"exec": {}, "fs": {}}}]}}
        config = copy.deepcopy(original)
        config, baseline = enable(config)
        for key in ("sandbox.mode", "tools.exec.host", "tools.exec.security",
                    "tools.exec.ask", "tools.fs.workspaceOnly"):
            self.assertTrue(baseline[key]["present"] is False)
            self.assertEqual(baseline[key]["parents"], [True] * (len(key.split(".")) - 1))
        config, _ = restore(config, baseline)
        self.assertEqual(config, original)  # empty dicts preserved

    def test_empty_intermediates_partially_absent(self):
        original = {"agents": {"list": [
            {"id": "pixel", "tools": {"exec": {}}}]}}  # no sandbox, no fs
        config = copy.deepcopy(original)
        _, baseline = enable(config)
        self.assertEqual(baseline["sandbox.mode"]["parents"], [False])
        self.assertEqual(baseline["tools.fs.workspaceOnly"]["parents"], [True, False])
        self.assertEqual(baseline["tools.exec.host"]["parents"], [True, True])
        config, _ = restore(config, baseline)
        self.assertEqual(config, original)  # empty exec dict kept; others pruned

    # ---- fresh-install shapes: missing parents recorded, roundtrip exact ----

    def _fresh_roundtrip(self, pixel_overrides):
        original = {"agents": {
            "defaults": {"sandbox": {"mode": "all"}},
            "list": [dict({"id": "pixel"}, **pixel_overrides),
                     {"id": "other", "model": "unchanged-model"}]}}
        config = copy.deepcopy(original)
        config, baseline = enable(config)
        has_s = "sandbox" in pixel_overrides
        has_t = "tools" in pixel_overrides
        has_e = has_t and "exec" in pixel_overrides["tools"]
        has_f = has_t and "fs" in pixel_overrides["tools"]
        expected_parents = {
            "sandbox.mode": [has_s],
            "tools.exec.host": [has_t, has_e],
            "tools.exec.security": [has_t, has_e],
            "tools.exec.ask": [has_t, has_e],
            "tools.fs.workspaceOnly": [has_t, has_f],
        }
        for key, n in (("sandbox.mode", 1), ("tools.exec.host", 2),
                       ("tools.exec.security", 2), ("tools.exec.ask", 2),
                       ("tools.fs.workspaceOnly", 2)):
            self.assertEqual(baseline[key]["parents"], expected_parents[key],
                             "parent presence for %s" % key)
            self.assertFalse(baseline[key]["present"])
        pixel = pixel_of(config)
        self.assertEqual(pixel["sandbox"]["mode"], "off")
        self.assertEqual(pixel["tools"]["exec"],
                         {"host": "gateway", "security": "full", "ask": "off"})
        self.assertIs(pixel["tools"]["fs"]["workspaceOnly"], False)
        self.assertEqual(config["agents"]["defaults"], {"sandbox": {"mode": "all"}})
        self.assertEqual(config["agents"]["list"][1], {"id": "other", "model": "unchanged-model"})
        config, restored = restore(config, baseline)
        self.assertEqual(config, original)  # exact roundtrip, empties pruned

    def test_fresh_install_pixel_only(self):
        self._fresh_roundtrip({})

    def test_fresh_install_sandbox_empty_only(self):
        self._fresh_roundtrip({"sandbox": {}})

    def test_fresh_install_tools_empty_only(self):
        self._fresh_roundtrip({"tools": {}})

    def test_fresh_install_empty_exec_fs_dicts(self):
        self._fresh_roundtrip({"sandbox": {}, "tools": {"exec": {}, "fs": {}}})

    def test_enable_does_not_mutate_input(self):
        original = {"agents": {"list": [{"id": "pixel"}]}}
        snapshot = copy.deepcopy(original)
        config, baseline = enable(original)
        self.assertEqual(original, snapshot)  # input untouched
        config, restored = restore(config, baseline)
        self.assertTrue(restored)
        self.assertEqual(config, snapshot)  # restored copy back to original
        self.assertEqual(original, snapshot)

    # ---- regression 2: restore validates everything before mutating ----

    def test_restore_rejects_later_malformed_without_mutation(self):
        original = load_sample()
        config = copy.deepcopy(original)
        _, baseline = enable(config)
        pixel_of(config)["tools"]["exec"] = "later-malformed"
        snapshot = copy.deepcopy(config)
        with self.assertRaises(MigrationError):
            restore(config, baseline)
        self.assertEqual(config, snapshot)  # nothing partially changed

    # ---- baseline schema validation ----

    def test_baseline_missing_record_rejected(self):
        baseline = capture_baseline(pixel_of(load_sample()))
        del baseline["sandbox.mode"]
        with self.assertRaises(MigrationError):
            restore(load_sample(), baseline)

    def test_baseline_unknown_record_rejected(self):
        baseline = capture_baseline(pixel_of(load_sample()))
        baseline["evil.field"] = {"present": False, "value": None, "parents": []}
        with self.assertRaises(MigrationError):
            restore(load_sample(), baseline)

    def test_baseline_non_boolean_presence_rejected(self):
        baseline = capture_baseline(pixel_of(load_sample()))
        baseline["sandbox.mode"]["present"] = "yes"
        with self.assertRaises(MigrationError):
            restore(load_sample(), baseline)

    def test_baseline_wrong_scalar_type_rejected(self):
        baseline = capture_baseline(pixel_of(load_sample()))
        baseline["tools.fs.workspaceOnly"] = {
            "present": True, "value": "true", "parents": [True]}
        with self.assertRaises(MigrationError):
            restore(load_sample(), baseline)

    def test_enable_with_invalid_baseline_rejected(self):
        config = load_sample()
        snapshot = copy.deepcopy(config)
        with self.assertRaises(MigrationError):
            enable(config, baseline={"sandbox.mode": {"present": True, "value": 3, "parents": []}})
        self.assertEqual(config, snapshot)

    def test_enable_transactional_on_error(self):
        # One valid field + one malformed path: whole enable must abort.
        original = load_sample()
        config = copy.deepcopy(original)
        config["agents"]["list"][0]["sandbox"] = "not-a-dict"
        snapshot = copy.deepcopy(config)
        with self.assertRaises(MigrationError):
            enable(config)
        self.assertEqual(config, snapshot)

    # ---- refinement: path-only root errors, never echo config values ----

    def test_root_shapes_rejected_with_path_only_errors(self):
        cases = [
            "not-a-dict-at-all",
            ["list-root"],
            [],
            {"agents": "yes"},
            {"agents": []},
            {"agents": {"defaults": {}}},
            {"agents": {"list": "agents-list-as-string"}},
            {"agents": {"list": 7}},
            {"agents": {"list": None}},
        ]
        for cfg in cases:
            with self.assertRaises(MigrationError) as cm:
                enable(cfg)
            msg = str(cm.exception)
            self.assertNotIn("yes", msg)
            self.assertNotIn("agents-list-as-string", msg)
            self.assertIn("malformed config", msg)
            self.assertIn("root", msg)
            with self.assertRaises(MigrationError):
                restore(cfg, capture_baseline({"id": "pixel"}))

    def test_root_errors_mention_only_paths(self):
        config = {"agents": {"list": [{"id": "someone-else"}]}}
        with self.assertRaises(MigrationError) as cm:
            enable(config)
        msg = str(cm.exception)
        self.assertIn("id 'pixel'", msg)
        self.assertNotIn("someone-else", msg)

    # ---- refinement: enum validation of baseline values ----

    def test_baseline_invalid_enum_rejected(self):
        original = load_sample()
        config = copy.deepcopy(original)
        _, baseline = enable(config)
        for key, bad in (("sandbox.mode", "sometimes"),
                         ("tools.exec.host", "cluster"),
                         ("tools.exec.security", "full-access"),
                         ("tools.exec.ask", "maybe")):
            broken = copy.deepcopy(baseline)
            broken[key]["value"] = bad
            snapshot = copy.deepcopy(config)
            with self.assertRaises(MigrationError) as cm:
                restore(config, broken)
            msg = str(cm.exception)
            self.assertNotIn(bad, msg)
            self.assertIn(key, msg)
            self.assertEqual(config, snapshot)
            with self.assertRaises(MigrationError):
                enable(config, baseline=broken)

    def test_baseline_enum_message_lists_only_allowed_values(self):
        baseline = capture_baseline(pixel_of(load_sample()))
        baseline["sandbox.mode"]["value"] = "sometimes"
        with self.assertRaises(MigrationError) as cm:
            restore(load_sample(), baseline)
        msg = str(cm.exception)
        for allowed in ("off", "non-main", "all"):
            self.assertIn(allowed, msg)
        self.assertNotIn("sometimes", msg)

    # ---- refinement: baseline parent-relationship validation ----

    def test_baseline_parent_present_after_absent_rejected(self):
        baseline = capture_baseline(pixel_of(load_sample()))
        baseline["tools.exec.host"]["parents"] = [False, True]
        with self.assertRaises(MigrationError):
            restore(load_sample(), baseline)

    def test_baseline_shared_parent_disagreement_rejected(self):
        baseline = capture_baseline(pixel_of(load_sample()))
        baseline["tools.exec.host"]["parents"] = [True, True]
        baseline["tools.exec.security"]["parents"] = [True, False]
        with self.assertRaises(MigrationError):
            restore(load_sample(), baseline)

    def test_baseline_parent_inconsistency_rejected_on_enable_too(self):
        config = load_sample()
        baseline = capture_baseline(pixel_of(config))
        baseline["tools.fs.workspaceOnly"]["parents"] = [False, True]
        with self.assertRaises(MigrationError):
            enable(config, baseline=baseline)

    # ---- refinement: no mutation after error; later edits preserved ----

    def test_no_mutation_after_root_error_on_restore(self):
        original = load_sample()
        config = copy.deepcopy(original)
        _, baseline = enable(config)
        config["agents"] = "broken-agents-object"
        snapshot = copy.deepcopy(config)
        with self.assertRaises(MigrationError):
            restore(config, baseline)
        self.assertEqual(config, snapshot)

    def test_no_mutation_after_baseline_enum_error(self):
        original = load_sample()
        config = copy.deepcopy(original)
        _, baseline = enable(config)
        config["agents"]["list"][0]["sandbox"] = {}
        snapshot = copy.deepcopy(config)
        baseline["tools.exec.ask"]["value"] = "maybe"
        with self.assertRaises(MigrationError):
            restore(config, baseline)
        self.assertEqual(config, snapshot)

    def test_unrelated_edits_after_enable_preserved_through_restore(self):
        original = load_sample()
        config = copy.deepcopy(original)
        _, baseline = enable(config)
        config["agents"]["list"][1]["model"] = "other-later-model"
        config["agents"]["defaults"]["theme"] = "dark"
        config["top"] = {"level": "kept"}
        config, _ = restore(config, baseline)
        self.assertEqual(config["agents"]["list"][1]["model"], "other-later-model")
        self.assertEqual(config["agents"]["defaults"],
                         {"sandbox": {"mode": "all"}, "theme": "dark"})
        self.assertEqual(config["top"], {"level": "kept"})
        pixel = pixel_of(config)
        self.assertEqual(pixel["sandbox"]["mode"], "all")
        self.assertEqual(pixel["tools"]["exec"]["host"], "sandbox")

    def test_repeated_enable_unchanged_baseline_selfconsistent(self):
        original = load_sample()
        config = copy.deepcopy(original)
        _, b1 = enable(config)
        config2, b2 = enable(config, baseline=b1)
        self.assertIs(b1, b2)
        expected_enabled, _ = enable(copy.deepcopy(original))
        self.assertEqual(config2, expected_enabled)  # same enable result
        config3, _ = restore(config2, b2)
        self.assertEqual(config3, original)


    def test_present_leaf_requires_all_parents(self):
        original = {"agents": {"list": [{"id": "pixel"}]}}
        enabled, baseline = enable(original)
        for key, value in [("sandbox.mode", "all"), ("tools.exec.host", "sandbox"),
                           ("tools.exec.security", "allowlist"), ("tools.exec.ask", "on-miss"),
                           ("tools.fs.workspaceOnly", True)]:
            with self.subTest(key=key):
                invalid = copy.deepcopy(baseline)
                invalid[key].update(present=True, value=value)
                before = copy.deepcopy(enabled)
                with self.assertRaises(MigrationError):
                    restore(enabled, invalid)
                self.assertEqual(enabled, before)

    def test_unknown_baseline_keys_do_not_leak_their_contents(self):
        config = load_sample()
        _, baseline = enable(config)
        marker = "private-secret-marker"
        for nested in (False, True):
            invalid = copy.deepcopy(baseline)
            target = invalid["sandbox.mode"] if nested else invalid
            target[marker] = True
            with self.assertRaises(MigrationError) as cm:
                restore(config, invalid)
            self.assertNotIn(marker, str(cm.exception))

    def test_all_empty_parent_presence_combinations(self):
        for sandbox in (False, True):
            for tools in (None, {}, {"exec": {}}, {"fs": {}}, {"exec": {}, "fs": {}}):
                with self.subTest(sandbox=sandbox, tools=tools):
                    agent = {"id": "pixel"}
                    if sandbox:
                        agent["sandbox"] = {}
                    if tools is not None:
                        agent["tools"] = copy.deepcopy(tools)
                    original = {"agents": {"defaults": {"sandbox": {"mode": "all"}},
                                           "list": [agent, {"id": "other"}]}}
                    enabled, baseline = enable(original)
                    restored, _ = restore(enabled, baseline)
                    self.assertEqual(restored, original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
