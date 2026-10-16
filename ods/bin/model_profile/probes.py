"""The first-switch capability battery (PLAN WP3.2, probes P1-P6 and P8).

Every probe talks to the loaded llama-server only through an injected
``exchange(path, payload, timeout) -> (http_status, text)`` callable, so the
host agent keeps transport, keys and endpoint selection. ``exchange`` raises
``OSError`` when the runtime cannot be reached or a request times out.

Each probe returns a result dict::

    {"status": "pass" | "fail" | "skipped" | "error" | "unknown",
     "failureClass": str | None, "evidence": dict, "ms": int}

Model behaviour never raises: transport failures and unexpected response
shapes are ``error``, an exhausted budget is ``unknown``. Requests run at
temperature 0 with a fixed seed and a bounded ``max_tokens``. Prompts are
fixed product text with no owner, host or fleet details.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import struct
import time
import zlib
from typing import Any, Callable

from . import SUITE_VERSION

Exchange = Callable[[str, "dict | None", float], "tuple[int, str]"]

CHAT_PATH = "/v1/chat/completions"
SEED = 7
BUDGET_SECONDS = 120.0
# A probe is not started with less time than this left; it is ``unknown``.
MIN_PROBE_SECONDS = 4.0
EVIDENCE_TEXT_LIMIT = 400

READY_PROMPT = "Reply with the single word READY."
TOOL_PROMPT = "Use the get_weather tool to check the weather in Paris."
TOOL_RESULT = {"temperature_c": 21, "condition": "sunny"}
THINK_PROMPT = "What is 17 + 25? Reply with the number only."
VISION_PROMPT = "What color is this square? One word."
SPEED_PROMPT = "Count from one to thirty in words, separated by commas."
WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "The city name."}},
            "required": ["city"],
        },
    },
}

# Thinking markup that belongs in reasoning_content, never in the answer.
_THINK_MARKUP = re.compile(r"</?think>|<\|channel\|>|<\|start\|>|<\|message\|>|<\|end\|>", re.IGNORECASE)
# Tool-call syntax that leaked into content instead of a structured call.
_TOOL_MARKUP = re.compile(
    r"</?tool_call>|\[TOOL_CALLS\]|<\|tool_call|<function=|\"name\"\s*:\s*\"get_weather\"",
    re.IGNORECASE,
)
_PARSER_ERROR = re.compile(r"unable to generate parser|parser", re.IGNORECASE)
_PRIVATE_PATH = re.compile(
    r"(?:/(?:home|Users|root|mnt|var|opt|tmp|srv|private)/|[A-Za-z]:\\)[^\s\"']*"
)
_BEARER = re.compile(r"(?i)bearer\s+\S+")

# Per-probe ceilings; the total budget can only shorten them.
PROBE_TIMEOUTS = {"P1": 30.0, "P2": 45.0, "P3": 45.0, "P4": 45.0, "P5": 60.0, "P6": 45.0, "P8": 30.0}


class Budget:
    """One total time budget for the whole battery (PLAN D2)."""

    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._deadline = clock() + seconds

    def remaining(self) -> float:
        return max(0.0, self._deadline - self._clock())

    def timeout_for(self, probe: str) -> float | None:
        """This probe's timeout, or None when the budget cannot start it."""
        left = self.remaining()
        if left < MIN_PROBE_SECONDS:
            return None
        return min(PROBE_TIMEOUTS[probe], left)


def _result(status: str, failure: str | None = None, evidence: dict | None = None, ms: int = 0) -> dict:
    return {"status": status, "failureClass": failure, "evidence": evidence or {}, "ms": ms}


