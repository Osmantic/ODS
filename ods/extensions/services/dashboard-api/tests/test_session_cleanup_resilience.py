"""Unit test suite for session store TTL cleanup resilience."""

import time
import unittest


def sweep_expired_sessions(sessions: dict[str, float], ttl_seconds: float = 300.0) -> int:
    if not isinstance(sessions, dict):
        return 0
    now = time.time()
    expired_keys = [k for k, ts in list(sessions.items()) if (now - ts) > ttl_seconds]
    for k in expired_keys:
        del sessions[k]
    return len(expired_keys)


class TestSessionCleanupResilience(unittest.TestCase):
    def test_sweep_expired_sessions_cleans_old_keys(self):
        sessions = {"active": time.time(), "stale": time.time() - 400.0}
        removed = sweep_expired_sessions(sessions, ttl_seconds=300.0)
        self.assertEqual(removed, 1)
        self.assertIn("active", sessions)
        self.assertNotIn("stale", sessions)

    def test_sweep_expired_sessions_invalid_dict(self):
        self.assertEqual(sweep_expired_sessions(None), 0)


if __name__ == "__main__":
    unittest.main()
