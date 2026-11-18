import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))

import opencode_config as oc  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        p = self.dir / name
        p.write_bytes(text.encode("utf-8"))
        return p

    def read(self, name):
        return (self.dir / name).read_bytes().decode("utf-8")


class TestCommentsAcrossExtensions(Base):
    def test_comment_in_each_extension_survives(self):
        for name in ("config.json", "opencode.json", "opencode.jsonc"):
            with self.subTest(name=name):
                for f in self.dir.iterdir():
                    f.unlink()
                self.write(name, '{\n  // owner comment\n  "theme": "dark"\n}\n')
                oc.update_config(self.dir, {"provider": {"llama-server": {"name": "x"}}})
                text = self.read(name)
                self.assertIn("// owner comment", text)
                self.assertIn('"theme": "dark"', text)
                self.assertIn('"llama-server"', text)

    def test_block_comment_and_trailing_comma(self):
        self.write("opencode.json", '{\n  /* block */\n  "a": 1,\n}\n')
        oc.update_config(self.dir, {"b": 2})
        text = self.read("opencode.json")
        self.assertIn("/* block */", text)
        self.assertIn('"a": 1', text)
        self.assertIn('"b": 2', text)

    def test_bom_preserved(self):
        self.write("opencode.json", '\ufeff{\n  "a": 1\n}\n')
        oc.update_config(self.dir, {"b": 2})
        text = self.read("opencode.json")
        self.assertTrue(text.startswith("\ufeff"))
        self.assertIn('"b": 2', text)

    def test_nested_object_replacement_preserves_owner_comments(self):
        self.write(
            "opencode.json",
            '{\n  // owner comment for provider\n  "provider": {\n    // inner comment\n    "llama-server": {"name": "old"}\n  }\n}\n',
        )
        oc.update_config(self.dir, {"provider": {"llama-server": {"name": "new"}}})
        text = self.read("opencode.json")
        self.assertIn("// owner comment for provider", text)
        self.assertIn("// inner comment", text)
        data = oc.parse_jsonc(text)
        self.assertEqual(data["provider"]["llama-server"]["name"], "new")

    def test_comments_after_last_member_preserved(self):
        self.write(
            "opencode.json",
            '{\n  "a": 1\n  // trailing comment\n}\n',
        )
        oc.update_config(self.dir, {"b": 2})
        text = self.read("opencode.json")
        self.assertIn("// trailing comment", text)
        data = oc.parse_jsonc(text)
        self.assertEqual(data, {"a": 1, "b": 2})

    def test_comments_after_last_member_with_comma(self):
        self.write(
            "opencode.json",
            '{\n  "a": 1,\n  // trailing comment\n}\n',
        )
        oc.update_config(self.dir, {"b": 2})
        text = self.read("opencode.json")
        self.assertIn("// trailing comment", text)
        data = oc.parse_jsonc(text)
        self.assertEqual(data, {"a": 1, "b": 2})

    def test_empty_object_with_comments_preserved(self):
        self.write("opencode.json", '{\n  // only a comment\n}\n')
        oc.update_config(self.dir, {"a": 1})
        text = self.read("opencode.json")
        self.assertIn("// only a comment", text)
        data = oc.parse_jsonc(text)
        self.assertEqual(data, {"a": 1})

    def test_many_new_keys_produce_valid_output(self):
        self.write("opencode.json", '{\n  "a": 1\n}\n')
        patch = {f"k{i}": i for i in range(20)}
        oc.update_config(self.dir, patch)
        data = json.loads(self.read("opencode.json"))
        self.assertEqual(data["a"], 1)
        for i in range(20):
            self.assertEqual(data[f"k{i}"], i)


class TestPrecedence(Base):
    def test_effective_merge_order(self):
        self.write("config.json", '{"a": 1, "b": {"x": 1, "y": 1}}')
        self.write("opencode.json", '{"b": {"y": 2}, "c": 2}')
        self.write("opencode.jsonc", '{"c": 3}')
        eff = oc.read_effective(self.dir)
        self.assertEqual(eff, {"a": 1, "b": {"x": 1, "y": 2}, "c": 3})

    def test_arrays_replaced(self):
        self.write("config.json", '{"a": [1, 2]}')
        self.write("opencode.json", '{"a": [3]}')
        self.assertEqual(oc.read_effective(self.dir)["a"], [3])

    def test_only_highest_precedence_written(self):
        self.write("config.json", '{"a": 1}')
        self.write("opencode.json", '{"b": 2}')
        before = self.read("config.json")
        oc.update_config(self.dir, {"c": 3})
        self.assertEqual(self.read("config.json"), before)
        self.assertIn('"c": 3', self.read("opencode.json"))

    def test_creates_opencode_json_when_none(self):
        path = oc.update_config(self.dir, {"a": 1})
        self.assertEqual(path.name, "opencode.json")
        self.assertEqual(json.loads(self.read("opencode.json")), {"a": 1})


