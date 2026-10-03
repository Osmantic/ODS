"""Prove a Windows Lemonade route without confusing WSL's localhost with it."""
import importlib.util
import copy
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wsl_proof_agent", SOURCE / "bin/ods-host-agent.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
MODEL = "extra.Qwen3.5-9B-Q4_K_M.gguf"
ENV = {
    "GPU_BACKEND": "cpu", "LEMONADE_EXTERNAL": "true", "LLM_BACKEND": "lemonade",
    "LEMONADE_BASE_URL": "http://localhost:13305",
    "LEMONADE_CONTAINER_BASE_URL": "http://host.docker.internal:13305",
    "LEMONADE_HOST_TRANSPORT": "model-router", "AMD_INFERENCE_LOCATION": "host",
    "LEMONADE_MODEL": MODEL, "CTX_SIZE": "65536",
}


def health(model=MODEL, context=65536):
    return json.dumps({
        "status": "ok", "model_loaded": model,
        "all_models_loaded": [{"model_name": model, "checkpoint": "C:\\models\\Qwen3.5-9B-Q4_K_M.gguf",
                               "recipe_options": {"ctx_size": context}, "device": "gpu"}],
    })


def completion(model=MODEL, content="READY"):
    return json.dumps({"model": model, "choices": [{"message": {"content": content}}]})


