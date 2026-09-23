"""Exercise the real retired-name guard in disposable Git indexes."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from install_doc_history import FINGERPRINT_FILE, LEDGER_FILE, historical_line_masks


HERE = Path(__file__).resolve().parent
GATE = (HERE / "test-install-docs.sh").read_text(encoding="utf-8")
# Run exactly the gate's Python program; fixtures need no product install/docs.
GUARD = GATE.split('python3 - "$REPO_ROOT" <<\'PY\'\n', 1)[1].split("\nPY\n", 1)[0]
OLD_ROOT = "dream" + "-server"
SHA = "0123456789abcdef" * 2 + "01234567"
SOURCE = OLD_ROOT + "/tests/fixture.py"
RULE = "generic-api-key"
LINE = "19"


@unittest.skipUnless(shutil.which("git"), "The guard enumerates tracked files with Git")
class HistoricalScannerReferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ods-doc-history-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # The caller can select a Windows-created worktree from WSL with
        # GIT_DIR/GIT_WORK_TREE. Never let those settings redirect fixture Git.
        self.git_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.git_env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True,
                       capture_output=True, env=self.git_env)
        self.write("ods/tests/install_doc_history.py", (HERE / "install_doc_history.py").read_text())
        self.pair()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def pair(self, *, sha=SHA, source=SOURCE, rule=RULE, line=LINE, reason="Reviewed fixture"):
        self.fingerprint = f"{sha}:{source}:{rule}:{line}"
        self.row = f"| `{sha}` | `{source}:{line}` | `{rule}` | {reason} |"
        self.write(FINGERPRINT_FILE, self.fingerprint + "\n")
        self.write(LEDGER_FILE, self.row + "\n")

    def guard(self, expected_success):
        subprocess.run(["git", "-C", str(self.root), "add", "--all"], check=True,
                       capture_output=True, env=self.git_env)
        result = subprocess.run(
            [sys.executable, "-", str(self.root)], input=GUARD, text=True,
            capture_output=True, timeout=20, env=self.git_env,
        )
        self.assertEqual(result.returncode == 0, expected_success, result.stdout + result.stderr)
        if not expected_success:
            self.assertIn("Retired product, organization, or binary asset references remain", result.stdout)

    def test_exact_unique_pair_masks_only_two_path_spans(self):
        masks = historical_line_masks(self.root, OLD_ROOT)
        self.assertEqual(set(masks), {(FINGERPRINT_FILE, 1), (LEDGER_FILE, 1)})
        self.assertTrue(all(OLD_ROOT not in line for line in masks.values()))
        self.assertIn("Reviewed fixture", masks[(LEDGER_FILE, 1)])
        self.guard(True)

    def test_fingerprint_without_ledger_is_rejected(self):
        self.write(LEDGER_FILE, "No adjudication.\n")
        self.guard(False)

    def test_ledger_without_fingerprint_is_rejected(self):
        self.write(FINGERPRINT_FILE, "# No exception.\n")
        self.guard(False)

    def test_missing_partner_file_cannot_create_a_mask(self):
        (self.root / LEDGER_FILE).unlink()
        self.assertEqual(historical_line_masks(self.root, OLD_ROOT), {})
        self.guard(False)

    def test_crlf_evidence_preserves_record_line_numbers(self):
        (self.root / FINGERPRINT_FILE).write_bytes(("# history\r\n" + self.fingerprint + "\r\n").encode())
        (self.root / LEDGER_FILE).write_bytes(("# review\r\n\r\n" + self.row + "\r\n").encode())
        self.assertEqual(set(historical_line_masks(self.root, OLD_ROOT)),
                         {(FINGERPRINT_FILE, 2), (LEDGER_FILE, 3)})
        self.guard(True)

    def test_each_identity_field_must_match_exactly(self):
        variants = ["f" * 40 + self.fingerprint[40:],
                    self.fingerprint.replace("fixture.py", "other.py"),
                    self.fingerprint.replace(RULE, "jwt"),
                    self.fingerprint[:-2] + "20"]
        for fingerprint in variants:
            with self.subTest(fingerprint=fingerprint):
                self.write(FINGERPRINT_FILE, fingerprint + "\n")
                self.assertEqual(historical_line_masks(self.root, OLD_ROOT), {})
                self.guard(False)

    def test_malformed_hashes_lines_and_paths_are_rejected_even_when_paired(self):
        variants = [{"sha": "a" * 39}, {"sha": "a" * 41}, {"sha": "g" * 40},
                    {"sha": "A" * 40}]
        variants += [{"line": line} for line in ("0", "-1", "+1", "1.0", "019", "\u0661")]
        variants += [{"source": source} for source in (
            "/" + SOURCE, "C:/" + SOURCE, SOURCE.replace("/", "\\"),
            OLD_ROOT + "/../file.py", OLD_ROOT + "/./file.py", OLD_ROOT + "//file.py",
        )]
        for fields in variants:
            with self.subTest(fields=fields):
                self.pair(**fields)
                self.assertEqual(historical_line_masks(self.root, OLD_ROOT), {})
                self.guard(False)

    def test_comments_prefixes_suffixes_and_extra_cells_are_not_records(self):
        for text in ("# " + self.fingerprint, self.fingerprint + " # reviewed", " " + self.fingerprint):
            with self.subTest(text=text):
                self.write(FINGERPRINT_FILE, text + "\n")
                self.guard(False)
        self.write(FINGERPRINT_FILE, self.fingerprint + "\n")
        for text in (self.row + " extra |", "prefix " + self.row, self.row.replace("Reviewed fixture", "")):
            with self.subTest(text=text):
                self.write(LEDGER_FILE, text + "\n")
                self.guard(False)

    def test_duplicate_records_are_not_exempted(self):
        for filename, content in ((FINGERPRINT_FILE, self.fingerprint), (LEDGER_FILE, self.row)):
            with self.subTest(filename=filename):
                self.pair()
                self.write(filename, (content + "\n") * 2)
                self.guard(False)

    def test_live_instructions_in_either_evidence_file_still_fail(self):
        for filename, content in ((FINGERPRINT_FILE, self.fingerprint), (LEDGER_FILE, self.row)):
            with self.subTest(filename=filename):
                self.pair()
                self.write(filename, content + "\nInstall from https://example.test/" + OLD_ROOT + "\n")
                self.guard(False)

    def test_retired_reference_in_ledger_reason_still_fails(self):
        self.pair(reason="Install https://example.test/" + OLD_ROOT)
        self.assertEqual(len(historical_line_masks(self.root, OLD_ROOT)), 2)
        self.guard(False)

    def test_retired_reference_in_rule_still_fails(self):
        self.pair(rule=OLD_ROOT)
        self.assertEqual(len(historical_line_masks(self.root, OLD_ROOT)), 2)
        self.guard(False)

    def test_copies_elsewhere_are_not_exempted(self):
        for filename, text in (("README.md", self.fingerprint),
                               ("other/.gitleaksignore", self.fingerprint),
                               (LEDGER_FILE + ".bak", self.row)):
            with self.subTest(filename=filename):
                self.write(filename, text + "\n")
                self.guard(False)
                (self.root / filename).unlink()

    def test_live_path_names_still_fail(self):
        self.write(OLD_ROOT + "/README.md", "current install guide\n")
        self.guard(False)

    def test_existing_vendor_exception_remains_limited_to_fleet(self):
        self.write("ods/vendor/pixel/example.md", "dream" + "fleet\n")
        self.guard(True)
        self.write("ods/vendor/pixel/example.md", OLD_ROOT + "\n")
        self.guard(False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
