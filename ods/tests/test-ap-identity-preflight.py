#!/usr/bin/env python3
"""The real AP up command rejects invalid identity before network mutation."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ap-mode.sh"


class IdentityPreflight(unittest.TestCase):
    def invoke(self, ssid, password):
        with tempfile.TemporaryDirectory(prefix="ods-ap-identity-") as directory:
            root = Path(directory)
            bindir = root / "bin"
            bindir.mkdir()
            # None of these commands reaches the machine's real network tools.
            for name in ("id", "uname", "hostapd", "dnsmasq", "iptables", "ip", "nmcli", "iw"):
                stub = bindir / name
                stub.write_text("""#!/bin/bash
case "${0##*/}" in
  id) printf '0\\n';;
  uname) printf 'Linux\\n';;
  iw) printf 'Supported interface modes\\n * AP\\n';;
  *) printf '%s\\n' "${0##*/}" >> "$ODS_TEST_MUTATIONS"; exit 93;;
esac
""")
                stub.chmod(0o755)
            mutations = root / "mutations"
            env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
                   "ODS_AP_CONF_DIR": str(root / "conf"), "ODS_AP_RUN_DIR": str(root / "run"),
                   "ODS_AP_SSID": ssid, "ODS_AP_PASSWORD": password,
                   "ODS_TEST_MUTATIONS": str(mutations), "LC_ALL": "C.UTF-8"}
            result = subprocess.run(["bash", str(SCRIPT), "up"], env=env, capture_output=True,
                                    text=True, timeout=5, check=False)
            return result, mutations.exists(), (root / "run").exists()

    def test_invalid_values_do_not_release_interface_or_create_runtime(self):
        cases = [("x" * 33, "valid-pass"), ("界" * 11, "valid-pass"),
                 ("safe\nwpa=0", "valid-pass"), ("safe\rname", "valid-pass"),
                 ("safe", "x" * 64), ("safe", "界" * 22),
                 ("safe", "valid-pass\nwpa=0"), ("safe", "valid-pass\r")]
        for ssid, password in cases:
            with self.subTest(ssid_bytes=len(ssid.encode()), password_bytes=len(password.encode())):
                result, mutated, created = self.invoke(ssid, password)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertFalse(mutated, result.stderr)
                self.assertFalse(created, result.stderr)
                self.assertNotIn(password, result.stderr)

    def test_valid_byte_boundaries_reach_only_the_stubbed_network_boundary(self):
        for ssid, password in [("x" * 32, "p" * 63), ("界" * 10, "p" * 8),
                               ("safe", "界" * 21), ("safe", "")]:
            with self.subTest(ssid_bytes=len(ssid.encode()), password_bytes=len(password.encode())):
                _result, mutated, created = self.invoke(ssid, password)
                self.assertTrue(mutated)
                self.assertTrue(created)


if __name__ == "__main__":
    unittest.main()
