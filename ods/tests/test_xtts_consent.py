"""Stdlib checks using Compose config only; execute a harmless python3 stand-in."""
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

RECIPE = Path(__file__).resolve().parents[1] / "extensions/library/services/xtts"


class XttsConsentTests(unittest.TestCase):
    @classmethod
    def render(cls, variant=None, choice="0"):
        env = os.environ.copy()
        if choice is None:
            env.pop("COQUI_TOS_AGREED", None)
        else:
            env["COQUI_TOS_AGREED"] = choice
        command = ["docker", "compose", "--env-file", os.devnull, "-f", str(RECIPE / "compose.yaml")]
        if variant:
            command.extend(["-f", str(RECIPE / f"compose.{variant}.yaml")])
        result = subprocess.run([*command, "config", "--format", "json"], env=env,
                                text=True, capture_output=True, check=True, timeout=30)
        return json.loads(result.stdout)["services"]["xtts"]

    def test_explicit_acceptance_before_server(self):
        service = self.render()
        self.assertEqual(service["environment"]["COQUI_TOS_AGREED"], "0")
        self.assertIsNone(service.get("entrypoint"))  # Preserve NVIDIA initialization.
        command = service["command"]
        self.assertEqual(command[:2], ["/bin/sh", "-c"])
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            marker = temporary / "server-called"
            fake_python = temporary / "python3"
            fake_python.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$MARKER"\nif read -r line; then exit 99; fi\n')
            fake_python.chmod(0o755)
            for choice in (None, "", "0", "true", "yes", "1"):
                with self.subTest(choice=choice):
                    marker.unlink(missing_ok=True)
                    env = {"PATH": str(temporary) + ":" + os.defpath, "MARKER": str(marker), "PORT": "80"}
                    if choice is not None:
                        env["COQUI_TOS_AGREED"] = choice
                    # Compose config preserves $$ for later container interpolation.
                    result = subprocess.run([*command[:2], command[2].replace("$$", "$")], env=env,
                                            input="y\n", text=True, capture_output=True, timeout=5)
                    accepted = choice == "1"
                    self.assertEqual(marker.exists(), accepted)
                    self.assertEqual(result.returncode, 0 if accepted else 78)
                    if accepted:
                        self.assertEqual(marker.read_text().splitlines(), [
                            "-m", "xtts_api_server", "-hs", "0.0.0.0", "-sf", "/app/example",
                            "-t", "http://localhost:80", "-p", "80", "-ms", "apiManual",
                        ])
                    else:
                        self.assertIn("explicit acceptance", result.stderr)

    def test_overlays_preserve_guard_and_operator_value(self):
        base = self.render()
        for variant in ("amd", "nvidia"):
            with self.subTest(variant=variant):
                service = self.render(variant)
                self.assertEqual(service["command"], base["command"])
                self.assertEqual(service["environment"]["COQUI_TOS_AGREED"], "0")

    def test_missing_or_empty_consent_prevents_compose_creation(self):
        for choice in (None, ""):
            with self.subTest(choice=choice):
                with self.assertRaises(subprocess.CalledProcessError) as result:
                    self.render(choice=choice)
                self.assertIn("explicitly set COQUI_TOS_AGREED=1", result.exception.stderr)

    def test_manifest_has_no_acceptance_default(self):
        manifest = (RECIPE / "manifest.yaml").read_text(encoding="utf-8")
        consent = re.search(r"(?ms)^  env_vars:\n(.*?)(?=^  \S)", manifest)
        self.assertIsNotNone(consent)
        self.assertIn("key: COQUI_TOS_AGREED", consent[1])
        self.assertIn("required: true", consent[1])
        self.assertNotRegex(consent[1], r"\bdefault:")
        self.assertNotIn("setup_hook:", manifest)


if __name__ == "__main__":
    unittest.main()
