"""Unit test suite for GGUF tensor architecture metadata resilience."""

import unittest


def extract_gguf_architecture(metadata: dict) -> str:
    if not isinstance(metadata, dict):
        return "llama"
    arch = metadata.get("general.architecture") or metadata.get("architecture")
    if not arch or not isinstance(arch, str):
        return "llama"
    return arch.strip().lower()


class TestGGUFTensorMetaResilience(unittest.TestCase):
    def test_extract_architecture_valid(self):
        self.assertEqual(extract_gguf_architecture({"general.architecture": "Qwen2"}), "qwen2")

    def test_extract_architecture_fallback(self):
        self.assertEqual(extract_gguf_architecture({}), "llama")
        self.assertEqual(extract_gguf_architecture(None), "llama")


if __name__ == "__main__":
    unittest.main()
