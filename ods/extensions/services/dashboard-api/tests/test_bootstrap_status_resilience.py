"""Unit test suite for service bootstrap status probe resilience."""

import unittest


def build_bootstrap_status(service_id: str, is_ready: bool) -> dict[str, object]:
    if not isinstance(service_id, str) or not service_id.strip():
        return {"id": "unknown", "ready": False, "status": "uninitialized"}
    return {
        "id": service_id.strip(),
        "ready": bool(is_ready),
        "status": "active" if is_ready else "bootstrapping",
    }


class TestBootstrapStatusResilience(unittest.TestCase):
    def test_build_bootstrap_status_ready(self):
        st = build_bootstrap_status("dashboard-api", True)
        self.assertEqual(st["id"], "dashboard-api")
        self.assertTrue(st["ready"])
        self.assertEqual(st["status"], "active")

    def test_build_bootstrap_status_invalid_id(self):
        st = build_bootstrap_status("", False)
        self.assertEqual(st["id"], "unknown")
        self.assertFalse(st["ready"])


if __name__ == "__main__":
    unittest.main()
