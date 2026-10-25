"""ODS: assemble buffered backend SSE bytes into a ChatCompletion-shaped dict.

Pure, self-contained, no I/O. Caller enforces tool schemas.
"""
from __future__ import annotations

import json
from typing import Any

_MAX_BYTES = 16 * 1024 * 1024


def response_reports_failure(payload: dict[str, Any]) -> bool:
    """Shared diagnostic verdict for parsed Chat/Responses JSON and SSE.

    A null error is normal on successful Responses objects. An explicit failure
    wins over a completion marker; this verdict does not change forwarding.
    """
    for value in (payload, payload.get("response")):
        if isinstance(value, dict) and (
            value.get("error") is not None
            or value.get("status") in ("failed", "incomplete", "cancelled")
            or value.get("type") in ("error", "response.failed", "response.incomplete", "response.cancelled")
        ):
            return True
    return False


# Only source-authored reasons may reach logs or the response. Never derive a
# diagnostic from str(exc), backend fields, prompts, tool names or arguments.
_REJECTION_CODES = {
    'choice not object': 'sse_choice_not_object',
    'choices not list': 'sse_choices_not_list',
    'conflicting id': 'sse_conflicting_id',
    'conflicting role': 'sse_conflicting_role',
    'conflicting system_fingerprint': 'sse_conflicting_system_fingerprint',
    'conflicting tool id': 'sse_conflicting_tool_id',
    'conflicting tool name': 'sse_conflicting_tool_name',
    'conflicting tool type': 'sse_conflicting_tool_type',
    'content delta after finish_reason': 'sse_content_delta_after_finish_reason',
    'content looks like inline tool_call': 'sse_content_looks_like_inline_tool_call',
    'content not string': 'sse_content_not_string',
    'created not int': 'sse_created_not_int',
    'delta not object': 'sse_delta_not_object',
    'duplicate finish_reason': 'sse_duplicate_finish_reason',
    'event after [DONE]': 'sse_event_after_done',
    'event not object': 'sse_event_not_object',
    'expected_model must be non-empty str': 'sse_expected_model_must_be_non_empty_str',
    'finish_reason not string': 'sse_finish_reason_not_string',
    'finish_reason tool_calls with no tool calls': 'sse_finish_reason_tool_calls_with_no_tool_calls',
    'function not object': 'sse_function_not_object',
    'id not string': 'sse_id_not_string',
    'inconsistent choice index': 'sse_inconsistent_choice_index',
    'invalid choice index': 'sse_invalid_choice_index',
    'invalid finish_reason for content': 'sse_invalid_finish_reason_for_content',
    'invalid tool arguments json': 'sse_invalid_tool_arguments_json',
    'invalid tool index': 'sse_invalid_tool_index',
    'invalid usage frame': 'sse_invalid_usage_frame',
    'invalid utf-8': 'sse_invalid_utf_8',
    'malformed json': 'sse_malformed_json',
    'max_bytes must be positive int': 'sse_max_bytes_must_be_positive_int',
    'missing [DONE]': 'sse_missing_done',
    'missing assistant role': 'sse_missing_assistant_role',
    'missing terminal finish_reason': 'sse_missing_terminal_finish_reason',
    'missing tool id': 'sse_missing_tool_id',
    'missing tool name': 'sse_missing_tool_name',
    'missing tool type': 'sse_missing_tool_type',
    'model mismatch': 'sse_model_mismatch',
    'model not string': 'sse_model_not_string',
    'more than one choice': 'sse_more_than_one_choice',
    'no events': 'sse_no_events',
    'no frame carried expected model': 'sse_no_frame_carried_expected_model',
    'raw exceeds max_bytes': 'sse_raw_exceeds_max_bytes',
    'raw must be bytes': 'sse_raw_must_be_bytes',
    'reasoning delta after finish_reason': 'sse_reasoning_delta_after_finish_reason',
    'reasoning_content not string': 'sse_reasoning_content_not_string',
    'role is not assistant': 'sse_role_is_not_assistant',
    'tool arguments not string': 'sse_tool_arguments_not_string',
    'tool calls require finish_reason tool_calls': 'sse_tool_calls_require_finish_reason_tool_calls',
    'tool id not string': 'sse_tool_id_not_string',
    'tool indexes are not contiguous': 'sse_tool_indexes_are_not_contiguous',
    'tool name not string': 'sse_tool_name_not_string',
    'tool type not string': 'sse_tool_type_not_string',
    'tool_call not object': 'sse_tool_call_not_object',
    'tool_calls delta after finish_reason': 'sse_tool_calls_delta_after_finish_reason',
    'tool_calls not list': 'sse_tool_calls_not_list',
    'top-level error': 'sse_top_level_error',
    'truncated stream: unterminated data event': 'sse_truncated_stream_unterminated_data_event',
    'unsupported delta field': 'sse_unsupported_delta_field',
}
_SAFE_CODES = frozenset(_REJECTION_CODES.values())


