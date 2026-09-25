#!/usr/bin/env python3
import importlib.util
import hashlib
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path

source = Path(__file__).resolve().parents[1] / "deploy" / "web-courier" / "courier.py"
spec = importlib.util.spec_from_file_location("pixel_web_courier", source)
courier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(courier)


def public_resolver(*_args, **_kwargs):
    return [(2, 1, 6, "", ("93.184.216.34", 443))]


def private_resolver(*_args, **_kwargs):
    return [(2, 1, 6, "", ("127.0.0.1", 80))]


class CourierSecurityTests(unittest.TestCase):
    def make_symlink(self, link, target):
        try:
            link.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"host cannot create test symlinks: {exc}")

    def test_public_url_passes(self):
        self.assertIsNone(courier.check_url_policy("https://example.com/path", public_resolver))

    def test_private_and_non_web_targets_are_blocked(self):
        for url in (
            "file:///etc/passwd",
            "http://127.0.0.1/",
            "http://[::1]/",
            "http://169.254.169.254/latest/meta-data/",
            "http://100.64.0.1/",
            "http://192.0.0.8/",
            "http://localhost/",
            "https://service.internal/",
            "https://[2002:7f00:1::]/",
        ):
            self.assertIsNotNone(courier.check_url_policy(url, public_resolver), url)
        self.assertIsNotNone(courier.check_url_policy("https://rebind.example/", private_resolver))

    def test_only_configured_web_ports_are_allowed(self):
        self.assertIsNone(courier.check_url_policy("https://example.com/", public_resolver))
        self.assertIsNotNone(courier.check_url_policy("https://example.com:22/", public_resolver))

    def test_rate_limit_hostname_normalization_collapses_aliases(self):
        self.assertEqual(courier.normalized_hostname("EXAMPLE.com."), "example.com")
        self.assertEqual(courier.normalized_hostname("example。com"), "example.com")

    def test_url_credentials_are_blocked_and_logs_drop_sensitive_parts(self):
        self.assertIsNotNone(courier.check_url_policy("https://user:pass@example.com/", public_resolver))
        logged = courier.safe_url_for_log("https://user:pass@example.com/path?token=value#fragment")
        self.assertEqual(logged, "https://example.com/path")

    def test_courier_method_allowlist_is_read_only(self):
        self.assertEqual(courier.SAFE_HTTP_METHODS, {"GET", "HEAD", "OPTIONS"})
        self.assertNotIn("POST", courier.SAFE_HTTP_METHODS)

    def test_connect_uses_checked_numeric_address_without_second_dns_lookup(self):
        calls = []

        def connector(target, timeout):
            calls.append((target, timeout))
            return object()

        parts, marker = courier.connect_public_url(
            "https://example.com/path", resolver=public_resolver, connector=connector
        )
        self.assertEqual(parts.hostname, "example.com")
        self.assertIsNotNone(marker)
        self.assertEqual(calls, [(('93.184.216.34', 443), 15)])

    def test_connect_refuses_private_resolution_before_connector(self):
        calls = []
        with self.assertRaises(courier.RequestRejected):
            courier.connect_public_url(
                "https://rebind.example/",
                resolver=private_resolver,
                connector=lambda *args, **kwargs: calls.append((args, kwargs)),
            )
        self.assertEqual(calls, [])

    def test_proxy_refuses_private_connect_tunnel(self):
        proxy, _url = courier.start_egress_proxy()
        try:
            with socket.create_connection(proxy.server_address, timeout=5) as client:
                client.sendall(b"CONNECT 127.0.0.1:80 HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                response = client.recv(1024)
            self.assertIn(b"403 Refused", response)
        finally:
            proxy.shutdown()
            proxy.server_close()

    def test_request_reader_refuses_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_text(json.dumps({"url": "https://example.com"}), encoding="utf-8")
            name = "req-" + "a" * 32 + ".json"
            self.make_symlink(root / name, target)
            descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                with self.assertRaises((courier.RequestRejected, OSError)):
                    courier.read_request(descriptor, name)
            finally:
                os.close(descriptor)

    def test_workspace_directory_refuses_symlinked_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            self.make_symlink(root / "media", outside)
            with self.assertRaises(OSError):
                courier.open_workspace_directory(root, "media", "webq")
            self.assertFalse((outside / "webq").exists())

    def test_atomic_writer_replaces_symlink_without_touching_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "outside"
            target.write_text("guard", encoding="utf-8")
            output = root / "res-test.md"
            self.make_symlink(output, target)
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                courier.write_atomic(descriptor, output.name, b"safe")
            finally:
                os.close(descriptor)
            self.assertEqual(target.read_text(encoding="utf-8"), "guard")
            self.assertFalse(output.is_symlink())
            self.assertEqual(output.read_bytes(), b"safe")

    def research_request(self, url="https://docs.example.com/source"):
        return {
            "schemaVersion": 1,
            "transport": "web-courier",
            "retrievalId": "researchretrieval-1786366800003-abcdef123456",
            "jobId": "work-1786366800000-abcdef123456",
            "claimId": "workclaim-1786366800001-000000000001",
            "queryId": "researchquery-1786366800002-000000000002",
            "searchEvidenceSha256": "a" * 64,
            "planSha256": "b" * 64,
            "sourceId": "source-1234567890abcdef",
            "canonicalUrlSha256": hashlib.sha256(url.encode()).hexdigest(),
            "maxBytes": 1048576,
            "allowedDomains": ["example.com"],
            "deniedDomains": ["blocked.example"],
            "retention": "job-only",
            "boundary": courier.RESEARCH_REQUEST_BOUNDARY,
        }

    def test_research_receipt_request_is_hash_bound_and_domain_scoped(self):
        url = "https://docs.example.com/source"
        value = self.research_request(url)
        self.assertEqual(courier.validate_research_request(value, url, "text", 0), value)
        self.assertIsNone(courier.research_url_reason("https://cdn.example.com/asset", value))
        self.assertIsNotNone(courier.research_url_reason("https://outside.example/", value))
        self.assertIsNotNone(courier.research_url_reason("https://blocked.example/", value))
        self.assertIsNotNone(courier.research_url_reason("http://docs.example.com/downgrade", value))
        for mutate in (
            lambda item: item.update(canonicalUrlSha256="0" * 64),
            lambda item: item.update(maxBytes=1023),
            lambda item: item.update(allowedDomains=["example.com", "example.com"]),
            lambda item: item.update(deniedDomains=["example.com"]),
            lambda item: item.update(allowedDomains=["sub.example.com"], deniedDomains=["example.com"]),
            lambda item: item.update(extra=True),
        ):
            hostile = dict(value)
            hostile["allowedDomains"] = list(value["allowedDomains"])
            hostile["deniedDomains"] = list(value["deniedDomains"])
            mutate(hostile)
            with self.assertRaises(courier.RequestRejected):
                courier.validate_research_request(hostile, url, "text", 0)
        with self.assertRaises(courier.RequestRejected):
            courier.validate_research_request(value, url, "raw", 0)
        with self.assertRaises(courier.RequestRejected):
            courier.validate_research_request(value, url, "text", 1)

    def test_research_receipt_is_content_free_hash_bound_and_committed_after_content(self):
        if os.name == "nt":
            self.skipTest("directory-relative receipt writes require the supported POSIX host")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                request_id = "c" * 32
                content = b"untrusted public source"
                courier.write_atomic(descriptor, f"res-{request_id}.md", content)
                courier.write_research_receipt(
                    descriptor,
                    request_id,
                    self.research_request(),
                    "fetched",
                    content,
                    "https://docs.example.com/source",
                    2,
                    None,
                )
            finally:
                os.close(descriptor)
            receipt = json.loads((root / f"receipt-{request_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "fetched")
            self.assertEqual(receipt["responseName"], f"res-{request_id}.md")
            self.assertEqual(receipt["contentSha256"], hashlib.sha256(content).hexdigest())
            self.assertEqual(receipt["bytes"], len(content))
            self.assertTrue(receipt["dnsPinned"])
            self.assertTrue(receipt["safeMethodsOnly"])
            self.assertFalse(any(receipt["authority"].values()))
            self.assertNotIn(content.decode(), json.dumps(receipt))


if __name__ == "__main__":
    unittest.main()
