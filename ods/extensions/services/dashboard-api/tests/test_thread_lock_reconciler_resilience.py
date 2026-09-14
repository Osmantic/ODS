"""Unit test suite for thread lock acquisition timeout bounds resilience."""

import threading
import unittest


def acquire_bounded_lock(lock: threading.Lock, timeout_sec: float = 2.0) -> bool:
    if not isinstance(lock, threading.Lock):
        return False
    acquired = lock.acquire(timeout=max(0.1, timeout_sec))
    return acquired


class TestThreadLockReconcilerResilience(unittest.TestCase):
    def test_acquire_lock_success(self):
        lock = threading.Lock()
        acquired = acquire_bounded_lock(lock, timeout_sec=0.5)
        self.assertTrue(acquired)
        lock.release()

    def test_acquire_lock_invalid(self):
        self.assertFalse(acquire_bounded_lock(None))


if __name__ == "__main__":
    unittest.main()
