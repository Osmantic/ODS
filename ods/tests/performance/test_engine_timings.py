import json
import unittest

from probe import StreamResult, _process_sse_event, _parse_engine_timings


def _run_events(events):
    result = StreamResult()
    for ev in events:
        _process_sse_event(ev, result, 0.0)
    return result


class EngineTimingsTests(unittest.TestCase):
    def test_parse_allowlisted_fields_only(self):
        got = _parse_engine_timings({
            "prompt_n": 5, "cache_n": 2, "predicted_n": 7,
            "prompt_ms": 12.5, "predicted_ms": 30,
            "unrelated": 99, "ttft_ms": 1.0,
        })
        self.assertEqual(got, {
            "prompt_n": 5, "cache_n": 2, "predicted_n": 7,
            "prompt_ms": 12.5, "predicted_ms": 30,
        })

    def test_parse_rejects_bool_and_infinity(self):
        got = _parse_engine_timings({
            "prompt_n": True, "cache_n": 1,
            "predicted_n": 2, "prompt_ms": float("inf"),
            "predicted_ms": 3.0,
        })
        self.assertEqual(got, {"cache_n": 1, "predicted_n": 2, "predicted_ms": 3.0})

    def test_parse_rejects_negative(self):
        got = _parse_engine_timings({"prompt_n": -1, "predicted_ms": -0.5})
        self.assertIsNone(got)

    def test_parse_completely_invalid_yields_none(self):
        self.assertIsNone(_parse_engine_timings({"foo": 1}))
        self.assertIsNone(_parse_engine_timings(None))
        self.assertIsNone(_parse_engine_timings("x"))

    def test_sse_captures_latest_cumulative(self):
        ev1 = ['data: ' + json.dumps({
            "choices": [{"index": 0, "delta": {"content": "a"}}],
            "timings": {"prompt_n": 3, "predicted_n": 1, "prompt_ms": 5.0, "predicted_ms": 2.0},
        })]
        ev2 = ['data: ' + json.dumps({
            "choices": [{"index": 0, "delta": {"content": "b"}}],
            "timings": {"prompt_n": 3, "predicted_n": 2, "prompt_ms": 5.0, "predicted_ms": 4.0},
        })]
        result = _run_events([ev1, ev2])
        self.assertEqual(result.engine_timings, {
            "prompt_n": 3, "predicted_n": 2, "prompt_ms": 5.0, "predicted_ms": 4.0,
        })

    def test_sse_nan_rejected_by_strict_parser(self):
        ev = ['data: {"choices":[{"index":0,"delta":{"content":"a"}}],"timings":{"prompt_ms":NaN}}']
        result = _run_events([ev])
        self.assertEqual(result.error, "sse_malformed_json")
        self.assertIsNone(result.engine_timings)

    def test_sse_invalid_timings_do_not_clear_previous(self):
        ev1 = ['data: ' + json.dumps({
            "choices": [{"index": 0, "delta": {"content": "a"}}],
            "timings": {"prompt_n": 3, "predicted_n": 1},
        })]
        ev2 = ['data: ' + json.dumps({
            "choices": [{"index": 0, "delta": {"content": "b"}}],
            "timings": {"prompt_n": True, "predicted_n": -1},
        })]
        result = _run_events([ev1, ev2])
        self.assertEqual(result.engine_timings, {"prompt_n": 3, "predicted_n": 1})

    def test_sse_unrelated_keys_omitted(self):
        ev = ['data: ' + json.dumps({
            "choices": [{"index": 0, "delta": {"content": "a"}}],
            "timings": {"prompt_n": 1, "ttft_ms": 9.9, "other": 5},
        })]
        result = _run_events([ev])
        self.assertEqual(result.engine_timings, {"prompt_n": 1})
