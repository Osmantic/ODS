"""Independent regressions for reviewed provider URL and JSON boundaries."""

import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
from pixel_provider.config import ConfigError, default_config, normalize_config
from pixel_provider.store import StoreError, decode_document


def fixture():
    value = default_config()
    value["providers"] = [{
        "id": "test", "label": "Test", "kind": "local",
        "baseUrl": "http://localhost:8080/v1", "model": "test-model",
        "contextTokens": 8192, "maxOutputTokens": 1024,
        "supportsTools": True, "supportsVision": False, "reasoning": False,
        "credentialRef": None, "enabled": True,
    }]
    # Validate the fixture first, so negative URLs cannot pass merely because
    # an unrelated stale role reference already made the fixture invalid.
    return normalize_config(value)


class Boundaries(unittest.TestCase):
    def test_depth_ignores_quoted_and_escaped_brackets(self):
        value = {"text": '[' * 100 + '\\"' + '}' * 100}
        self.assertEqual(decode_document(json.dumps(value).encode()), value)

    def test_adversarial_urls(self):
        bad = (
            "https://[::1]ignored/v1", "https://[v1.example]/v1",
            "https://[fe80::1%25eth0]/v1", "https://[::ffff:169.254.169.254]/v1",
            "https://[::ffff:169.254.170.2]/v1", "https://169.254.170.2/v1",
            "https://[::]/v1", "https://224.0.0.1/v1",
            "http://127.evil.test/v1", "http://127.1/v1",
            "http://2130706433/v1", "http://0x7f000001/v1",
            "http://localhost:+1/v1", "http://localhost:/v1",
            "http://localhost:65536/v1", "http://localhost:0/v1",
            "http://localhost/v10", "http://localhost/v1/chat",
            "http://localhost/v1/../v1", "http://localhost/a%2fb/v1",
            "http://@localhost/v1", "http://localhost/v1?",
            "http://localhost/v1#", "http://localhost/v1\n",
        )
        for url in bad:
            with self.subTest(url=url):
                value = fixture()
                value["providers"][0]["baseUrl"] = url
                with self.assertRaises(ConfigError):
                    normalize_config(value)

    def test_malformed_json(self):
        for raw in (b'{"x":1,"x":2}', b'NaN', b'Infinity', b'1e999',
                    b'"\xff"', b'[' * 5000 + b']' * 5000, b'x' * (256 * 1024 + 1)):
            with self.subTest(prefix=raw[:12]):
                with self.assertRaises(StoreError) as raised:
                    decode_document(raw)
                self.assertEqual(raised.exception.code, "malformed-json")

    def test_wrong_json_field_types(self):
        sample = fixture()
        for field in sample["providers"][0]:
            for invalid in ([], {}, None):
                if field == "credentialRef" and invalid is None:
                    continue
                value = copy.deepcopy(sample)
                value["providers"][0][field] = invalid
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaises(ConfigError):
                        normalize_config(value)


if __name__ == "__main__":
    unittest.main()
