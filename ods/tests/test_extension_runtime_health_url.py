"""Extension runtime check must normalize health endpoint paths lacking leading slash."""

import unittest


def normalize_health_url(port: int, health: str) -> str:
    if not health.startswith("/"):
        health = f"/{health}"
    return f"http://127.0.0.1:{port}{health}"


class ExtensionHealthUrlTests(unittest.TestCase):
    def test_relative_health_path_is_prefixed(self):
        cases = [
            ("healthz", "http://127.0.0.1:8080/healthz"),
            ("/healthz", "http://127.0.0.1:8080/healthz"),
            ("api/health", "http://127.0.0.1:8080/api/health"),
            ("/api/v1/live", "http://127.0.0.1:8080/api/v1/live"),
        ]
        for path, expected in cases:
            with self.subTest(path=path):
                self.assertEqual(normalize_health_url(8080, path), expected)

    def test_baseline_concatenation_defect(self):
        # Demonstrates defect where missing slash corrupts the port number
        corrupted = f"http://127.0.0.1:{8080}{'healthz'}"
        self.assertEqual(corrupted, "http://127.0.0.1:8080healthz")


if __name__ == "__main__":
    unittest.main()
