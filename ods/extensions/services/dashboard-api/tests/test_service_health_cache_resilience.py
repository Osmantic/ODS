"""Unit test suite for service health status TTL cache resilience."""

import time
import unittest


def get_cached_service_health(cache: dict[str, tuple[dict, float]], service_id: str, ttl_seconds: float = 10.0) -> dict | None:
    if not isinstance(cache, dict) or not service_id:
        return None
    entry = cache.get(service_id)
    if not entry:
        return None
    data, ts = entry
    if (time.time() - ts) > ttl_seconds:
        return None
    return data


class TestServiceHealthCacheResilience(unittest.TestCase):
    def test_get_cached_service_health_valid(self):
        cache = {"api": ({"status": "ok"}, time.time())}
        res = get_cached_service_health(cache, "api")
        self.assertIsNotNone(res)
        self.assertEqual(res["status"], "ok")

    def test_get_cached_service_health_expired(self):
        cache = {"api": ({"status": "ok"}, time.time() - 20.0)}
        res = get_cached_service_health(cache, "api", ttl_seconds=10.0)
        self.assertIsNone(res)


if __name__ == "__main__":
    unittest.main()
