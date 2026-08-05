#!/usr/bin/env python3
"""Regression tests for YAML scalar handling in the Hermes config patcher."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

import yaml


SCRIPT = Path(__file__).parents[1] / "scripts" / "patch-hermes-config.py"
SPEC = importlib.util.spec_from_file_location("patch_hermes_config", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
PATCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCHER)


class TestHermesConfigScalarEscaping(unittest.TestCase):
    MODEL = 'model"\\name\ninjected_model: true'
    BASE_URL = 'http://local.example/"\\v1\ninjected_url: true'
    API_KEY = 'secret"\\value\ninjected_key: true'

    def _patch_and_load(self, initial: str) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text(initial, encoding="utf-8")

            PATCHER.patch_config(
                config_path,
                self.MODEL,
                self.BASE_URL,
                65536,
                self.API_KEY,
            )

            return yaml.safe_load(config_path.read_text(encoding="utf-8"))

    def _assert_values_are_data(self, config: dict) -> None:
        self.assertEqual(config["model"]["default"], self.MODEL)
        self.assertEqual(config["model"]["base_url"], self.BASE_URL)
        self.assertEqual(config["model"]["api_key"], self.API_KEY)
        self.assertNotIn("injected_model", config)
        self.assertNotIn("injected_url", config)
        self.assertNotIn("injected_key", config)

    def test_escapes_values_in_existing_model_block(self):
        config = self._patch_and_load(
            'model:\n  default: "old-model"\n  base_url: "http://old/v1"\n'
        )

        self._assert_values_are_data(config)

    def test_escapes_values_when_creating_model_block(self):
        config = self._patch_and_load('terminal:\n  backend: "local"\n')

        self._assert_values_are_data(config)


if __name__ == "__main__":
    unittest.main()