class CompletionStreamError(ValueError):
    """Closed, nonsecret classification of a rejected completed stream."""

    def __init__(self, reason: str):
        known = reason in _REJECTION_CODES
        self.code = _REJECTION_CODES[reason] if known else "sse_unclassified"
        super().__init__("sse: " + (reason if known else "invalid completed stream"))


class CompletionStreamIdentityError(CompletionStreamError):
    """A completed stream did not prove the selected concrete model."""


def completion_stream_rejection_code(exc: ValueError) -> str:
    code = exc.code if isinstance(exc, CompletionStreamError) else None
    return code if isinstance(code, str) and code in _SAFE_CODES else "sse_unclassified"


def _err(msg: str) -> CompletionStreamError:
    return CompletionStreamError(msg)


def _parse_sse(raw: bytes) -> list[str]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise _err("invalid utf-8") from e
    # Normalize CRLF and lone CR to LF per SSE spec.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    events: list[str] = []
    data_lines: list[str] = []
    for line in text.split("\n"):
        if line == "":
            if data_lines:
                events.append("\n".join(data_lines))
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            v = line[5:]
            if v.startswith(" "):
                v = v[1:]
            data_lines.append(v)
    if data_lines:
        # Truncated stream: no blank-line terminator for the final event.
        raise _err("truncated stream: unterminated data event")
    return events


def _merge_tool_delta(acc: dict[int, dict[str, Any]], tc: Any) -> None:
    if not isinstance(tc, dict):
        raise _err("tool_call not object")
    idx = tc.get("index")
    if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0:
        raise _err("invalid tool index")
    slot = acc.setdefault(idx, {"id": None, "type": None, "name": None, "args": []})
    fn = tc.get("function")
    if fn is not None:
        if not isinstance(fn, dict):
            raise _err("function not object")
        name = fn.get("name")
        if name is not None:
            if not isinstance(name, str):
                raise _err("tool name not string")
            if slot["name"] is not None and slot["name"] != name:
                raise _err("conflicting tool name")
            slot["name"] = name
        args = fn.get("arguments")
        if args is not None:
            if not isinstance(args, str):
                raise _err("tool arguments not string")
            slot["args"].append(args)
    tid = tc.get("id")
    if tid is not None:
        if not isinstance(tid, str):
            raise _err("tool id not string")
        if slot["id"] is not None and slot["id"] != tid:
            raise _err("conflicting tool id")
        slot["id"] = tid
    ttype = tc.get("type")
    if ttype is not None:
        if not isinstance(ttype, str):
            raise _err("tool type not string")
        if slot["type"] is not None and slot["type"] != ttype:
            raise _err("conflicting tool type")
        slot["type"] = ttype


