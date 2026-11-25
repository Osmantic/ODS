"""Prove a Windows-owned llama-server route without confusing WSL's localhost with it."""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wsl_proof_agent", SOURCE / "bin/ods-host-agent.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
MODEL = "Qwen3.5-9B-Q4_K_M.gguf"
KEY = "5e" * 32
ORIGIN = "http://host.docker.internal:13305"
# A migrated Portal install: the stack runs in WSL, llama-server.exe is the
# owned Windows task, and every probe crosses the owned model-router.
ENV = {
    "GPU_BACKEND": "cpu", "LLM_BACKEND": "llama-server", "AMD_INFERENCE_RUNTIME": "llama-server",
    "AMD_INFERENCE_RUNTIME_MODE": "windows-portal-llama-server",
    "NATIVE_LLM_BASE_URL": "http://localhost:13305",
    "NATIVE_LLM_CONTAINER_BASE_URL": ORIGIN,
    "ODS_HOST_LLM_TRANSPORT": "model-router", "AMD_INFERENCE_LOCATION": "host",
    "GGUF_FILE": MODEL, "CTX_SIZE": "65536", "LLAMA_SERVER_API_KEY": KEY,
}


def models(model=MODEL):
    return json.dumps({"object": "list", "data": [{"id": model, "object": "model", "owned_by": "llamacpp"}]})


def props(context=65536, served=MODEL):
    return json.dumps({"model_path": "C:\\Users\\Test\\AppData\\Local\\ODS\\lemonade\\models\\" + served,
                       "default_generation_settings": {"n_ctx": context}})


def completion(model=MODEL, content="READY", reasoning=""):
    message = {"content": content}
    if reasoning:
        message["reasoning_content"] = reasoning
    return json.dumps({"model": model, "choices": [{"message": message}]})


def runtime(*, health='{"status":"ok"}', listed=None, settings=None, answer=None):
    replies = {"/health": health, "/v1/models": listed or models(), "/props": settings or props(),
               "/v1/chat/completions": answer or completion()}

    def request(install_dir, origin, path, **kwargs):
        return replies[path]

    return request


class WindowsRuntimeProof(unittest.TestCase):
    def setUp(self):
        # Only the WSL kernel check belongs to the fixture; the transport key
        # (and its one-release legacy name) is read by the real bridge.
        patcher = patch.object(agent._wsl_runtime, "candidate", side_effect=lambda env: agent._wsl_runtime.env_value(
            env, agent._wsl_runtime.TRANSPORT_KEY)[1] == "model-router")
        patcher.start()
        self.addCleanup(patcher.stop)

    def prove(self, env=None, **kwargs):
        return agent._wait_for_model_readiness(
            env or ENV, model_id=MODEL, gguf_file=MODEL, llm_model_name=MODEL,
            attempts=kwargs.pop("attempts", 1), initial_delay=0, interval=0,
            return_proof=True, **kwargs,
        )

    def test_every_proof_step_uses_the_owned_router_endpoint_and_key(self):
        calls = []
        serve = runtime()

        def request(install_dir, origin, path, **kwargs):
            calls.append((origin, path, kwargs))
            return serve(install_dir, origin, path, **kwargs)

        with patch.object(agent, "_router_transport_request", side_effect=request), \
                patch.object(agent.subprocess, "run", side_effect=AssertionError("Linux localhost must not be probed")):
            proof = self.prove()
        self.assertEqual(proof["identity"], MODEL)
        self.assertEqual(proof["contextLength"], 65536)
        self.assertTrue(proof["contextVerified"])
        self.assertEqual([call[1] for call in calls], ["/health", "/v1/models", "/props", "/v1/chat/completions"])
        self.assertTrue(all(call[0] == ORIGIN for call in calls))
        self.assertTrue(all(call[2]["api_key"] == KEY for call in calls))
        payload = calls[3][2]["payload"]
        self.assertEqual(payload["model"], MODEL)
        self.assertIs(payload["chat_template_kwargs"]["enable_thinking"], False)

    def test_loading_wrong_identity_short_context_or_other_file_never_reaches_completion(self):
        loading = '{"error":{"code":503,"message":"Loading model","type":"unavailable_error"}}'
        for case in (dict(health=loading), dict(listed=models("other-model.gguf")),
                     dict(settings=props(context=32768)), dict(settings=props(served="other-model.gguf"))):
            serve = runtime(**case)
            paths = []

            def request(install_dir, origin, path, **kwargs):
                paths.append(path)
                return serve(install_dir, origin, path, **kwargs)

            with self.subTest(case=case), patch.object(agent, "_router_transport_request", side_effect=request):
                self.assertFalse(self.prove())
                self.assertNotIn("/v1/chat/completions", paths)

    def test_completion_must_be_visible_and_match_the_loaded_model(self):
        for answer in (completion("other-model.gguf"), completion(content=""),
                       completion(content="", reasoning="thinking only"), completion(content="???")):
            with self.subTest(answer=answer), \
                    patch.object(agent, "_router_transport_request", side_effect=runtime(answer=answer)):
                self.assertFalse(self.prove())

    def test_unavailable_or_unowned_router_never_produces_proof(self):
        with patch.object(agent, "_router_transport_request", side_effect=OSError("owned router not available")):
            self.assertFalse(self.prove())

    def test_router_startup_is_not_mistaken_for_a_permanent_failure(self):
        serve = runtime()
        replies = iter([OSError("router is still starting")])

        def request(install_dir, origin, path, **kwargs):
            failure = next(replies, None)
            if failure is not None:
                raise failure
            return serve(install_dir, origin, path, **kwargs)

        with patch.object(agent, "_router_transport_request", side_effect=request) as transport:
            proof = self.prove(attempts=2)
        self.assertEqual(proof["identity"], MODEL)
        self.assertEqual(transport.call_count, 5)

    def test_invalid_transport_stops_readiness_without_retries(self):
        diagnosis = {}
        with patch.object(agent, "_router_transport_request", side_effect=ValueError("invalid route")) as request:
            result = self.prove(attempts=3, diagnosis=diagnosis)
        self.assertFalse(result)
        self.assertTrue(diagnosis["final"])
        self.assertEqual(request.call_count, 1)

    def test_changed_route_cancels_after_the_health_probe_without_completion(self):
        with patch.object(agent, "_router_transport_request", side_effect=runtime()) as request:
            proof = self.prove(env_still_current=lambda: not request.called)
        self.assertFalse(proof)
        self.assertEqual([call.args[2] for call in request.call_args_list], ["/health", "/v1/models"])

    def test_direct_transport_does_not_use_the_router(self):
        env = {**ENV, "ODS_HOST_LLM_TRANSPORT": "direct"}
        self.assertFalse(agent._runtime_uses_router_transport(env))
        with patch.object(agent, "_router_transport_request", side_effect=AssertionError("unexpected router probe")):
            self.assertEqual(agent._runtime_endpoint(env)[1], "direct")

    def test_missing_transport_marker_keeps_direct_routes(self):
        self.assertFalse(agent._runtime_uses_router_transport(
            {k: v for k, v in ENV.items() if k != "ODS_HOST_LLM_TRANSPORT"}))
        for backend in ("nvidia", "apple", "cpu"):
            self.assertFalse(agent._runtime_uses_router_transport({"GPU_BACKEND": backend}))

    def test_unmigrated_portal_keys_keep_the_router_route_for_one_release(self):
        legacy = {key: value for key, value in ENV.items()
                  if key not in {"ODS_HOST_LLM_TRANSPORT", "NATIVE_LLM_BASE_URL", "NATIVE_LLM_CONTAINER_BASE_URL"}}
        legacy.update({"LEMONADE_HOST_TRANSPORT": "model-router",
                       "LEMONADE_BASE_URL": "http://localhost:13305/api/v1",
                       "LEMONADE_CONTAINER_BASE_URL": ORIGIN + "/api/v1"})
        self.assertEqual(agent._runtime_endpoint(legacy), (ORIGIN, "router"))
        with patch.object(agent, "_router_transport_request", side_effect=runtime()) as request:
            self.assertEqual(self.prove(legacy)["identity"], MODEL)
        self.assertTrue(all(call.args[1] == ORIGIN for call in request.call_args_list))

    def test_transport_change_invalidates_initial_route_proof(self):
        for key, value in (("ODS_HOST_LLM_TRANSPORT", "direct"),
                           ("NATIVE_LLM_CONTAINER_BASE_URL", "http://host.docker.internal:18080")):
            with self.subTest(key=key), patch.object(agent, "load_env", return_value={**ENV, key: value}):
                self.assertFalse(agent._initial_switchboard_route_env_matches(ENV))


