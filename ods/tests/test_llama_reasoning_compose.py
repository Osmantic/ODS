import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "extensions/services/llama-server/llama-server-entrypoint.sh"
MODES = ["cpu", "nvidia", "intel", "arc", "amd", "amd-rocm", "apple"]
ENV = {
    "WEBUI_SECRET": "dummy-webui",
    "SEARXNG_SECRET": "dummy-searxng",
    "LITELLM_KEY": "dummy-litellm",
    "N8N_USER": "dummy-user",
    "N8N_PASS": "dummy-pass",
    "LLAMA_REASONING": "OFF",
}


def compose_config(files):
    cmd = ["docker", "compose"]
    for f in files:
        cmd += ["-f", str(f)]
    cmd += ["config", "--format", "json"]
    env = {**os.environ, **ENV}
    r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True,
                       text=True, timeout=30, check=True)
    return json.loads(r.stdout)


def base_files():
    return [ROOT / "docker-compose.base.yml"]


def overlay(mode):
    return ROOT / f"docker-compose.{mode}.yml"


class TestComposeModes(unittest.TestCase):
    def _check(self, cfg):
        svc = cfg["services"]["llama-server"]
        env = svc.get("environment", {})
        if isinstance(env, list):
            env = dict(e.split("=", 1) for e in env if "=" in e)
        self.assertEqual(env.get("LLAMA_ARG_REASONING"), "OFF",
                         "LLAMA_ARG_REASONING not OFF")
        self.assertEqual(svc.get("entrypoint"),
                         ["/bin/sh", "/opt/ods/llama-server-entrypoint.sh"],
                         "entrypoint mismatch")
        vols = svc.get("volumes", [])
        matches = [v for v in vols
                   if isinstance(v, dict)
                   and v.get("target") == "/opt/ods/llama-server-entrypoint.sh"]
        self.assertEqual(len(matches), 1, "entrypoint mount count != 1")
        m = matches[0]
        self.assertTrue(m.get("read_only"), "entrypoint mount not read-only")
        src = Path(m["source"])
        if not src.is_absolute():
            src = (ROOT / src).resolve()
        self.assertEqual(src, ENTRY.resolve(), "entrypoint source mismatch")
        cmd = svc.get("command", [])
        if isinstance(cmd, str):
            cmd = cmd.split()
        self.assertIn("--model", cmd, "missing --model")
        self.assertIn("--metrics", cmd, "missing --metrics")

    def test_all_modes(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                cfg = compose_config(base_files() + [overlay(mode)])
                self._check(cfg)

    def test_user_entrypoint_override_wins(self):
        override = {
            "services": {
                "llama-server": {
                    "entrypoint": ["/bin/true"],
                }
            }
        }
        with tempfile.NamedTemporaryFile(
                "w", suffix=".yml", encoding="utf-8", delete=False) as f:
            json.dump(override, f)
            tmp = Path(f.name)
        try:
            cfg = compose_config(base_files() + [overlay("cpu"), tmp])
            svc = cfg["services"]["llama-server"]
            self.assertEqual(svc.get("entrypoint"), ["/bin/true"],
                             "user entrypoint override lost")
        finally:
            tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