def _excerpt(value: Any, limit: int = EVIDENCE_TEXT_LIMIT) -> str:
    """A capped, redacted text excerpt: no absolute paths, no bearer tokens."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = _BEARER.sub("Bearer <redacted>", _PRIVATE_PATH.sub("<path>", text))
    return text if len(text) <= limit else text[:limit] + "…"


def thinking_kwargs(control: str, *, on: bool) -> dict:
    """Request fields that set thinking for a model with this ``control``."""
    if control == "enable_thinking":
        return {"chat_template_kwargs": {"enable_thinking": on}}
    return {}


# Models that always think need room for their reasoning before the answer:
# DeepSeek-R1-Distill-Qwen-1.5B spends about 440 tokens before "Ready", so a
# 96-token chat probe recorded it as unable to chat on one GPU and able on
# another (Tower3 and tower2, 2026-10-09).
ALWAYS_THINKING_MIN_TOKENS = 1024


def _chat_request(messages: list, control: str, *, max_tokens: int, think: bool = False, **extra) -> dict:
    budget = max(max_tokens * 4, ALWAYS_THINKING_MIN_TOKENS) if control == "always" else max_tokens
    return {
        "messages": messages,
        "temperature": 0,
        "seed": SEED,
        "max_tokens": budget,
        "stream": False,
        **thinking_kwargs(control, on=think),
        **extra,
    }


def _message(text: str) -> tuple[dict, str | None]:
    """(first choice message, finish_reason) from a completion body."""
    body = json.loads(text)
    choice = body["choices"][0]
    message = choice.get("message") or {}
    if not isinstance(message, dict):
        raise TypeError("choice message is not an object")
    return message, choice.get("finish_reason")


def _run(exchange: Exchange, budget: Budget, probe: str, payload: dict | None,
         path: str = CHAT_PATH) -> tuple[int, str, int] | dict:
    """Send one request; a dict return is the probe's final error/unknown result."""
    timeout = budget.timeout_for(probe)
    if timeout is None:
        return _result("unknown", "budget-exhausted")
    started = time.monotonic()
    attempts = 0
    while True:
        attempts += 1
        try:
            status, text = exchange(path, payload, timeout)
        except OSError as exc:
            ms = int((time.monotonic() - started) * 1000)
            return _result("error", "unreachable", {"error": _excerpt(str(exc), 200)}, ms)
        # One retry for a runtime that is briefly busy, never for an answer.
        if status in {502, 503} and attempts == 1 and budget.timeout_for(probe) is not None:
            continue
        return status, text, int((time.monotonic() - started) * 1000)


def _http_failure(status: str | int, text: str, ms: int, *, tools: bool = False) -> dict:
    if tools and status == 400 and _PARSER_ERROR.search(text or ""):
        return _result("fail", "parser-error", {"http": status, "error": _excerpt(text, 300)}, ms)
    return _result("error", f"http-{status}", {"http": status, "error": _excerpt(text, 300)}, ms)


def _shape_error(exc: Exception, text: str, ms: int) -> dict:
    return _result("error", "bad-response", {"error": type(exc).__name__, "body": _excerpt(text, 300)}, ms)


def static_facts(props: dict) -> dict:
    """What llama-server's /props says once the model is loaded."""
    template = props.get("chat_template")
    template = template if isinstance(template, str) else ""
    caps = props.get("chat_template_caps")
    modalities = props.get("modalities") if isinstance(props.get("modalities"), dict) else {}
    settings = props.get("default_generation_settings")
    n_ctx = settings.get("n_ctx") if isinstance(settings, dict) else None
    return {
        "buildInfo": props.get("build_info") if isinstance(props.get("build_info"), str) else None,
        "templatePresent": bool(template.strip()),
        "templateSha256": hashlib.sha256(template.encode("utf-8")).hexdigest() if template else None,
        "templateCaps": {
            key: bool(value) for key, value in caps.items() if isinstance(key, str) and key.startswith("supports_")
        } if isinstance(caps, dict) else {},
        "vision": modalities.get("vision") is True,
        "audio": modalities.get("audio") is True,
        "contextLength": n_ctx if isinstance(n_ctx, int) and not isinstance(n_ctx, bool) else None,
        "thinkingControl": thinking_control(template),
        "effortInTemplate": "reasoning_effort" in template,
    }


def thinking_control(template: str) -> str:
    """How a request turns thinking off: ``enable_thinking``, ``always`` or ``none``.

    ``always`` covers templates that open a reasoning block every turn
    (DeepSeek-R1 distills) and harmony's channels (gpt-oss), which the pinned
    builds do not let a request switch off.
    """
    if "enable_thinking" in template:
        return "enable_thinking"
    if "<|channel|>" in template or "<think>" in template:
        return "always"
    return "none"


