"""Run real installer helpers against private, disposable onboarding records."""

import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires POSIX host ownership, file locks, or Unix sockets; run under Linux/WSL")

import copy
import json
import os
from pathlib import Path
import pwd
import subprocess
import tempfile
import unittest


LIBRARY = Path(__file__).resolve().parents[1] / "installers/lib/pixel-host-install.sh"


class OnboardingUpgradeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.answers = self.home / "data/onboarding.json"
        self.owner = pwd.getpwuid(os.getuid()).pw_name
        self.env = dict(os.environ, MAX_CONTEXT="65536", LLAMA_REASONING="off",
                        ODS_MODEL_SWITCHBOARD="observe", LITELLM_PORT="4000",
                        LITELLM_KEY="disposable-test-key", INSTALL_DIR=str(self.home),
                        EXTERNAL_LLM_URL="http://127.0.0.1:18080",
                        EXTERNAL_LLM_MODEL="test-model")
        self.write()
        self.original = json.loads(self.answers.read_text())
        self.original["modelMaxTokens"] = 16384
        self.save(self.original)

    def invoke(self, function, *arguments, success=True, env=None):
        command = ('set -euo pipefail; source "$1"; shift; '
                   'ods_sudo_available() { return 1; }; '
                   'ai_bad() { printf "%s\\n" "$*" >&2; }; "$@"')
        result = subprocess.run(
            ["bash", "-c", command, "onboarding-test", str(LIBRARY), function,
             self.owner, str(self.home), *map(str, arguments)],
            env=env or self.env, text=True, capture_output=True, timeout=15)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def write(self, **kwargs):
        return self.invoke("_ods_pixel_write_onboarding", self.answers,
                           "/usr/bin/openclaw", "/opt/ods/pixel-ods", "a" * 64,
                           "parallel-free", "/opt/ods/parallel", "b" * 64, **kwargs)

    def save(self, value):
        self.answers.write_text(json.dumps(value))
        self.answers.chmod(0o600)

    def test_upgrade_preserves_budget_across_credential_rotation(self):
        self.write(env=dict(self.env, LITELLM_KEY="rotated-test-key"))
        value = json.loads(self.answers.read_text())
        self.assertEqual(value["modelMaxTokens"], 16384)
        self.assertEqual(value["modelContextWindow"], 65536)
        self.assertEqual(value["modelApiKey"], "rotated-test-key")
        self.assertEqual(value["gatewayExtensions"], self.original["gatewayExtensions"])

    def test_changed_model_context_or_reasoning_uses_factory_budget(self):
        for change in ({"EXTERNAL_LLM_MODEL": "other-model"},
                       {"MAX_CONTEXT": "32768"}, {"LLAMA_REASONING": "on"}):
            with self.subTest(change=change):
                self.save(self.original)
                self.write(env=dict(self.env, **change))
                self.assertEqual(json.loads(self.answers.read_text())["modelMaxTokens"], 8192)

    def test_explicit_setting_on_same_model_remains_effective(self):
        self.invoke("_ods_pixel_update_onboarding_model", self.answers,
                    "test-model", 65536, 1024, "false")
        self.write()
        value = json.loads(self.answers.read_text())
        self.assertEqual(value["modelMaxTokens"], 1024)
        self.assertEqual(value["gatewayExtensions"], self.original["gatewayExtensions"])

    def test_invalid_existing_budget_refused_without_overwrite(self):
        for field, invalid in (("modelMaxTokens", True), ("modelMaxTokens", 0),
                               ("modelMaxTokens", 65537), ("modelContextWindow", 4095),
                               ("modelReasoning", "false")):
            with self.subTest(field=field, value=invalid):
                self.save(dict(self.original, **{field: invalid}))
                before = self.answers.read_bytes()
                self.write(success=False)
                self.assertEqual(self.answers.read_bytes(), before)
        self.answers.write_text("{malformed")
        self.write(success=False)
        self.assertEqual(self.answers.read_text(), "{malformed")

    def test_unsafe_existing_record_refused_without_overwrite(self):
        before = self.answers.read_bytes()
        self.answers.chmod(0o640)
        self.write(success=False)
        self.assertEqual(self.answers.read_bytes(), before)
        self.answers.chmod(0o600)
        linked = self.home / "linked.json"
        os.link(self.answers, linked)
        self.write(success=False)
        self.assertEqual(linked.read_bytes(), before)
        linked.unlink()
        self.answers.rename(linked)
        self.answers.symlink_to(linked)
        self.write(success=False)
        self.assertEqual(linked.read_bytes(), before)

    def snapshot(self, **kwargs):
        return self.invoke("_ods_pixel_model_reconciliation_snapshot", self.answers, **kwargs)

    def prepare_snapshot(self):
        model = {"id": "ods/current", "name": "ODS Current (test-model)",
                 "contextWindow": 65536, "maxTokens": 16384, "reasoning": False}
        live = {"models": {"providers": {"ods-gateway": {
                    "api": "openai-completions", "apiKey": "disposable-test-key",
                    "baseUrl": "http://127.0.0.1:4000/v1", "models": [model]}}},
                "agents": {"list": [{"id": "pixel", "model": "ods-gateway/ods/current"}]}}
        for name, value in ((".openclaw/openclaw.json", live),
                            (".config/ods/pixel-managed.json", {"manager": "ods"}),
                            (".config/pixel-deployment/onboarding.json", self.original)):
            path = self.home / name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_text(json.dumps(value))
            path.chmod(0o600)

    def test_snapshot_and_update_preserve_additional_digest_bound_extensions(self):
        self.prepare_snapshot()
        value = copy.deepcopy(self.original)
        # Generic integration contract: no special case for the search provider.
        value["gatewayExtensions"].insert(0, {"id": "future-tool", "path": "/opt/tool", "sha256": "c" * 64})
        self.save(value)
        backup = Path(self.snapshot().stdout.strip())
        rollback = json.loads((backup / "rollback-onboarding.json").read_text())
        self.assertEqual(rollback["gatewayExtensions"], value["gatewayExtensions"])
        self.assertEqual(rollback["modelMaxTokens"], 16384)
        self.invoke("_ods_pixel_update_onboarding_model", self.answers, "next-model", 32768, 2048, "false")
        updated = json.loads(self.answers.read_text())
        self.assertEqual(updated["gatewayExtensions"], value["gatewayExtensions"])
        self.assertEqual(updated["modelName"], "ODS Current (next-model)")

    def test_invalid_extensions_rejected_by_snapshot_and_update(self):
        self.prepare_snapshot()
        base, extra = self.original["gatewayExtensions"]
        invalid_sets = [[], [extra], [base, base], [base, {"id": "parallel"}],
                        [base, None], [base, dict(extra, path="/")],
                        [base, dict(extra, path="/opt/../tool")],
                        [base, dict(extra, path="/opt/tool\n")],
                        [base, dict(extra, sha256="unverified")],
                        [base, dict(extra, id="INVALID")], [base] + [extra] * 32]
        for extensions in invalid_sets:
            with self.subTest(extensions=extensions):
                self.save(dict(self.original, gatewayExtensions=extensions))
                before = self.answers.read_bytes()
                self.snapshot(success=False)
                self.invoke("_ods_pixel_update_onboarding_model", self.answers,
                            "next-model", 65536, 2048, "false", success=False)
                self.assertEqual(self.answers.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
