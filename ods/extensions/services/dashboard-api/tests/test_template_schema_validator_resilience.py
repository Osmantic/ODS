"""Unit test suite for template schema structure validation resilience."""

import unittest


def validate_template_schema(template_dict: dict | None) -> tuple[bool, str]:
    if not template_dict or not isinstance(template_dict, dict):
        return False, "missing_schema"
    if "name" not in template_dict or not template_dict["name"]:
        return False, "missing_name"
    if "steps" not in template_dict or not isinstance(template_dict["steps"], list):
        return False, "invalid_steps"
    return True, "valid"


class TestTemplateSchemaValidatorResilience(unittest.TestCase):
    def test_validate_template_valid(self):
        ok, reason = validate_template_schema({"name": "pipeline", "steps": []})
        self.assertTrue(ok)
        self.assertEqual(reason, "valid")

    def test_validate_template_missing_name(self):
        ok, reason = validate_template_schema({"steps": []})
        self.assertFalse(ok)
        self.assertEqual(reason, "missing_name")


if __name__ == "__main__":
    unittest.main()