class WindowsLemonadeProof(unittest.TestCase):
    def prove(self, env=None):
        return agent._wait_for_model_readiness(
            env or ENV, model_id=MODEL, gguf_file=MODEL, llm_model_name=MODEL,
            lemonade_model_id=MODEL, attempts=1, initial_delay=0, interval=0,
            return_proof=True, allow_model_warmup=False,
        )

    def test_health_and_completion_use_same_owned_container_endpoint(self):
        calls = []

        def request(install_dir, api_base, path, **kwargs):
            calls.append((install_dir, api_base, path, kwargs))
            return health() if path == "/health" else completion()

        with patch.object(agent, "_container_lemonade_request", side_effect=request), \
                patch.object(agent.subprocess, "run", side_effect=AssertionError("Linux localhost must not be probed")):
            proof = self.prove()
        self.assertEqual(proof["identity"], MODEL)
        self.assertEqual(proof["contextLength"], 65536)
        self.assertTrue(proof["contextVerified"])
        self.assertEqual([call[2] for call in calls], ["/health", "/chat/completions"])
        self.assertTrue(all(call[1] == "http://host.docker.internal:13305/api/v1" for call in calls))
        self.assertEqual(calls[1][3]["payload"]["model"], MODEL)

    def test_wrong_identity_or_short_context_does_not_reach_completion(self):
        for observed in (health("other-model"), health(context=32768)):
            with self.subTest(observed=observed), \
                    patch.object(agent, "_container_lemonade_request", return_value=observed) as request:
                self.assertFalse(self.prove())
                self.assertEqual(request.call_count, 1)

    def test_completion_must_be_meaningful_and_match_the_loaded_model(self):
        for observed in (completion("other-model"), completion(content=""), completion(content="???")):
            with self.subTest(observed=observed), \
                    patch.object(agent, "_container_lemonade_request", side_effect=[health(), observed]):
                self.assertFalse(self.prove())

    def test_unavailable_or_unowned_container_never_produces_proof(self):
        with patch.object(agent, "_container_lemonade_request", side_effect=OSError("owned router not available")):
            self.assertFalse(self.prove())

    def test_router_startup_is_not_mistaken_for_a_permanent_failure(self):
        responses = [OSError("router is still starting"), health(), completion()]
        with patch.object(agent, "_container_lemonade_request", side_effect=responses) as request:
            proof = agent._wait_for_model_readiness(
                ENV, model_id=MODEL, gguf_file=MODEL, llm_model_name=MODEL,
                lemonade_model_id=MODEL, attempts=2, initial_delay=0, interval=0,
                return_proof=True, allow_model_warmup=False,
            )
        self.assertEqual(proof["identity"], MODEL)
        self.assertEqual(request.call_count, 3)

    def test_existing_direct_lemonade_flow_does_not_use_docker(self):
        env = {**ENV, "LEMONADE_HOST_TRANSPORT": "direct"}
        responses = [subprocess.CompletedProcess([], 0, health()), subprocess.CompletedProcess([], 0, completion())]
        with patch.object(agent, "_container_lemonade_request", side_effect=AssertionError("unexpected Docker probe")), \
                patch.object(agent.subprocess, "run", side_effect=responses):
            self.assertEqual(self.prove(env)["identity"], MODEL)

    def test_missing_transport_marker_keeps_direct_and_non_lemonade_routes(self):
        self.assertFalse(agent._lemonade_uses_container_transport({k: v for k, v in ENV.items() if k != "LEMONADE_HOST_TRANSPORT"}))
        for backend in ("nvidia", "apple", "cpu"):
            self.assertFalse(agent._lemonade_uses_container_transport({"GPU_BACKEND": backend}))

    def test_context_and_model_queries_share_the_configured_transport(self):
        with patch.object(agent, "_container_lemonade_request", return_value=health()), \
                patch.object(agent.subprocess, "run", side_effect=AssertionError("unexpected host probe")):
            self.assertEqual(agent._query_lemonade_runtime_context_length(
                ENV, expected_gguf_file=MODEL, expected_model_id=MODEL), 65536)

    def test_context_is_unknown_when_transport_is_unavailable(self):
        for error in (OSError("unavailable"), subprocess.TimeoutExpired("docker", 5)):
            with self.subTest(error=error), patch.object(agent, "_container_lemonade_request", side_effect=error):
                self.assertIsNone(agent._query_lemonade_runtime_context_length(
                    ENV, expected_gguf_file=MODEL, expected_model_id=MODEL))

    def test_invalid_transport_stops_readiness_without_retries(self):
        diagnosis = {}
        with patch.object(agent, "_container_lemonade_request", side_effect=ValueError("invalid route")) as request:
            result = agent._wait_for_model_readiness(
                ENV, model_id=MODEL, gguf_file=MODEL, llm_model_name=MODEL,
                lemonade_model_id=MODEL, attempts=3, initial_delay=0, interval=0,
                return_proof=True, diagnosis=diagnosis,
            )
        self.assertFalse(result)
        self.assertTrue(diagnosis["final"])
        self.assertEqual(request.call_count, 1)

    def test_changed_route_cancels_after_container_probe_without_completion(self):
        with patch.object(agent, "_container_lemonade_request", return_value=health()) as request:
            proof = agent._wait_for_model_readiness(
                ENV, model_id=MODEL, gguf_file=MODEL, llm_model_name=MODEL,
                lemonade_model_id=MODEL, attempts=1, initial_delay=0,
                return_proof=True, env_still_current=lambda: not request.called,
            )
        self.assertFalse(proof)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[2], "/health")

    def test_transport_change_invalidates_initial_route_proof(self):
        for key, value in (("LEMONADE_HOST_TRANSPORT", "direct"),
                           ("LEMONADE_CONTAINER_BASE_URL", "http://host.docker.internal:18080")):
            with self.subTest(key=key), patch.object(agent, "load_env", return_value={**ENV, key: value}):
                self.assertFalse(agent._initial_switchboard_route_env_matches(ENV))


