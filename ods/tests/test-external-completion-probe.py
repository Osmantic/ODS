#!/usr/bin/env python3
"""Exercise the actual installer probe without contacting a model provider."""

import io
import json
from pathlib import Path
import sys
import unittest
import urllib.error
from unittest.mock import patch


PHASE = Path(__file__).resolve().parents[1] / "installers/phases/12-health.sh"
SOURCE = PHASE.read_text().split('exec -i "$dashboard_container" python -c \'', 1)[1]
PROBE = compile(SOURCE.split('\' "$container_url" "$model" "ODS/', 1)[0], str(PHASE), "exec")


class CompletionProbeTests(unittest.TestCase):
    def run_probe(self, body, key=""):
        response = io.StringIO(json.dumps(body))
        with (
            patch.object(sys, "argv", ["probe", "http://provider:8080", "any-model", "ODS/9.9.9"]),
            patch("urllib.request.urlopen", return_value=response) as request,
            patch("sys.stdin", io.StringIO(key)),
            patch("sys.stdout", new_callable=io.StringIO) as output,
        ):
            exec(PROBE, {})
            payload = json.loads(request.call_args.args[0].data)
            self.assertEqual(payload["model"], "any-model")
            self.assertEqual(payload["max_tokens"], 1)
            self.assertEqual(
                request.call_args.args[0].get_header("Authorization"),
                "Bearer " + key if key else None,
            )
            # Some API front ends refuse Python's default User-Agent.
            self.assertEqual(request.call_args.args[0].get_header("User-agent"), "ODS/9.9.9")
            return output.getvalue()

    @staticmethod
    def response(message, finish="length"):
        return {"choices": [{"message": {"role": "assistant", **message}, "finish_reason": finish}]}

    def test_assistant_output(self):
        self.assertIn("assistant token", self.run_probe(self.response({"content": "OK"}, "stop")))

    def test_authenticated_assistant_output(self):
        self.assertIn(
            "assistant token",
            self.run_probe(self.response({"content": "OK"}, "stop"), "test-secret-123"),
        )

    def test_reasoning_budget_exhaustion_is_only_inference_evidence(self):
        for field in ("reasoning", "reasoning_content"):
            with self.subTest(field=field):
                result = self.run_probe(self.response({"content": None, field: "The"}))
                self.assertIn("one-token probe exhausted", result)

    def test_rejects_unusable_or_malformed_responses(self):
        cases = [
            None, [], {}, {"choices": []}, {"choices": [None]},
            {"error": {"message": "failed"}, "choices": []},
            self.response({"role": "user", "content": "OK"}),
            self.response({"content": ""}), self.response({"content": "  "}),
            self.response({"content": False}), self.response({"content": []}),
            self.response({"content": None, "reasoning": " "}),
            self.response({"content": None, "reasoning": 1}),
            self.response({"content": None, "reasoning": "unfinished"}, "stop"),
            self.response({"content": None, "reasoning_content": "unfinished"}, "error"),
        ]
        for body in cases:
            with self.subTest(body=body), self.assertRaises(SystemExit):
                self.run_probe(body)

    def test_http_error_names_the_status(self):
        refused = urllib.error.HTTPError(
            "http://provider:8080/v1/chat/completions", 403, "Forbidden", {},
            io.BytesIO(b"error code: 1010"))
        with (
            patch.object(sys, "argv", ["probe", "http://provider:8080", "any-model", "ODS/9.9.9"]),
            patch("urllib.request.urlopen", side_effect=refused),
            patch("sys.stdin", io.StringIO("")),
            self.assertRaises(SystemExit) as stopped,
        ):
            exec(PROBE, {})
        self.assertEqual(str(stopped.exception), "API answered HTTP 403: error code: 1010")


if __name__ == "__main__":
    unittest.main()
