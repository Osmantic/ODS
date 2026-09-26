"""Unit tests for config.py utility guards — Vishaaallll contributions."""
import os
import pytest


class TestInferMimeType:
    def test_json_extension(self):
        from config import infer_mime_type
        assert infer_mime_type("config.json") == "application/json"

    def test_yaml_extension(self):
        from config import infer_mime_type
        assert infer_mime_type("manifest.yaml") == "application/yaml"

    def test_yml_alias(self):
        from config import infer_mime_type
        assert infer_mime_type("settings.yml") == "application/yaml"

    def test_unknown_returns_fallback(self):
        from config import infer_mime_type
        assert infer_mime_type("model.bin") == "application/octet-stream"

    def test_none_returns_fallback(self):
        from config import infer_mime_type
        assert infer_mime_type(None) == "application/octet-stream"

    def test_no_extension_returns_fallback(self):
        from config import infer_mime_type
        assert infer_mime_type("Dockerfile") == "application/octet-stream"
