"""Unit test suite for model library catalog metadata resilience."""

import unittest


def parse_model_catalog_entry(entry: dict) -> dict[str, object]:
    if not isinstance(entry, dict):
        return {"id": "unknown", "valid": False}
    model_id = entry.get("id") or entry.get("model_id") or "unknown"
    return {
        "id": str(model_id),
        "valid": bool(model_id != "unknown"),
        "quantization": str(entry.get("quant", "q4_k_m")),
    }


class TestModelLibraryResilience(unittest.TestCase):
    def test_parse_model_catalog_entry_valid(self):
        item = parse_model_catalog_entry({"id": "llama-3-8b-instruct", "quant": "q8_0"})
        self.assertEqual(item["id"], "llama-3-8b-instruct")
        self.assertTrue(item["valid"])
        self.assertEqual(item["quantization"], "q8_0")

    def test_parse_model_catalog_entry_invalid(self):
        item = parse_model_catalog_entry(None)
        self.assertEqual(item["id"], "unknown")
        self.assertFalse(item["valid"])


if __name__ == "__main__":
    unittest.main()
