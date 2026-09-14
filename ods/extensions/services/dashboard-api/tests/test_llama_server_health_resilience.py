"""Unit test suite for llama-server health status probe resilience."""

import unittest


def parse_llama_health_response(data: dict) -> dict[str, str]:
    if not isinstance(data, dict):
        return {"status": "error", "reason": "invalid_payload"}
    status = data.get("status", "unknown")
    return {"status": str(status), "reason": "ok" if status == "ok" else "degraded"}


class TestLlamaServerHealthResilience(unittest.TestCase):
    def test_parse_llama_health_valid(self):
        res = parse_llama_health_response({"status": "ok"})
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["reason"], "ok")

    def test_parse_llama_health_invalid(self):
        res = parse_llama_health_response([])
        self.assertEqual(res["status"], "error")


if __name__ == "__main__":
    unittest.main()