def probe_answer(exchange: Exchange, budget: Budget, control: str) -> dict:
    """P1: a plain answer, with thinking off where the model allows it."""
    sent = _run(exchange, budget, "P1", _chat_request(
        [{"role": "user", "content": READY_PROMPT}], control, max_tokens=24))
    if isinstance(sent, dict):
        return sent
    status, text, ms = sent
    if status != 200:
        return _http_failure(status, text, ms)
    try:
        message, finish = _message(text)
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return _shape_error(exc, text, ms)
    content = message.get("content") if isinstance(message.get("content"), str) else ""
    evidence = {"http": status, "finishReason": finish, "content": _excerpt(content)}
    if not content.strip():
        return _result("fail", "truncated" if finish == "length" else "empty", evidence, ms)
    if _THINK_MARKUP.search(content):
        return _result("fail", "markup-leak", evidence, ms)
    if "READY" not in content.upper():
        return _result("fail", "wrong-answer", evidence, ms)
    return _result("pass", None, evidence, ms)


def _tool_request(control: str, *, stream: bool = False, messages: list | None = None) -> dict:
    return _chat_request(
        messages or [{"role": "user", "content": TOOL_PROMPT}],
        control,
        max_tokens=256,
        tools=[WEATHER_TOOL],
        tool_choice="auto",
        stream=stream,
    )


def classify_tool_call(message: dict, finish: str | None) -> tuple[str, str | None]:
    """(status, failureClass) for one get_weather call by the P2/P4 oracle."""
    content = message.get("content") if isinstance(message.get("content"), str) else ""
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or not calls:
        return ("fail", "content-leak") if _TOOL_MARKUP.search(content) else ("fail", "ignored")
    function = calls[0].get("function") if isinstance(calls[0], dict) else None
    if not isinstance(function, dict) or function.get("name") != "get_weather":
        return "fail", "wrong-tool"
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return "fail", "invalid-args"
    if not isinstance(arguments, dict) or "paris" not in str(arguments.get("city") or "").lower():
        return "fail", "invalid-args"
    if _TOOL_MARKUP.search(content):
        return "fail", "content-leak"
    if finish != "tool_calls":
        return "fail", "finish-reason"
    return "pass", None


def _tool_evidence(status: int, message: dict, finish: str | None) -> dict:
    calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
    return {
        "http": status,
        "finishReason": finish,
        "content": _excerpt(message.get("content") or ""),
        "toolCalls": [
            {"name": str((call.get("function") or {}).get("name") or "")[:64],
             "arguments": _excerpt((call.get("function") or {}).get("arguments") or "", 200)}
            for call in calls[:3] if isinstance(call, dict)
        ],
    }


def probe_tool_call(exchange: Exchange, budget: Budget, control: str) -> tuple[dict, dict | None]:
    """P2: one structured tool call, not streamed. Also returns the message for P3."""
    sent = _run(exchange, budget, "P2", _tool_request(control))
    if isinstance(sent, dict):
        return sent, None
    status, text, ms = sent
    if status != 200:
        return _http_failure(status, text, ms, tools=True), None
    try:
        message, finish = _message(text)
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return _shape_error(exc, text, ms), None
    verdict, failure = classify_tool_call(message, finish)
    return _result(verdict, failure, _tool_evidence(status, message, finish), ms), message


def probe_tool_round_trip(exchange: Exchange, budget: Budget, control: str, call_message: dict | None) -> dict:
    """P3: the tool result goes back; the answer must use it without calling again."""
    if call_message is None:
        return _result("skipped", "needs-p2")
    call = dict(call_message["tool_calls"][0])
    call.setdefault("id", "call_0")
    call.setdefault("type", "function")
    messages = [
        {"role": "user", "content": TOOL_PROMPT},
        {"role": "assistant", "content": call_message.get("content") or "", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(TOOL_RESULT)},
    ]
    sent = _run(exchange, budget, "P3", _tool_request(control, messages=messages))
    if isinstance(sent, dict):
        return sent
    status, text, ms = sent
    if status != 200:
        return _http_failure(status, text, ms, tools=True)
    try:
        message, finish = _message(text)
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return _shape_error(exc, text, ms)
    evidence = _tool_evidence(status, message, finish)
    if isinstance(message.get("tool_calls"), list) and message["tool_calls"]:
        return _result("fail", "loops", evidence, ms)
    content = (message.get("content") or "").lower() if isinstance(message.get("content"), str) else ""
    if "21" not in content and "sunny" not in content:
        return _result("fail", "ignores-result", evidence, ms)
    return _result("pass", None, evidence, ms)