class WindowsLemonadeTelemetry(unittest.TestCase):
    def setUp(self):
        self.health = {
            "status": "ok", "model_loaded": MODEL,
            "all_models_loaded": [{
                "model_name": MODEL, "checkpoint": "C:/private/models/model.gguf", "type": "llm",
                "recipe": "llamacpp", "recipe_options": {"ctx_size": 65536, "llamacpp_backend": "vulkan"},
            }],
        }
        self.catalog = {"data": [{"id": MODEL, "checkpoint": "C:/private/models/model.gguf",
                                   "downloaded": True, "recipe": "llamacpp"}]}
        self.stats = {"tokens_per_second": 24.577333938267465, "output_tokens": 163,
                      "input_tokens": 1196, "prompt_tokens": 18188, "time_to_first_token": 1.992152,
                      "prompt": "private prompt", "decode_token_times": [0.01]}

    def observe(self, stats=None, final_health=None):
        replies = [self.health, self.catalog, self.stats if stats is None else stats,
                   self.health if final_health is None else final_health]
        with patch.object(agent, "_lemonade_container_body", side_effect=[json.dumps(row) for row in replies]) as request:
            result = agent._read_external_lemonade_observation(ENV, include_stats=True)
        self.assertEqual([call.args[1] for call in request.call_args_list], ["/health", "/models", "/stats", "/health"])
        return result

    def test_real_stats_are_bound_to_the_stable_model_without_generating(self):
        result = self.observe()
        self.assertEqual(result["modelId"], MODEL)
        self.assertEqual(result["contextLength"], 65536)
        self.assertEqual(result["stats"], {key: self.stats[key] for key in (
            "tokens_per_second", "output_tokens", "input_tokens", "prompt_tokens", "time_to_first_token")})

    def test_stats_failure_is_unavailable_without_affecting_plain_observation(self):
        for error in (OSError("HTTP 404"), subprocess.TimeoutExpired("docker", 5)):
            with self.subTest(error=error), patch.object(agent, "_lemonade_container_body", side_effect=[
                    json.dumps(self.health), json.dumps(self.catalog), error]):
                with self.assertRaises(type(error)):
                    agent._read_external_lemonade_observation(ENV, include_stats=True)
        with patch.object(agent, "_lemonade_container_body", side_effect=[
                json.dumps(self.health), json.dumps(self.catalog), json.dumps(self.health)]) as request:
            self.assertNotIn("stats", agent._read_external_lemonade_observation(ENV))
            self.assertEqual([call.args[1] for call in request.call_args_list], ["/health", "/models", "/health"])

    def test_model_context_changes_and_other_loaded_runtimes_reject_stats(self):
        changed = copy.deepcopy(self.health)
        changed["all_models_loaded"][0]["recipe_options"]["ctx_size"] = 32768
        other = copy.deepcopy(self.health)
        other["all_models_loaded"].append({"type": "tts", "model_name": "audio"})
        for last in (changed, other, {**self.health, "model_loaded": "other-model"}):
            with self.subTest(last=last), self.assertRaises(ValueError):
                self.observe(final_health=last)

    def test_invalid_numbers_and_private_fields_never_escape(self):
        for invalid in (True, "24.5", -1, float("nan"), float("inf"), 10**400):
            with self.subTest(invalid=str(invalid)):
                result = self.observe(stats={**dict.fromkeys(self.stats, invalid), "prompt": "private prompt"})
                self.assertTrue(all(value is None for value in result["stats"].values()))
                self.assertNotIn("prompt", result["stats"])
        result = self.observe(stats={"tokens_per_second": 0, "output_tokens": 0, "input_tokens": 1.5})
        self.assertIsNone(result["stats"]["tokens_per_second"])
        self.assertEqual(result["stats"]["output_tokens"], 0)
        self.assertIsNone(result["stats"]["input_tokens"])

    def test_nonobject_and_error_payloads_do_not_create_measurements(self):
        for invalid in ([], "stats", {"error": "unsupported", "tokens_per_second": 24}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.observe(stats=invalid)

    def test_stats_do_not_expand_direct_external_runtime_access(self):
        with patch.object(agent, "_lemonade_container_body") as request, self.assertRaises(ValueError):
            agent._read_external_lemonade_observation({**ENV, "LEMONADE_HOST_TRANSPORT": "direct"}, include_stats=True)
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
