"""Unit test suite for MIME content-type resolution resilience."""

import mimetypes
import unittest


def resolve_file_mime_type(filename: str | None, default_mime: str = "application/octet-stream") -> str:
    if not filename or not isinstance(filename, str):
        return default_mime
    mime, _ = mimetypes.guess_type(filename.strip())
    return mime if mime else default_mime


class TestMIMETypeResolverResilience(unittest.TestCase):
    def test_resolve_mime_type_valid(self):
        self.assertEqual(resolve_file_mime_type("document.json"), "application/json")
        self.assertTrue(resolve_file_mime_type("archive.tar.gz") in ("application/x-tar", "application/gzip"))

    def test_resolve_mime_type_unknown(self):
        self.assertEqual(resolve_file_mime_type("unknown_file.custom_ext"), "application/octet-stream")

    def test_resolve_mime_type_none(self):
        self.assertEqual(resolve_file_mime_type(None), "application/octet-stream")


if __name__ == "__main__":
    unittest.main()