def reassemble_stream(text: str) -> tuple[dict, str | None]:
    """Fold OpenAI-style SSE chunks into one (message, finish_reason)."""
    content: list[str] = []
    calls: dict[int, dict] = {}
    finish = None
    chunks = 0
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        chunk = json.loads(data)
        chunks += 1
        choices = chunk.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        finish = choice.get("finish_reason") or finish
        delta = choice.get("delta") or {}
        if isinstance(delta.get("content"), str):
            content.append(delta["content"])
        for part in delta.get("tool_calls") or []:
            slot = calls.setdefault(int(part.get("index", 0)), {"id": None, "function": {"name": "", "arguments": ""}})
            slot["id"] = part.get("id") or slot["id"]
            function = part.get("function") or {}
            slot["function"]["name"] += function.get("name") or ""
            slot["function"]["arguments"] += function.get("arguments") or ""
    if chunks == 0:
        raise ValueError("no stream chunks")
    message: dict = {"content": "".join(content)}
    if calls:
        message["tool_calls"] = [calls[index] for index in sorted(calls)]
    return message, finish


def probe_streamed_tool_call(exchange: Exchange, budget: Budget, control: str) -> dict:
    """P4: P2 streamed; deltas reassembled and judged by the same oracle."""
    sent = _run(exchange, budget, "P4", _tool_request(control, stream=True))
    if isinstance(sent, dict):
        return sent
    status, text, ms = sent
    if status != 200:
        return _http_failure(status, text, ms, tools=True)
    try:
        message, finish = reassemble_stream(text)
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return _shape_error(exc, text, ms)
    verdict, failure = classify_tool_call(message, finish)
    return _result(verdict, failure, _tool_evidence(status, message, finish), ms)


def probe_thinking(exchange: Exchange, budget: Budget, control: str) -> dict:
    """P5 (live part): is reasoning separated from the answer, and does "off" work?"""
    on_payload = _chat_request([{"role": "user", "content": THINK_PROMPT}], control, max_tokens=384, think=True)
    sent = _run(exchange, budget, "P5", on_payload)
    if isinstance(sent, dict):
        return sent
    status, text, ms = sent
    if status != 200:
        return _http_failure(status, text, ms)
    try:
        message, finish = _message(text)
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return _shape_error(exc, text, ms)
    content = message.get("content") if isinstance(message.get("content"), str) else ""
    reasoning = message.get("reasoning_content") if isinstance(message.get("reasoning_content"), str) else ""
    evidence = {"control": control, "on": {"finishReason": finish, "reasoningChars": len(reasoning),
                                          "content": _excerpt(content, 160)}}
    if _THINK_MARKUP.search(content):
        return _result("fail", "leak-into-content", evidence, ms)
    if control == "none":
        evidence["separated"] = None
        if reasoning.strip():
            return _result("fail", "thinks-without-control", evidence, ms)
        return _result("pass", None, evidence, ms)
    evidence["separated"] = bool(reasoning.strip()) and bool(content.strip())
    if not reasoning.strip():
        if finish == "length":
            return _result("unknown", "truncated", evidence, ms)
        return _result("fail", "no-reasoning", evidence, ms)
    if control != "enable_thinking":
        return _result("pass", None, evidence, ms)
    off = _run(exchange, budget, "P5", _chat_request(
        [{"role": "user", "content": THINK_PROMPT}], control, max_tokens=64, think=False))
    if isinstance(off, dict):
        return {**off, "evidence": evidence}
    status, text, off_ms = off
    ms += off_ms
    if status != 200:
        return _http_failure(status, text, ms)
    try:
        message, finish = _message(text)
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return _shape_error(exc, text, ms)
    content = message.get("content") if isinstance(message.get("content"), str) else ""
    reasoning = message.get("reasoning_content") if isinstance(message.get("reasoning_content"), str) else ""
    evidence["off"] = {"finishReason": finish, "reasoningChars": len(reasoning), "content": _excerpt(content, 160)}
    if reasoning.strip() or _THINK_MARKUP.search(content):
        return _result("fail", "off-ignored", evidence, ms)
    return _result("pass", None, evidence, ms)


