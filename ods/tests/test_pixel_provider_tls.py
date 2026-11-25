"""Pixel provider probes speak only verified TLS 1.2 or newer.

The connection and health probes run on the host, whose Python can be 3.9,
where the default client context still allows TLS 1.0 and 1.1.
"""
from pathlib import Path
import ssl
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
from pixel_provider import connection_transport, health


class ProviderTLSContextTests(unittest.TestCase):
    def test_context_refuses_tls_before_1_2_and_verifies_the_server(self):
        context = connection_transport.tls_context()
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_both_probes_wrap_https_with_that_context(self):
        root = Path(__file__).resolve().parents[1] / "bin" / "pixel_provider"
        for module in (connection_transport, health):
            source = (root / Path(module.__file__).name).read_text(encoding="utf-8")
            self.assertIn("tls_context().wrap_socket(sock, server_hostname=parts.hostname)", source)
            self.assertNotIn("ssl.create_default_context().wrap_socket", source)


if __name__ == "__main__":
    unittest.main()
