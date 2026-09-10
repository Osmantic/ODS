#!/usr/bin/env python3
"""Exercise the actual installer probe without contacting a model provider."""

import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


PHASE = Path(__file__).resolve().parents[1] / "installers/phases/12-health.sh"
SOURCE = PHASE.read_text().split('exec "$dashboard_container" python -c \'', 1)[1]
PROBE = compile(SOURCE.split('\' "$container_url" "$model"', 1)[0], str(PHASE), "exec")


class CompletionProbeTests(unittest.TestCase):
    def run_probe(self, body):
        response = io.StringIO(json.dumps(body))
        with (
            patch.object(sys, "argv", ["probe", "http://provider:8080", "any-model"]),
            patch("urllib.request.urlopen", return_value=response) as request,
            patch("sys.stdout", new_callable=io.StringIO) as output,
        ):
            exec(PROBE, {})
            payload = json.loads(request.call_args.args[0].data)
            self.assertEqual(payload["model"], "any-model")
            self.assertEqual(payload["max_tokens"], 1)
            return output.getvalue()

    @staticmethod
    def response(message, finish="length"):
        return {"choices": [{"message": {"role": "assistant", **message}, "finish_reason": finish}]}

    def test_assistant_output(self):
        self.assertIn("assistant token", self.run_probe(self.response({"content": "OK"}, "stop")))

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


if __name__ == "__main__":
    unittest.main()
