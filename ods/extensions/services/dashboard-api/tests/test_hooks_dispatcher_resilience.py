"""Unit test suite for event hooks dispatcher resilience."""

import unittest


def dispatch_event_hook(event_name: str | None, payload: dict | None) -> bool:
    if not event_name or not isinstance(event_name, str):
        return False
    if payload is not None and not isinstance(payload, dict):
        return False
    return True


class TestHooksDispatcherResilience(unittest.TestCase):
    def test_dispatch_event_valid(self):
        self.assertTrue(dispatch_event_hook("on_model_start", {"model": "llama3"}))

    def test_dispatch_event_invalid_name(self):
        self.assertFalse(dispatch_event_hook("", {}))
        self.assertFalse(dispatch_event_hook(None, {}))


if __name__ == "__main__":
    unittest.main()
