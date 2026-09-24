"""Exercise the real CI transport rewrite on isolated repository files."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/matrix-smoke.yml"
SHELL = os.environ.get("ODS_TEST_BASH") or shutil.which("bash")


@unittest.skipUnless(SHELL and os.name == "posix", "POSIX filesystem and bash are required")
class ZypperTransportTests(unittest.TestCase):
    def rewrite(self, text):
        source = WORKFLOW.read_text(encoding="utf-8")
        start = source.index("          configure_zypper_ci_repository_transport() {")
        end = source.index("          configure_zypper_ci_network() {", start)
        function = textwrap.dedent(source[start:end])
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "test.repo"
            other = Path(tmp) / "untouched.txt"
            repo.write_text(text)
            other.write_text(text)
            subprocess.run([SHELL, "-e", "-c", function + '\nconfigure_zypper_ci_repository_transport "$1"',
                            "fixture", tmp], check=True, capture_output=True, text=True, timeout=10)
            self.assertEqual(other.read_text(), text)
            return repo.read_text()

    def test_official_urls_preserve_path_and_signature_settings(self):
        source = "[repo-oss]\nbaseurl=http://download.opensuse.org/tumbleweed/repo/oss/\n gpgkey = http://download.opensuse.org/tumbleweed/repo/oss/repodata/repomd.xml.key\ngpgcheck=1\nrepo_gpgcheck=1\n"
        expected = source.replace("http://download.opensuse.org/", "https://download.opensuse.org/")
        self.assertEqual(self.rewrite(source), expected)
        self.assertEqual(self.rewrite(expected), expected)

    def test_third_party_lookalikes_credentials_and_non_url_fields_unchanged(self):
        source = "\n".join([
            "baseurl=http://custom.example/tumbleweed/repo/oss/",
            "baseurl=http://download.opensuse.org.evil.example/repo/",
            "baseurl=http://user@download.opensuse.org/repo/",
            "baseurl=http://download.opensuse.org:8080/repo/",
            "gpgkey=http://codecs.opensuse.org/openh264/key",
            "# baseurl=http://download.opensuse.org/comment/",
            "name=http://download.opensuse.org/description",
            "baseurl=https://download.opensuse.org/already-secure/",
            "baseurl=http://custom.example/path/http://download.opensuse.org/",
        ]) + "\n"
        self.assertEqual(self.rewrite(source), source)


if __name__ == "__main__":
    unittest.main()
