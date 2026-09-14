"""Unit test suite for YAML block indentation formatting resilience."""

import unittest


def format_yaml_block_indentation(yaml_str: str | None, indent_spaces: int = 2) -> str:
    if not yaml_str or not isinstance(yaml_str, str):
        return ""
    spaces = " " * max(0, min(8, int(indent_spaces)))
    lines = yaml_str.strip().splitlines()
    return "\n".join(f"{spaces}{line}" if line.strip() else "" for line in lines)


class TestYAMLIndentationFormatterResilience(unittest.TestCase):
    def test_format_yaml_indent_valid(self):
        res = format_yaml_block_indentation("key: value\nfoo: bar", indent_spaces=2)
        self.assertEqual(res, "  key: value\n  foo: bar")

    def test_format_yaml_indent_none(self):
        self.assertEqual(format_yaml_block_indentation(None), "")


if __name__ == "__main__":
    unittest.main()