class TestDistinctCompatPreserved(Base):
    def test_lower_file_byte_identical(self):
        self.write("config.json", '{\n  // keep me\n  "owner": {"apiKey": "sk-x"}\n}\n')
        self.write("opencode.json", '{"a": 1}')
        before = self.read("config.json")
        oc.update_config(self.dir, {"provider": {"llama-server": {"name": "x"}}})
        self.assertEqual(self.read("config.json"), before)

    def test_no_mirroring(self):
        self.write("opencode.json", '{"a": 1}')
        oc.update_config(self.dir, {"b": 2})
        self.assertFalse((self.dir / "config.json").exists())


class TestMalformedAnyUnchanged(Base):
    def test_malformed_lower_blocks_write(self):
        self.write("config.json", '{ broken')
        self.write("opencode.json", '{"a": 1}')
        before = self.read("opencode.json")
        with self.assertRaises(oc.ConfigError):
            oc.update_config(self.dir, {"b": 2})
        self.assertEqual(self.read("opencode.json"), before)

    def test_malformed_target_blocks_write(self):
        self.write("opencode.json", '{ broken')
        with self.assertRaises(oc.ConfigError):
            oc.update_config(self.dir, {"b": 2})

    def test_incomplete_comment(self):
        self.write("opencode.json", '{"a": 1 /* unterminated')
        with self.assertRaises(oc.ConfigError):
            oc.update_config(self.dir, {"b": 2})

    def test_trailing_garbage(self):
        self.write("opencode.json", '{"a": 1} extra')
        with self.assertRaises(oc.ConfigError):
            oc.update_config(self.dir, {"b": 2})

    def test_invalid_number(self):
        self.write("opencode.json", '{"a": 01}')
        with self.assertRaises(oc.ConfigError):
            oc.update_config(self.dir, {"b": 2})


class TestDuplicateKey(Base):
    def test_duplicate_rejected(self):
        self.write("opencode.json", '{"a": 1, "a": 2}')
        with self.assertRaises(oc.ConfigError):
            oc.update_config(self.dir, {"b": 3})


class TestSymlink(Base):
    def test_symlink_rejected(self):
        real = self.dir / "real.json"
        real.write_text('{"a": 1}', encoding="utf-8")
        link = self.dir / "opencode.json"
        os.symlink(real, link)
        with self.assertRaises(oc.ConfigError):
            oc.update_config(self.dir, {"b": 2})
        self.assertEqual(real.read_text(encoding="utf-8"), '{"a": 1}')

    def test_nonregular_rejected(self):
        p = self.dir / "opencode.json"
        p.mkdir()
        with self.assertRaises(oc.ConfigError):
            oc.update_config(self.dir, {"b": 2})

    def test_config_dir_symlink_rejected(self):
        real = self.dir / "real"
        real.mkdir()
        link = self.dir / "link"
        os.symlink(real, link)
        with self.assertRaises(oc.ConfigError):
            oc.update_config(link, {"a": 1})


class TestInvalidShape(Base):
    def test_non_object_root(self):
        self.write("opencode.json", '[1, 2]')
        with self.assertRaises(oc.ConfigError):
            oc.update_config(self.dir, {"b": 2})

    def test_wrong_typed_intermediate(self):
        self.write("opencode.json", '{"provider": "oops"}')
        with self.assertRaises(oc.ConfigError):
            oc.update_config(self.dir, {"provider": {"llama-server": {"name": "x"}}})
        self.assertEqual(self.read("opencode.json"), '{"provider": "oops"}')

    def test_null_intermediate_rejected(self):
        self.write("opencode.json", '{"provider": null}')
        with self.assertRaises(oc.ConfigError):
            oc.update_config(self.dir, {"provider": {"llama-server": {"name": "x"}}})
        self.assertEqual(self.read("opencode.json"), '{"provider": null}')

    def test_lower_array_intermediate_rejected(self):
        self.write("opencode.json", '{"provider": [1, 2]}')
        with self.assertRaises(oc.ConfigError):
            oc.update_config(self.dir, {"provider": {"llama-server": {"name": "x"}}})
        self.assertEqual(self.read("opencode.json"), '{"provider": [1, 2]}')

    def test_missing_intermediate_added(self):
        self.write("opencode.json", '{"a": 1}')
        oc.update_config(self.dir, {"provider": {"llama-server": {"name": "x"}}})
        data = json.loads(self.read("opencode.json"))
        self.assertEqual(data["provider"]["llama-server"]["name"], "x")

    def test_leaf_array_replaced_only_when_explicit(self):
        self.write("opencode.json", '{"a": [1, 2], "b": [3]}')
        oc.update_config(self.dir, {"a": [9]})
        data = json.loads(self.read("opencode.json"))
        self.assertEqual(data["a"], [9])
        self.assertEqual(data["b"], [3])


