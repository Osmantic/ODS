"""WP3 probe battery (TEST-PLAN T-U-3): scripted runtime answers -> probe verdicts."""

from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path

import pytest

_BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from model_profile import probes  # noqa: E402

QWEN_TEMPLATE = "{% if tools %}<tools>{% endif %}{% if enable_thinking %}<think>{% endif %}"
R1_TEMPLATE = "{{ bos_token }}<｜User｜>{{ message }}<｜Assistant｜><think>\n"
HARMONY_TEMPLATE = "<|start|>assistant<|channel|>analysis<|message|>{{ reasoning_effort }}"


def _completion(content="", *, tool_calls=None, reasoning=None, finish="stop", timings=None):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    body = {"choices": [{"index": 0, "message": message, "finish_reason": finish}]}
    if timings is not None:
        body["timings"] = timings
    return 200, json.dumps(body)


def _weather_call(arguments='{"city": "Paris"}', name="get_weather"):
    return [{"id": "call_1", "type": "function", "function": {"name": name, "arguments": arguments}}]


def _sse(*chunks):
    lines = [f"data: {json.dumps(chunk)}" for chunk in chunks] + ["data: [DONE]"]
    return 200, "\n\n".join(lines) + "\n\n"


def _delta(delta, finish=None):
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


STREAMED_CALL = _sse(
    _delta({"role": "assistant", "content": None}),
    _delta({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                            "function": {"name": "get_weather", "arguments": ""}}]}),
    _delta({"tool_calls": [{"index": 0, "function": {"arguments": '{"city": '}}]}),
    _delta({"tool_calls": [{"index": 0, "function": {"arguments": '"Paris"}'}}]}),
    _delta({}, finish="tool_calls"),
)


class Runtime:
    """A scripted llama-server: one answer per probe kind, chosen by request shape."""

    def __init__(self, **answers):
        self.answers = {
            "ready": _completion("READY"),
            "tool": _completion("", tool_calls=_weather_call(), finish="tool_calls"),
            "tool_result": _completion("It is 21 °C and sunny in Paris."),
            "tool_stream": STREAMED_CALL,
            "think_on": _completion("42", reasoning="17 plus 25 is 42."),
            "think_off": _completion("42"),
            "vision": _completion("Red"),
            "speed": _completion("one, two", timings={"predicted_n": 128, "predicted_per_second": 75.3,
                                                      "prompt_n": 20, "prompt_per_second": 900.0}),
        }
        self.answers.update(answers)
        self.requests = []

    def kind(self, payload):
        messages = payload["messages"]
        first = messages[0]["content"]
        if payload.get("tools"):
            if any(message["role"] == "tool" for message in messages):
                return "tool_result"
            return "tool_stream" if payload.get("stream") else "tool"
        if isinstance(first, list):
            return "vision"
        if first == probes.THINK_PROMPT:
            kwargs = payload.get("chat_template_kwargs") or {}
            return "think_off" if kwargs.get("enable_thinking") is False else "think_on"
        if first == probes.SPEED_PROMPT:
            return "speed"
        return "ready"

    def __call__(self, path, payload, timeout):
        assert path == probes.CHAT_PATH
        assert 0 < timeout <= 60
        kind = self.kind(payload)
        self.requests.append((kind, payload))
        answer = self.answers[kind]
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, list):
            return answer.pop(0)
        return answer


def _budget(seconds=120.0):
    return probes.Budget(seconds, clock=lambda: 0.0)


def _props(template=QWEN_TEMPLATE, vision=False):
    return {
        "chat_template": template,
        "chat_template_caps": {"supports_tools": True, "supports_tool_calls": True, "other": 1},
        "modalities": {"vision": vision, "audio": False},
        "build_info": "b11429-d81235049",
        "default_generation_settings": {"n_ctx": 65536},
    }


# --- P1 -----------------------------------------------------------------------

@pytest.mark.parametrize("answer, status, failure", [
    (_completion("READY"), "pass", None),
    (_completion("<think>hmm</think>READY"), "fail", "markup-leak"),
    (_completion(""), "fail", "empty"),
    (_completion("", finish="length"), "fail", "truncated"),
    (_completion("Hello there"), "fail", "wrong-answer"),
])
def test_p1_answer(answer, status, failure):
    result = probes.probe_answer(Runtime(ready=answer), _budget(), "enable_thinking")
    assert (result["status"], result["failureClass"]) == (status, failure)