class WindowsRuntimeTelemetry(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(agent._wsl_runtime, "candidate", side_effect=lambda env: agent._wsl_runtime.env_value(
            env, agent._wsl_runtime.TRANSPORT_KEY)[1] == "model-router")
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(agent, "load_env", return_value=dict(ENV))
        patcher.start()
        self.addCleanup(patcher.stop)
        agent._host_llm_status_cache = (0.0, None)
        self.addCleanup(setattr, agent, "_host_llm_status_cache", (0.0, None))

    def observe(self, metrics):
        replies = {"/health": '{"status":"ok"}', "/v1/models": models(), "/props": props(), "/metrics": metrics}
        with patch.object(agent, "_router_transport_request",
                          side_effect=lambda install_dir, origin, path, **kwargs: replies[path]) as request:
            result = agent._host_llm_status()
        self.assertEqual([call.args[2] for call in request.call_args_list],
                         ["/health", "/v1/models", "/props", "/metrics"])
        self.assertTrue(all(call.kwargs["api_key"] == KEY for call in request.call_args_list))
        return result

    def test_counters_model_and_context_come_through_the_router(self):
        result = self.observe("llamacpp:prompt_tokens_total 1196\nllamacpp:tokens_predicted_total 163\n"
                              "llamacpp:tokens_predicted_seconds_total 6.63\n")
        self.assertEqual(result["source"], "wsl-model-router")
        self.assertEqual(result["health"]["model_loaded"], MODEL)
        self.assertEqual(result["health"]["context_length"], 65536)
        self.assertEqual(result["metrics"], {"prompt_tokens_total": 1196.0, "tokens_predicted_total": 163.0,
                                             "tokens_predicted_seconds_total": 6.63})
        self.assertNotIn("Users", json.dumps(result))
        self.assertNotIn(KEY, json.dumps(result))

    def test_invalid_counter_values_never_become_measurements(self):
        result = self.observe("llamacpp:prompt_tokens_total nan\nllamacpp:tokens_predicted_total -1\n"
                              "llamacpp:tokens_predicted_seconds_total inf\nllamacpp:requests_processing x\n")
        self.assertIsNone(result["metrics"])

    def test_unreachable_router_is_unavailable_not_idle(self):
        for error in (OSError("router is not running"), subprocess.TimeoutExpired("docker", 5)):
            agent._host_llm_status_cache = (0.0, None)
            with self.subTest(error=error), patch.object(agent, "_router_transport_request", side_effect=error):
                self.assertIsNone(agent._host_llm_status())


if __name__ == "__main__":
    unittest.main()
