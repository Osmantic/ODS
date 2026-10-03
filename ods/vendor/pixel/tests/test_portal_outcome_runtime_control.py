import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "pixel_test_portal_outcome_runtime_control", ROOT / "scripts/portal_outcome_runtime_control.py",
)
control = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(control)


def completed(stdout: bytes, returncode: int = 0, stderr: bytes = b""):
    return subprocess.CompletedProcess(["docker"], returncode, stdout=stdout, stderr=stderr)


class RuntimeControlTests(unittest.TestCase):
    def test_cold_control_proves_zero_requests_without_inference(self):
        metrics = b'# TYPE vllm:request_success_total counter\nvllm:request_success_total{finished_reason="stop"} 0\n'
        with mock.patch.object(control.subprocess, "run", side_effect=[completed(metrics), completed(metrics)]) as run:
            receipt = control.execute(
                docker_path="docker", container="pixel-outcome-model-aaaaaaaaaaaa",
                run_id="outcomerun-1786550400000-aaaaaaaaaaaa", condition="cold-first-request",
            )
        self.assertEqual(run.call_count, 2)
        self.assertEqual(receipt["requestsBefore"], 0)
        self.assertEqual(receipt["requestsAfter"], 0)
        self.assertEqual(receipt["warmupModelRequests"], 0)
        self.assertIsNone(receipt["warmupRequestSha256"])
        self.assertFalse(any(receipt["authority"].values()))

    def test_warm_control_runs_one_fixed_probe_and_retains_only_hashes_and_usage(self):
        before = b'vllm:request_success_total{finished_reason="stop"} 0\n'
        response = json.dumps({
            "model": "DeepSeek-V4-Flash-0731", "choices": [{"message": {"content": "READY"}}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 1},
        }, separators=(",", ":")).encode()
        after = b'vllm:request_success_total{finished_reason="stop"} 1\n'
        with mock.patch.object(control.subprocess, "run", side_effect=[completed(before), completed(response), completed(after)]) as run:
            receipt = control.execute(
                docker_path="docker", container="pixel-outcome-model-bbbbbbbbbbbb",
                run_id="outcomerun-1786550400001-bbbbbbbbbbbb", condition="warm-neutral-probe",
            )
        self.assertEqual(run.call_count, 3)
        self.assertEqual(receipt["warmupRequestSha256"], control.WARMUP_REQUEST_SHA256)
        self.assertEqual(receipt["warmupResponseSha256"], control.evaluation.sha256(response))
        self.assertEqual(receipt["warmupModelRequests"], 1)
        self.assertEqual(receipt["warmupInputTokens"], 9)
        self.assertEqual(receipt["warmupOutputTokens"], 1)
        self.assertNotIn("READY", json.dumps(receipt))
        self.assertFalse(receipt["measuredUsageIncludesWarmup"])

    def test_control_fails_closed_on_prior_state_or_wrong_model(self):
        used = b'vllm:request_success_total{finished_reason="stop"} 1\n'
        with mock.patch.object(control.subprocess, "run", return_value=completed(used)):
            with self.assertRaisesRegex(control.evaluation.OutcomeError, "not a fresh zero-request runtime"):
                control.execute(
                    docker_path="docker", container="pixel-outcome-model-cccccccccccc",
                    run_id="outcomerun-1786550400002-cccccccccccc", condition="cold-first-request",
                )
        before = b'vllm:request_success_total{finished_reason="stop"} 0\n'
        response = b'{"model":"substitute","choices":[{}],"usage":{"prompt_tokens":1,"completion_tokens":1}}'
        with mock.patch.object(control.subprocess, "run", side_effect=[completed(before), completed(response)]):
            with self.assertRaisesRegex(control.evaluation.OutcomeError, "wrong model"):
                control.execute(
                    docker_path="docker", container="pixel-outcome-model-dddddddddddd",
                    run_id="outcomerun-1786550400003-dddddddddddd", condition="warm-neutral-probe",
                )

    def test_control_rejects_unknown_condition_and_malformed_counter(self):
        with self.assertRaisesRegex(control.evaluation.OutcomeError, "identity or condition"):
            control.execute(
                docker_path="docker", container="pixel-outcome-model-eeeeeeeeeeee",
                run_id="outcomerun-1786550400004-eeeeeeeeeeee", condition="implicit-warm",
            )
        malformed = b'vllm:request_success_total{finished_reason="stop"} not-a-number\n'
        with mock.patch.object(control.subprocess, "run", return_value=completed(malformed)):
            with self.assertRaisesRegex(control.evaluation.OutcomeError, "unavailable or malformed"):
                control.execute(
                    docker_path="docker", container="pixel-outcome-model-ffffffffffff",
                    run_id="outcomerun-1786550400005-ffffffffffff", condition="cold-first-request",
                )


if __name__ == "__main__":
    unittest.main()