def test_p1_turns_thinking_off_only_through_the_models_own_control():
    runtime = Runtime()
    probes.probe_answer(runtime, _budget(), "enable_thinking")
    probes.probe_answer(runtime, _budget(), "always")
    probes.probe_answer(runtime, _budget(), "none")
    (_, qwen), (_, always), (_, plain) = runtime.requests
    assert qwen["chat_template_kwargs"] == {"enable_thinking": False}
    assert "chat_template_kwargs" not in always and "chat_template_kwargs" not in plain
    # An always-thinking model gets room to reason before it answers: an
    # R1 distill needs about 440 tokens for this prompt.
    assert always["max_tokens"] >= probes.ALWAYS_THINKING_MIN_TOKENS > qwen["max_tokens"]
    assert all(payload["temperature"] == 0 and payload["seed"] == probes.SEED for _, payload in runtime.requests)


# --- P2 -----------------------------------------------------------------------

@pytest.mark.parametrize("answer, status, failure", [
    (_completion("", tool_calls=_weather_call(), finish="tool_calls"), "pass", None),
    (_completion('<tool_call>{"name": "get_weather", "arguments": {"city": "Paris"}}</tool_call>'),
     "fail", "content-leak"),
    ((400, '{"error": {"code": 400, "message": "Unable to generate parser for this template."}}'),
     "fail", "parser-error"),
    (_completion("I cannot check the weather."), "fail", "ignored"),
    (_completion("", tool_calls=_weather_call('{"city": Paris'), finish="tool_calls"), "fail", "invalid-args"),
    (_completion("", tool_calls=_weather_call('{"city": "London"}'), finish="tool_calls"), "fail", "invalid-args"),
    (_completion("", tool_calls=_weather_call(name="search"), finish="tool_calls"), "fail", "wrong-tool"),
    (_completion("", tool_calls=_weather_call(), finish="stop"), "fail", "finish-reason"),
    ((500, "internal error"), "error", "http-500"),
    ((200, "not json"), "error", "bad-response"),
])
def test_p2_tool_call(answer, status, failure):
    result, message = probes.probe_tool_call(Runtime(tool=answer), _budget(), "enable_thinking")
    assert (result["status"], result["failureClass"]) == (status, failure)
    # Only a parsed 200 answer carries a message for P3.
    assert (message is None) == (failure in {"parser-error", "http-500", "bad-response"})


def test_p2_arguments_may_arrive_as_an_object():
    answer = _completion("", tool_calls=_weather_call({"city": "Paris, France"}), finish="tool_calls")
    result, _message = probes.probe_tool_call(Runtime(tool=answer), _budget(), "none")
    assert result["status"] == "pass"


# --- P3 -----------------------------------------------------------------------

@pytest.mark.parametrize("answer, status, failure", [
    (_completion("It is 21 °C and sunny."), "pass", None),
    (_completion("", tool_calls=_weather_call(), finish="tool_calls"), "fail", "loops"),
    (_completion("I could not find the weather."), "fail", "ignores-result"),
])
def test_p3_tool_round_trip(answer, status, failure):
    runtime = Runtime(tool_result=answer)
    _p2, message = probes.probe_tool_call(runtime, _budget(), "enable_thinking")
    result = probes.probe_tool_round_trip(runtime, _budget(), "enable_thinking", message)
    assert (result["status"], result["failureClass"]) == (status, failure)
    _kind, payload = runtime.requests[-1]
    assert [message["role"] for message in payload["messages"]] == ["user", "assistant", "tool"]
    assert payload["messages"][2]["tool_call_id"] == "call_1"
    assert json.loads(payload["messages"][2]["content"]) == probes.TOOL_RESULT


def test_p3_is_skipped_without_a_p2_call():
    assert probes.probe_tool_round_trip(Runtime(), _budget(), "none", None)["status"] == "skipped"


# --- P4 -----------------------------------------------------------------------

