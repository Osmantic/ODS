#!/usr/bin/env python3
"""Deployable setup hooks must not turn entropy failures into blank secrets."""

import os
import subprocess
import tempfile
import unittest
from itertools import product
from pathlib import Path

SERVICES = Path(__file__).resolve().parents[1] / "extensions/library/services"
KEYS = {
    "anythingllm": ["ANYTHINGLLM_JWT_SECRET", "ANYTHINGLLM_AUTH_TOKEN"],
    "flowise": ["FLOWISE_USERNAME", "FLOWISE_PASSWORD"],
    "frigate": ["FRIGATE_RTSP_PASSWORD"],
    "jupyter": ["JUPYTER_TOKEN"],
    "librechat": ["JWT_SECRET", "JWT_REFRESH_SECRET", "LIBRECHAT_MONGO_PASSWORD", "LIBRECHAT_MEILI_KEY", "CREDS_KEY", "CREDS_IV"],
    "open-interpreter": ["OPEN_INTERPRETER_API_KEY"],
    "paperless-ngx": ["PAPERLESS_SECRET_KEY"],
    "weaviate": ["WEAVIATE_API_KEY"],
}


class SecretFailures(unittest.TestCase):
    def test_failed_or_empty_generator_never_appends_a_secret(self):
        for service, keys in KEYS.items():
            secret_keys = [key for key in keys if key != "FLOWISE_USERNAME"]
            for missing, mode in product(secret_keys, ("failure", "partial", "empty")):
                with self.subTest(service=service, missing=missing, mode=mode), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    env_file = root / ".env"
                    original = "KEEP=operator\n" + "".join(f"{key}=retained\n" for key in keys if key != missing)
                    env_file.write_text(original)
                    bindir = root / "bin"
                    bindir.mkdir()
                    stub = bindir / "openssl"
                    stub.write_text("#!/bin/sh\ncase $ODS_TEST_RAND_MODE in\n"
                                    "partial) printf 'unusable-output'; exit 77;;\n"
                                    "failure) exit 77;;\nempty) exit 0;;\nesac\n")
                    stub.chmod(0o755)
                    result = subprocess.run(
                        ["sh", str(SERVICES / service / "setup.sh"), str(root)],
                        env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "ODS_TEST_RAND_MODE": mode},
                        capture_output=True, text=True, check=False,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    saved = env_file.read_text()
                    self.assertEqual(saved, original)

    def test_completed_setup_does_not_require_the_generator(self):
        for service, keys in KEYS.items():
            with self.subTest(service=service), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                original = "KEEP=operator\n" + "".join(f"{key}=retained-{index}\n" for index, key in enumerate(keys))
                (root / ".env").write_text(original)
                bindir = root / "bin"
                bindir.mkdir()
                stub = bindir / "openssl"
                stub.write_text('#!/bin/sh\nprintf called >> "$ODS_TEST_RAND_CALLS"\nexit 77\n')
                stub.chmod(0o755)
                marker = root / "called"
                result = subprocess.run(
                    ["sh", str(SERVICES / service / "setup.sh"), str(root)],
                    env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "ODS_TEST_RAND_CALLS": str(marker)},
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((root / ".env").read_text(), original)
                self.assertFalse(marker.exists(), "already configured hook still invoked openssl")

    def test_real_generation_remains_complete_and_idempotent(self):
        for service, keys in KEYS.items():
            with self.subTest(service=service), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                command = ["sh", str(SERVICES / service / "setup.sh"), str(root)]
                subprocess.run(command, capture_output=True, check=True)
                original = (root / ".env").read_bytes()
                values = dict(line.split("=", 1) for line in original.decode().splitlines() if line)
                self.assertEqual(set(values), set(keys))
                self.assertTrue(all(values.values()))
                subprocess.run(command, capture_output=True, check=True)
                self.assertEqual((root / ".env").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