def assemble_chat_completion_sse(
    raw: bytes,
    expected_model: str,
    *,
    max_bytes: int = _MAX_BYTES,
    response_metadata: dict[str, bool] | None = None,
) -> dict:
    if not isinstance(raw, (bytes, bytearray)):
        raise _err("raw must be bytes")
    if not isinstance(expected_model, str) or not expected_model:
        raise _err("expected_model must be non-empty str")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise _err("max_bytes must be positive int")
    if len(raw) > max_bytes:
        raise _err("raw exceeds max_bytes")

    events = _parse_sse(bytes(raw))

    top_id: str | None = None
    top_created: int | None = None
    top_sysfp: Any = None
    usage: Any = None
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tools: dict[int, dict[str, Any]] = {}
    role: str | None = None
    finish: str | None = None
    saw_done = False
    saw_any = False
    saw_model = False
    choice_index: int | None = None

    for ev in events:
        if saw_done:
            raise _err("event after [DONE]")
        if ev.strip() == "[DONE]":
            saw_done = True
            continue
        try:
            obj = json.loads(ev)
        except json.JSONDecodeError as e:
            raise _err("malformed json") from e
        if not isinstance(obj, dict):
            raise _err("event not object")
        if response_metadata is not None:
            # Reuse this parse before assembly discards non-Chat fields. Only a
            # constant-size verdict escapes; neither payload nor output changes.
            response_metadata["failed"] = response_metadata.get("failed", False) or response_reports_failure(obj)
        if "error" in obj and obj["error"] is not None:
            raise _err("top-level error")
        saw_any = True

        if "id" in obj and obj["id"] is not None:
            if not isinstance(obj["id"], str):
                raise _err("id not string")
            if top_id is None:
                top_id = obj["id"]
            elif top_id != obj["id"]:
                raise _err("conflicting id")
        if "created" in obj and obj["created"] is not None:
            c = obj["created"]
            if not isinstance(c, int) or isinstance(c, bool):
                raise _err("created not int")
            if top_created is None:
                top_created = c
            # llama.cpp timestamps each chunk as it is emitted; keep the
            # first timestamp for the synthesized completed response.
        if "system_fingerprint" in obj and obj["system_fingerprint"] is not None:
            sfp = obj["system_fingerprint"]
            if top_sysfp is None:
                top_sysfp = sfp
            elif top_sysfp != sfp:
                raise _err("conflicting system_fingerprint")

        model = obj.get("model")
        if model is not None:
            if not isinstance(model, str):
                raise _err("model not string")
            if model != expected_model:
                raise CompletionStreamIdentityError("model mismatch")
            saw_model = True

        choices = obj.get("choices")
        if choices is None:
            if "usage" in obj and obj["usage"] is not None:
                if finish is None or not isinstance(obj["usage"], dict):
                    raise _err("invalid usage frame")
                usage = obj["usage"]
            continue
        if not isinstance(choices, list):
            raise _err("choices not list")
        if len(choices) == 0:
            # usage-only frame after finish is allowed
            if "usage" in obj and obj["usage"] is not None:
                if finish is None or not isinstance(obj["usage"], dict):
                    raise _err("invalid usage frame")
                usage = obj["usage"]
            continue
        if len(choices) > 1:
            raise _err("more than one choice")
        ch = choices[0]
        if not isinstance(ch, dict):
            raise _err("choice not object")
        ci = ch.get("index")
        if ci is not None:
            if not isinstance(ci, int) or isinstance(ci, bool) or ci != 0:
                raise _err("invalid choice index")
            if choice_index is None:
                choice_index = ci
            elif choice_index != ci:
                raise _err("inconsistent choice index")
        delta = ch.get("delta")
        if delta is not None:
            if not isinstance(delta, dict):
                raise _err("delta not object")
            if any(value is not None for key, value in delta.items()
                   if key not in {"role", "content", "tool_calls",
                                  "reasoning_content"}):
                # A live thinking-enabled llama.cpp stream uses
                # reasoning_content. Keep other fields fail-closed rather
                # than silently dropping response semantics.
                raise _err("unsupported delta field")
            if finish is not None:
                if delta.get("content") is not None:
                    raise _err("content delta after finish_reason")
                if delta.get("tool_calls") is not None:
                    raise _err("tool_calls delta after finish_reason")
                if delta.get("reasoning_content") is not None:
                    raise _err("reasoning delta after finish_reason")
            r = delta.get("role")
            if r is not None:
                if r != "assistant":
                    raise _err("role is not assistant")
                if role is not None and role != r:
                    raise _err("conflicting role")
                role = r
            c = delta.get("content")
            if c is not None:
                if not isinstance(c, str):
                    raise _err("content not string")
                content_parts.append(c)
            reasoning = delta.get("reasoning_content")
            if reasoning is not None:
                if not isinstance(reasoning, str):
                    raise _err("reasoning_content not string")
                reasoning_parts.append(reasoning)
            tcs = delta.get("tool_calls")
            if tcs is not None:
                if not isinstance(tcs, list):
                    raise _err("tool_calls not list")
                for tc in tcs:
                    _merge_tool_delta(tools, tc)
        fr = ch.get("finish_reason")
        if fr is not None:
            if not isinstance(fr, str):
                raise _err("finish_reason not string")
            if finish is not None:
                raise _err("duplicate finish_reason")
            finish = fr
        if obj.get("usage") is not None:
            if finish is None or not isinstance(obj["usage"], dict):
                raise _err("invalid usage frame")
            usage = obj["usage"]

    if not saw_any:
        raise _err("no events")
    if not saw_model:
        raise CompletionStreamIdentityError("no frame carried expected model")
    if finish is None:
        raise _err("missing terminal finish_reason")
    if not saw_done:
        raise _err("missing [DONE]")
    if role != "assistant":
        raise _err("missing assistant role")

    content = "".join(content_parts)
    has_tools = bool(tools)

    if has_tools:
        if finish != "tool_calls":
            raise _err("tool calls require finish_reason tool_calls")
        if content.strip().startswith("<tool_call>"):
            raise _err("content looks like inline tool_call")
        ordered = []
        if sorted(tools) != list(range(len(tools))):
            raise _err("tool indexes are not contiguous")
        for idx in sorted(tools):
            slot = tools[idx]
            if not slot["id"]:
                raise _err("missing tool id")
            if slot["type"] != "function":
                raise _err("missing tool type")
            if not slot["name"]:
                raise _err("missing tool name")
            args_str = "".join(slot["args"])
            try:
                json.loads(args_str)
            except json.JSONDecodeError as e:
                raise _err("invalid tool arguments json") from e
            ordered.append({
                "id": slot["id"],
                "type": slot["type"],
                "function": {"name": slot["name"], "arguments": args_str},
            })
        message: dict[str, Any] = {
            "role": role,
            "content": content or None,
            "tool_calls": ordered,
        }
    else:
        if finish == "tool_calls":
            raise _err("finish_reason tool_calls with no tool calls")
        if finish not in ("stop", "length", "content_filter"):
            raise _err("invalid finish_reason for content")
        message = {"role": role, "content": content}

    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)

    out: dict[str, Any] = {
        "object": "chat.completion",
        "model": expected_model,
        "choices": [{
            "index": choice_index if choice_index is not None else 0,
            "message": message,
            "finish_reason": finish,
        }],
    }
    if top_id is not None:
        out["id"] = top_id
    if top_created is not None:
        out["created"] = top_created
    if top_sysfp is not None:
        out["system_fingerprint"] = top_sysfp
    if usage is not None:
        out["usage"] = usage
    return out