def test_p4_reassembles_tool_call_deltas_split_across_chunks():
    result = probes.probe_streamed_tool_call(Runtime(), _budget(), "enable_thinking")
    assert result["status"] == "pass"
    assert result["evidence"]["toolCalls"] == [{"name": "get_weather", "arguments": '{"city": "Paris"}'}]


def test_p4_records_a_stream_only_failure():
    leaked = _sse(_delta({"content": "<tool_call>{\"name\": \"get_weather\"}"}), _delta({}, finish="stop"))
    runtime = Runtime(tool_stream=leaked)
    p2, _message = probes.probe_tool_call(runtime, _budget(), "enable_thinking")
    p4 = probes.probe_streamed_tool_call(runtime, _budget(), "enable_thinking")
    assert p2["status"] == "pass"
    assert (p4["status"], p4["failureClass"]) == ("fail", "content-leak")


def test_p4_without_any_stream_chunk_is_an_error():
    result = probes.probe_streamed_tool_call(Runtime(tool_stream=(200, "{}")), _budget(), "none")
    assert (result["status"], result["failureClass"]) == ("error", "bad-response")


# --- P5 -----------------------------------------------------------------------

@pytest.mark.parametrize("template, control", [
    (QWEN_TEMPLATE, "enable_thinking"),
    (R1_TEMPLATE, "always"),
    (HARMONY_TEMPLATE, "always"),
    ("{% for message in messages %}{{ message.content }}{% endfor %}", "none"),
    ("", "none"),
])
def test_thinking_control_from_the_template(template, control):
    assert probes.thinking_control(template) == control


@pytest.mark.parametrize("on, off, status, failure, separated", [
    (_completion("42", reasoning="17 + 25 = 42"), _completion("42"), "pass", None, True),
    (_completion("<think>17 + 25</think>42"), _completion("42"), "fail", "leak-into-content", None),
    (_completion("42", reasoning="17 + 25 = 42"), _completion("42", reasoning="still thinking"), "fail", "off-ignored", True),
    (_completion("", finish="length"), _completion("42"), "unknown", "truncated", False),
])
def test_p5_thinking_toggle(on, off, status, failure, separated):
    result = probes.probe_thinking(Runtime(think_on=on, think_off=off), _budget(), "enable_thinking")
    assert (result["status"], result["failureClass"]) == (status, failure)
    assert result["evidence"].get("separated") == separated


def test_p5_always_thinking_model_needs_reasoning_separated():
    runtime = Runtime(think_on=_completion("42", reasoning="17 + 25 = 42"))
    result = probes.probe_thinking(runtime, _budget(), "always")
    assert result["status"] == "pass" and result["evidence"]["separated"] is True
    # There is no "off" to test for a model that always thinks.
    assert [kind for kind, _ in runtime.requests] == ["think_on"]


def test_p5_model_without_a_control_must_not_think():
    result = probes.probe_thinking(Runtime(think_on=_completion("42", reasoning="hmm")), _budget(), "none")
    assert (result["status"], result["failureClass"]) == ("fail", "thinks-without-control")


# --- P6 -----------------------------------------------------------------------

def test_vision_probe_image_is_a_valid_red_png():
    png = probes.red_square_png()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (64, 64)
    idat_length = struct.unpack(">I", png[33:37])[0]
    raw = zlib.decompress(png[41:41 + idat_length])
    assert raw[:4] == b"\x00\xff\x00\x00" and len(raw) == 64 * (1 + 64 * 3)