class TestRepeatedIdempotence(Base):
    def test_second_update_no_write(self):
        self.write("opencode.json", '{\n  // c\n  "a": 1\n}\n')
        patch = {"provider": {"llama-server": {"name": "x"}}}
        oc.update_config(self.dir, patch)
        first = self.read("opencode.json")
        mtime = (self.dir / "opencode.json").stat().st_mtime_ns
        oc.update_config(self.dir, patch)
        self.assertEqual(self.read("opencode.json"), first)
        self.assertEqual((self.dir / "opencode.json").stat().st_mtime_ns, mtime)


class TestInjectedWriteFailure(Base):
    def test_failure_leaves_original(self):
        self.write("opencode.json", '{"a": 1}')
        before = self.read("opencode.json")
        with mock.patch.object(oc.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                oc.update_config(self.dir, {"b": 2})
        self.assertEqual(self.read("opencode.json"), before)
        leftovers = [p for p in self.dir.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])


class TestUnicodeAndEscapes(Base):
    def test_unicode_and_escaped_strings(self):
        self.write("opencode.json", '{"s": "caf\\u00e9 \\"q\\""}')
        oc.update_config(self.dir, {"t": "\u00e9"})
        data = json.loads(self.read("opencode.json"))
        self.assertEqual(data["s"], 'caf\u00e9 "q"')
        self.assertEqual(data["t"], "\u00e9")

    def test_escaped_unicode_preserved(self):
        self.write("opencode.json", '{"s": "\\u00e9"}')
        oc.update_config(self.dir, {"t": 1})
        text = self.read("opencode.json")
        self.assertIn('"s": "\\u00e9"', text)


class TestCRLFPreserved(Base):
    def test_crlf_preserved(self):
        self.write("opencode.json", '{\r\n  "a": 1\r\n}\r\n')
        oc.update_config(self.dir, {"b": 2})
        raw = (self.dir / "opencode.json").read_bytes()
        self.assertIn(b'"a": 1\r\n', raw.replace(b',', b''))
        self.assertNotIn(b'"a": 1\n', raw.replace(b'"a": 1\r\n', b''))


class TestConcurrentWriter(Base):
    def test_competing_writer_detected(self):
        self.write("opencode.json", '{"a": 1}')
        before = self.read("opencode.json")
        original_snapshot = oc._snapshot
        calls = {"n": 0}

        def patched_snapshot(directory):
            calls["n"] += 1
            if calls["n"] == 2:
                # Simulate a competing writer between validation and commit.
                (directory / "opencode.json").write_bytes(b'{"a": 2}')
            return original_snapshot(directory)

        with mock.patch.object(oc, "_snapshot", side_effect=patched_snapshot):
            with self.assertRaises(oc.ConfigError):
                oc.update_config(self.dir, {"b": 2})
        # The competing writer's content is what remains; our write was aborted.
        self.assertEqual(self.read("opencode.json"), '{"a": 2}')
        self.assertNotEqual(before, self.read("opencode.json"))


class TestMalformedPatch(Base):
    def test_non_object_patch_rejected(self):
        self.write("opencode.json", '{"a": 1}')
        with self.assertRaises(oc.ConfigError):
            oc.update_config(self.dir, [1, 2])

    def test_nonfinite_patch_rejected(self):
        self.write("opencode.json", '{"a": 1}')
        with self.assertRaises((ValueError, oc.ConfigError)):
            oc.update_config(self.dir, {"x": float("nan")})


if __name__ == "__main__":
    unittest.main()
