"""Exercise installer review boundaries without network, installation or weights."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer_model_review", ROOT / "scripts/review-model-download.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)
from model_terms import hub_source_observation, project_terms  # noqa: E402


def fixture():
    revision = "a" * 40
    model = {"id": "fixture-q4", "name": "Fixture", "source_repo": "publisher/model",
             "source_revision": revision, "gguf_file": "fixture.gguf", "gguf_sha256": "b" * 64,
             "gguf_url": "https://huggingface.co/publisher/model/resolve/" + revision + "/fixture.gguf"}
    model["terms"] = hub_source_observation(model, license_id="other", gated=True, declared_bases=["publisher/base"])
    model["terms"]["commercial_use"] = "restricted"
    model["terms"]["conditions"] = [{"trigger": "Redistribution", "requirement": "Retain the original notices."}]
    model["terms"]["notice_documents"] = [{"repository": model["source_repo"], "path": "NOTICE", "sha256": "c" * 64,
                                           "url": "https://huggingface.co/publisher/model/blob/" + revision + "/NOTICE"}]
    return model


class Terminal(io.StringIO):
    def isatty(self):
        return True


class InstallerModelReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.model = fixture()
        self.catalog = self.root / "config/model-library.json"
        self.catalog.parent.mkdir()
        self.save_catalog()
        self.projection = project_terms(self.model)
        self.ack = {"termsDigest": self.projection["termsDigest"], "acknowledged": True, "upstreamAccepted": True}
        self.receipt = self.root / "data/model-download-review.json"
        for relative in ("scripts/review-model-download.py", "extensions/services/dashboard-api/model_terms.py",
                         "installers/lib/model-download-review.sh", "installers/windows/lib/model-download-review.ps1"):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        self.helper = self.root / "scripts/review-model-download.py"
        self.args = ["--catalog", str(self.catalog), "--file", self.model["gguf_file"],
                     "--url", self.model["gguf_url"], "--sha256", self.model["gguf_sha256"]]

    def save_catalog(self, models=None):
        self.catalog.write_text(json.dumps({"models": models or [self.model]}), encoding="utf-8")

    def save_receipt(self, ack=None, artifact=None):
        self.receipt.parent.mkdir(exist_ok=True)
        self.receipt.write_text(json.dumps({"schema_version": 1, "acknowledgements": [{
            "modelId": self.model["id"], "artifact": artifact or review.artifact_identity(self.model),
            "termsAcknowledgement": self.ack if ack is None else ack}]}), encoding="utf-8")

    def run_cli(self, *extra, stdin="", environment=None):
        return subprocess.run([sys.executable, str(self.helper), *self.args, *extra], input=stdin,
                              capture_output=True, text=True, timeout=10, env=environment)

    def test_show_json_discloses_terms_without_issuing_a_receipt(self):
        result = self.run_cli("--show-json", "--write-ack-file", str(self.receipt))
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertFalse(body["releaseReady"])
        self.assertEqual(body["artifact"], review.artifact_identity(self.model))
        self.assertEqual(body["terms"]["commercial_use"], "restricted")
        self.assertFalse(self.receipt.exists())

    def test_terminal_requires_default_no_and_separate_upstream_confirmation(self):
        for text in ("", "\n", "no\n", "yes please\n", "yes\n", "y\nno\n", "yes\nyes please\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                review.interactive_acknowledgement(self.projection, Terminal(text), io.StringIO())
        ack = review.interactive_acknowledgement(self.projection,
            Terminal("YES\ny\n"), io.StringIO())
        self.assertEqual(ack, self.ack)

    def test_terminal_without_observed_gating_never_implies_upstream_acceptance(self):
        projection = copy.deepcopy(self.projection)
        projection["terms"]["upstream_acceptance"] = "not_assessed"
        ack = review.interactive_acknowledgement(projection, Terminal("y\n"), io.StringIO())
        self.assertNotIn("upstreamAccepted", ack)

    def test_eof_piped_yes_and_global_yes_flags_do_not_authorize(self):
        env = {**os.environ, "AUTO_YES": "true", "ASSUME_YES": "true", "NON_INTERACTIVE": "true"}
        for text in ("", "yes\n", "yes\nyes\n"):
            with self.subTest(text=text):
                result = self.run_cli(stdin=text, environment=env)
                self.assertEqual(result.returncode, 1)
                self.assertIn("requires a terminal", result.stderr)
                self.assertFalse(self.receipt.exists())

    def test_unattended_exact_receipt_passes_and_discloses_restrictions(self):
        self.save_receipt()
        result = self.run_cli("--non-interactive", "--ack-file", str(self.receipt))
        self.assertEqual(result.returncode, 0, result.stderr)
        for message in ("License review pending", "Commercial use: restricted", "Retain the original notices.",
                        "Declared base (not reviewed): publisher/base", "Attribution/notice:"):
            self.assertIn(message, result.stderr)

    def test_unattended_missing_stale_false_and_upstream_missing_are_blocked(self):
        self.assertEqual(self.run_cli("--non-interactive").returncode, 1)
        for ack in ({}, {**self.ack, "acknowledged": "true"}, {**self.ack, "termsDigest": "0" * 64},
                    {**self.ack, "upstreamAccepted": False}):
            with self.subTest(ack=ack):
                self.save_receipt(ack)
                result = self.run_cli("--non-interactive", "--ack-file", str(self.receipt))
                self.assertEqual(result.returncode, 1, result.stderr)

    def test_receipt_cannot_cross_artifact_identity(self):
        for key in ("file", "url", "sha256"):
            with self.subTest(key=key):
                artifact = {**review.artifact_identity(self.model), key: "different"}
                self.save_receipt(artifact=artifact)
                self.assertEqual(self.run_cli("--ack-file", str(self.receipt)).returncode, 1)

    def test_missing_sha_mutable_url_wrong_id_and_ambiguous_catalog_fail_closed(self):
        for extra in (["--sha256", ""], ["--url", self.model["gguf_url"].replace("a" * 40, "main")], ["--model-id", "other"]):
            with self.subTest(extra=extra):
                self.assertEqual(self.run_cli(*extra, "--show-json").returncode, 1)
        self.save_catalog([self.model, {**self.model, "id": "duplicate"}])
        self.assertEqual(self.run_cli("--show-json").returncode, 1)

    def test_invalid_terms_and_split_files_cannot_be_approved(self):
        original = copy.deepcopy(self.model)
        for update in ({"terms": None}, {"gguf_parts": [{"file": "one"}, {"file": "two"}]}):
            with self.subTest(update=update):
                self.model = {**original, **update}
                self.save_catalog()
                self.assertEqual(self.run_cli("--show-json").returncode, 1)

    def test_catalog_change_while_prompting_does_not_issue_a_receipt(self):
        def reply(*_args):
            self.model["terms"]["note"] = "Changed after display"
            self.save_catalog()
            return self.ack
        with patch.object(review, "interactive_acknowledgement", reply), patch.object(sys, "stderr", io.StringIO()):
            code = review.main([*self.args, "--write-ack-file", str(self.receipt)])
        self.assertEqual(code, 1)
        self.assertFalse(self.receipt.exists())

    def test_explicit_review_receipt_can_be_reused_only_until_terms_change(self):
        with patch.object(review, "interactive_acknowledgement", return_value=self.ack), patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(review.main([*self.args, "--write-ack-file", str(self.receipt)]), 0)
        self.assertEqual(self.run_cli("--non-interactive", "--ack-file", str(self.receipt)).returncode, 0)
        self.model["terms"]["note"] = "New condition"
        self.save_catalog()
        result = self.run_cli("--non-interactive", "--ack-file", str(self.receipt))
        self.assertEqual(result.returncode, 1)
        self.assertIn("model_terms_changed", result.stderr)

    @unittest.skipIf(os.name == "nt", "POSIX pseudo-terminal coverage")
    def test_real_terminal_confirmation_and_eof(self):
        import pty
        for approve in (False, True):
            with self.subTest(approve=approve):
                master, slave = pty.openpty()
                try:
                    process = subprocess.Popen([sys.executable, str(self.helper), *self.args], stdin=slave,
                                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    answer = "yes\nyes\n" if approve else "\x04"
                    os.write(master, answer.encode())
                    stdout, stderr = process.communicate(timeout=10)
                    self.assertEqual(process.returncode, 0 if approve else 1, stdout + stderr)
                finally:
                    os.close(master)
                    os.close(slave)

    @unittest.skipIf(os.name == "nt", "POSIX installer/background boundary")
    def test_real_background_guard_never_contacts_remote_for_missing_or_stale_receipt(self):
        source = (ROOT / "scripts/bootstrap-upgrade.sh").read_text(encoding="utf-8")
        start = source.index('if [[ "$_dl_success" != "true" ]]; then\n    _review_library=')
        end = source.index('\nif [[ -f "$_part_path"', start)
        block = source[start:end]
        for mode in ("missing", "stale", "valid", "cached"):
            with self.subTest(mode=mode):
                marker = self.root / (mode + ".remote")
                if mode != "missing":
                    self.save_receipt({**self.ack, "termsDigest": "0" * 64} if mode == "stale" else self.ack)
                env = {**os.environ, "INSTALL_DIR": str(self.root), "FULL_GGUF_FILE": self.model["gguf_file"],
                       "FULL_GGUF_URL": self.model["gguf_url"], "FULL_GGUF_SHA256": self.model["gguf_sha256"],
                       "ODS_PYTHON_CMD": sys.executable, "TEST_REMOTE": str(marker)}
                script = ('fail() { printf "%s\\n" "$*" >&2; exit 1; }; log() { :; }; write_status() { :; }; '
                          'curl() { printf transfer > "$TEST_REMOTE"; }; get_remote_size() { curl "$1"; echo 1024; };\n'
                          '_dl_success=' + ("true" if mode == "cached" else "false") + '\n' + block)
                result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0 if mode in {"valid", "cached"} else 1, result.stderr)
                self.assertEqual(marker.exists(), mode == "valid")

    @unittest.skipUnless(os.name == "nt", "Native Windows installer boundary")
    def test_real_powershell_wrapper_requires_receipt_before_transfer(self):
        shells = list(dict.fromkeys(shell for shell in (shutil.which("pwsh"), shutil.which("powershell")) if shell))
        if not shells:
            self.skipTest("PowerShell unavailable")
        for shell, mode in ((shell, mode) for shell in shells for mode in ("missing", "stale", "valid")):
            with self.subTest(shell=shell, mode=mode):
                marker = self.root / (mode + ".transfer")
                if marker.exists():
                    marker.unlink()
                self.save_receipt({**self.ack, "termsDigest": "0" * 64} if mode == "stale" else self.ack)
                env = {**os.environ, "REVIEW_ROOT": str(self.root), "REVIEW_PYTHON": sys.executable,
                       "REVIEW_FILE": self.model["gguf_file"], "REVIEW_URL": self.model["gguf_url"],
                       "REVIEW_SHA": self.model["gguf_sha256"], "REVIEW_MARKER": str(marker)}
                env.pop("ODS_MODEL_TERMS_ACK_FILE", None)
                if mode != "missing":
                    env["ODS_MODEL_TERMS_ACK_FILE"] = str(self.receipt)
                script = '''$ErrorActionPreference = 'Stop'
function Get-ODSPythonDownloadCommand { return @{ FilePath = $env:REVIEW_PYTHON; PrefixArgs = @() } }
. (Join-Path $env:REVIEW_ROOT 'installers/windows/lib/model-download-review.ps1')
Confirm-ODSModelDownloadReview -Root $env:REVIEW_ROOT -File $env:REVIEW_FILE -Url $env:REVIEW_URL -Sha256 $env:REVIEW_SHA -Unattended
Set-Content -LiteralPath $env:REVIEW_MARKER -Value transfer
'''
                result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script], env=env,
                                        capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode == 0, mode == "valid", result.stdout + result.stderr)
                self.assertEqual(marker.exists(), mode == "valid")

    def test_static_tier_and_bootstrap_triples_match_current_catalog(self):
        import re
        catalog = json.loads((ROOT / "config/model-library.json").read_text(encoding="utf-8"))
        identities = {(model.get("gguf_file"), model.get("gguf_url"), model.get("gguf_sha256")) for model in catalog["models"]}
        for relative in ("installers/lib/tier-map.sh", "installers/lib/bootstrap-model.sh", "installers/macos/lib/tier-map.sh", "installers/windows/lib/tier-map.ps1"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            lines = text.splitlines()
            filename = None
            found = 0
            for index, line in enumerate(lines):
                match = re.search(r'(?:GGUF_FILE|GgufFile)\s*=\s*"([^"]*)"', line)
                if match:
                    filename = match[1]
                url = re.search(r'(?:GGUF_URL|GgufUrl)\s*=\s*"(https:[^"]+)"', line)
                if url:
                    digest = re.search(r'(?:GGUF_SHA256|GgufSha256)\s*=\s*"([^"]*)"', lines[index + 1])
                    with self.subTest(path=relative, line=index + 1):
                        self.assertIsNotNone(digest)
                        self.assertIn((filename, url[1], digest[1]), identities)
                    found += 1
            self.assertGreater(found, 0)


if __name__ == "__main__":
    unittest.main()