def red_square_png(size: int = 64) -> bytes:
    """A solid red size x size RGB PNG, built without image libraries."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    row = b"\x00" + b"\xff\x00\x00" * size
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * size, 9))
            + chunk(b"IEND", b""))


def probe_vision(exchange: Exchange, budget: Budget, control: str, *, vision: bool) -> dict:
    """P6: only with a projector loaded; one red square, one word back."""
    if not vision:
        return _result("skipped", "no-projector")
    image = "data:image/png;base64," + base64.b64encode(red_square_png()).decode("ascii")
    sent = _run(exchange, budget, "P6", _chat_request([{"role": "user", "content": [
        {"type": "text", "text": VISION_PROMPT},
        {"type": "image_url", "image_url": {"url": image}},
    ]}], control, max_tokens=16))
    if isinstance(sent, dict):
        return sent
    status, text, ms = sent
    if status != 200:
        return _http_failure(status, text, ms)
    try:
        message, finish = _message(text)
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return _shape_error(exc, text, ms)
    content = message.get("content") if isinstance(message.get("content"), str) else ""
    evidence = {"http": status, "finishReason": finish, "content": _excerpt(content, 160)}
    if "red" not in content.lower():
        return _result("fail", "wrong", evidence, ms)
    return _result("pass", None, evidence, ms)


def probe_speed(exchange: Exchange, budget: Budget, control: str) -> dict:
    """P8: generation and prompt speed from llama-server's own request timings."""
    sent = _run(exchange, budget, "P8", _chat_request(
        [{"role": "user", "content": SPEED_PROMPT}], control, max_tokens=128))
    if isinstance(sent, dict):
        return sent
    status, text, ms = sent
    if status != 200:
        return _http_failure(status, text, ms)
    try:
        timings = json.loads(text).get("timings")
    except ValueError as exc:
        return _shape_error(exc, text, ms)
    if not isinstance(timings, dict):
        return _result("unknown", "no-timings", {"http": status}, ms)
    evidence = {key: timings.get(key) for key in (
        "predicted_n", "predicted_per_second", "prompt_n", "prompt_per_second") if isinstance(timings.get(key), (int, float))}
    if not evidence.get("predicted_per_second"):
        return _result("unknown", "no-timings", evidence, ms)
    return _result("pass", None, evidence, ms)


def run_battery(
    exchange: Exchange,
    props: dict,
    *,
    budget_seconds: float = BUDGET_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """Run P1-P6 then P8 inside one budget; P8 is the first to be dropped."""
    started = clock()
    budget = Budget(budget_seconds, clock)
    facts = static_facts(props)
    control = facts["thinkingControl"]
    probes: dict[str, dict] = {}
    probes["P1"] = probe_answer(exchange, budget, control)
    probes["P2"], call_message = probe_tool_call(exchange, budget, control)
    probes["P3"] = probe_tool_round_trip(
        exchange, budget, control, call_message if probes["P2"]["status"] == "pass" else None)
    probes["P4"] = probe_streamed_tool_call(exchange, budget, control)
    probes["P5"] = probe_thinking(exchange, budget, control)
    probes["P6"] = probe_vision(exchange, budget, control, vision=facts["vision"])
    probes["P8"] = probe_speed(exchange, budget, control)
    return {
        "suite": SUITE_VERSION,
        "status": "partial" if any(result["status"] == "unknown" for result in probes.values()) else "complete",
        "budgetMs": int(budget_seconds * 1000),
        "elapsedMs": int((clock() - started) * 1000),
        "facts": facts,
        "probes": probes,
        "summary": summarize(facts, probes),
    }


def _passed(result: dict) -> bool | None:
    """True/False for a decided probe, None when it did not decide."""
    if result["status"] == "pass":
        return True
    if result["status"] == "fail":
        return False
    return None


def summarize(facts: dict, probes: dict) -> dict:
    """The few answers consumers read (WP4); None means "not known"."""
    tools = _passed(probes["P2"])
    if tools is True:
        tools = _passed(probes["P3"])
    speed = probes["P8"]["evidence"].get("predicted_per_second") if probes["P8"]["status"] == "pass" else None
    return {
        "chat": _passed(probes["P1"]),
        "tools": tools,
        "toolsStreamed": _passed(probes["P4"]),
        "thinking": {
            "control": facts["thinkingControl"],
            "separated": probes["P5"]["evidence"].get("separated") if probes["P5"]["status"] in {"pass", "fail"} else None,
            "works": _passed(probes["P5"]),
        },
        "vision": _passed(probes["P6"]) if facts["vision"] else None,
        "tokensPerSecond": round(float(speed), 1) if isinstance(speed, (int, float)) else None,
    }
