#!/usr/bin/env python3
"""Regression tests for service initialization vs missing component classification in ods-preflight.sh."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
PREFLIGHT_SCRIPT = ROOT_DIR / "ods-preflight.sh"


class TestPreflightServiceInitialization(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        # Clean up any preflight-*.log files created in ROOT_DIR
        for log_file in ROOT_DIR.glob("preflight-*.log"):
            try:
                log_file.unlink()
            except OSError:
                pass

    def test_initializing_containers_distinguished_from_missing(self):
        """When containers are running in Docker but endpoints are warming up / loading,

        preflight reports initialization in progress instead of missing components.
        """
        with tempfile.TemporaryDirectory() as stub_dir:
            curl_path = Path(stub_dir) / "curl"
            curl_path.write_text("#!/bin/sh\nexit 1\n")
            curl_path.chmod(0o755)

            docker_path = Path(stub_dir) / "docker"
            docker_path.write_text(
                """#!/bin/sh
if [ "$1" = "info" ]; then exit 0; fi
if [ "$1" = "compose" ]; then echo "Docker Compose v2.20.0"; exit 0; fi
if [ "$1" = "ps" ]; then
    printf "%s\n" "ods-whisper" "ods-tts" "ods-embeddings" "ods-dashboard" "ods-llama-server"
    exit 0
fi
exit 0
"""
            )
            docker_path.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = stub_dir + ":" + env["PATH"]
            env["GPU_BACKEND"] = "cpu"

            proc = subprocess.run(
                ["bash", str(PREFLIGHT_SCRIPT)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            stdout = proc.stdout
            self.assertIn("Whisper STT container running but not responding yet", stdout)
            self.assertIn("TTS container running but not responding yet", stdout)
            self.assertIn("Embeddings container running but not responding yet", stdout)
            self.assertIn("Dashboard container running but not responding yet", stdout)
            self.assertIn("llama-server container running but not responding yet", stdout)

            self.assertNotIn("Whisper STT not found", stdout)
            self.assertNotIn("TTS not found", stdout)
            self.assertNotIn("Embeddings not found", stdout)
            self.assertNotIn("Dashboard not found at port", stdout)

    def test_missing_containers_reported_as_unavailable(self):
        """When containers are not running in Docker, preflight reports components as missing."""
        with tempfile.TemporaryDirectory() as stub_dir:
            curl_path = Path(stub_dir) / "curl"
            curl_path.write_text("#!/bin/sh\nexit 1\n")
            curl_path.chmod(0o755)

            docker_path = Path(stub_dir) / "docker"
            docker_path.write_text(
                """#!/bin/sh
if [ "$1" = "info" ]; then exit 0; fi
if [ "$1" = "compose" ]; then echo "Docker Compose v2.20.0"; exit 0; fi
if [ "$1" = "ps" ]; then
    exit 0
fi
exit 0
"""
            )
            docker_path.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = stub_dir + ":" + env["PATH"]
            env["GPU_BACKEND"] = "cpu"

            proc = subprocess.run(
                ["bash", str(PREFLIGHT_SCRIPT)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            stdout = proc.stdout
            self.assertIn("Whisper STT not found — voice input will be unavailable", stdout)
            self.assertIn("TTS not found — voice output will be unavailable", stdout)
            self.assertIn("Embeddings not found — RAG features will be unavailable", stdout)
            self.assertIn("Dashboard not found at port", stdout)
            self.assertIn("No LLM endpoint found", stdout)

            self.assertNotIn("container running but not responding yet", stdout)


if __name__ == "__main__":
    unittest.main()