@pytest.mark.parametrize("answer, status, failure", [
    (_completion("Red."), "pass", None),
    (_completion("Blue"), "fail", "wrong"),
])
def test_p6_vision(answer, status, failure):
    runtime = Runtime(vision=answer)
    result = probes.probe_vision(runtime, _budget(), "none", vision=True)
    assert (result["status"], result["failureClass"]) == (status, failure)
    content = runtime.requests[0][1]["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_p6_is_skipped_without_a_projector():
    runtime = Runtime()
    assert probes.probe_vision(runtime, _budget(), "none", vision=False)["failureClass"] == "no-projector"
    assert runtime.requests == []


# --- P8 -----------------------------------------------------------------------

def test_p8_reads_llama_server_request_timings():
    result = probes.probe_speed(Runtime(), _budget(), "none")
    assert result["status"] == "pass"
    assert result["evidence"]["predicted_per_second"] == 75.3


def test_p8_without_timings_is_unknown():
    result = probes.probe_speed(Runtime(speed=_completion("one")), _budget(), "none")
    assert (result["status"], result["failureClass"]) == ("unknown", "no-timings")


# --- transport, budget, battery ----------------------------------------------

def test_a_timed_out_or_unreachable_runtime_is_an_error_not_a_crash():
    result = probes.probe_answer(Runtime(ready=TimeoutError("timed out")), _budget(), "none")
    assert (result["status"], result["failureClass"]) == ("error", "unreachable")


def test_a_busy_runtime_gets_one_retry():
    runtime = Runtime(ready=[(503, "loading"), _completion("READY")])
    assert probes.probe_answer(runtime, _budget(), "none")["status"] == "pass"
    assert len(runtime.requests) == 2


def test_an_exhausted_budget_leaves_the_rest_unknown_and_drops_speed_first():
    now = [0.0]
    runtime = Runtime()
    original = runtime.__call__

    def slow(path, payload, timeout):
        now[0] += 30.0
        return original(path, payload, timeout)

    profile = probes.run_battery(slow, _props(), budget_seconds=100.0, clock=lambda: now[0])
    statuses = {name: result["status"] for name, result in profile["probes"].items()}
    assert statuses["P1"] == "pass" and statuses["P2"] == "pass"
    assert statuses["P8"] == "unknown"
    assert profile["status"] == "partial"
    assert profile["probes"]["P8"]["failureClass"] == "budget-exhausted"


def test_full_battery_on_a_well_behaved_qwen_style_model():
    profile = probes.run_battery(Runtime(), _props(), clock=lambda: 0.0)
    assert profile["status"] == "complete"
    assert profile["suite"] == probes.SUITE_VERSION == "3"
    assert {name: result["status"] for name, result in profile["probes"].items()} == {
        "P1": "pass", "P2": "pass", "P3": "pass", "P4": "pass", "P5": "pass", "P6": "skipped", "P8": "pass"}
    assert profile["summary"] == {
        "chat": True,
        "tools": True,
        "toolsStreamed": True,
        "thinking": {"control": "enable_thinking", "separated": True, "works": True},
        "vision": None,
        "tokensPerSecond": 75.3,
    }
    facts = profile["facts"]
    assert facts["buildInfo"] == "b11429-d81235049"
    assert facts["templateCaps"] == {"supports_tools": True, "supports_tool_calls": True}
    assert facts["contextLength"] == 65536 and len(facts["templateSha256"]) == 64


def test_chat_only_model_summary():
    runtime = Runtime(tool=_completion("I cannot use tools."), tool_stream=_sse(_delta({"content": "No."}), _delta({}, "stop")))
    profile = probes.run_battery(runtime, _props(template=""), clock=lambda: 0.0)
    summary = profile["summary"]
    assert summary["chat"] is True and summary["tools"] is False and summary["toolsStreamed"] is False
    assert profile["probes"]["P3"]["status"] == "skipped"


def test_evidence_is_capped_and_redacted():
    leak = "Bearer abcdef0123456789 /home/someone/models/x.gguf C:\\Users\\someone\\x " + "x" * 2000
    result = probes.probe_answer(Runtime(ready=_completion(leak)), _budget(), "none")
    content = result["evidence"]["content"]
    assert "abcdef0123456789" not in content and "/home/someone" not in content and "C:\\Users" not in content
    assert len(content) <= probes.EVIDENCE_TEXT_LIMIT + 1


def test_probe_prompts_carry_no_private_text():
    prompts = " ".join([probes.READY_PROMPT, probes.TOOL_PROMPT, probes.THINK_PROMPT,
                        probes.VISION_PROMPT, probes.SPEED_PROMPT, json.dumps(probes.WEATHER_TOOL)])
    for word in ("michael", "tower", "strixy", "light-worker", "fleet", "osmantic", "ods"):
        assert word not in prompts.lower()
