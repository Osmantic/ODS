"""Read-only native receipt witness for one Portal task.

Captures the native session store BEFORE an API call and joins the AFTER state
to the exact API receipt. This is an operator attestor, not a cryptographic
proof against a malicious host. No writes, probes, subprocesses, or network.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from pathlib import Path
from datetime import datetime, timezone

import probe
import pixel_adapter

MAX_BYTES = 8 * 1024 * 1024
MAX_LINE = 1024 * 1024
DELIVERY_NOTE = ("\n\n[ODS Portal delivery requirement: Answer the owner's complete "
                 "message above. If it asks for exact text, copy that full exact text. "
                 "Do not answer with a generic acknowledgement. Do not output NO_REPLY.]")
TOOL_OK_STATUS = {"completed", "succeeded", "success"}
GENERIC_TOOLS = {"read", "write", "edit"}
PIXEL_TOOL = "pixel_ods_readonly_exec"


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _native_key(chat_id):
    return "agent:pixel:openai-user:ods-" + _sha(chat_id)


def _strict_json(text):
    return json.loads(text, parse_constant=probe._reject_constant,
                      object_pairs_hook=probe._unique_object)


def _parse_utc(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("bad_timestamp")
    dt = datetime.fromisoformat(value[:-1] + "+00:00")
    if dt.tzinfo is None:
        raise ValueError("bad_timestamp")
    return dt.astimezone(timezone.utc)


def _abs_clean(path):
    if not isinstance(path, str) or not path or not os.path.isabs(path):
        raise ValueError("bad_path")
    parts = Path(path).parts
    if any(p in ("..",) for p in parts):
        raise ValueError("bad_path")
    norm = os.path.normpath(path)
    if not os.path.isabs(norm):
        raise ValueError("bad_path")
    cur = norm
    while True:
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(st.st_mode):
                raise ValueError("symlink")
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return norm


def _lstat_regular(path):
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        raise ValueError("symlink")
    if not stat.S_ISREG(st.st_mode):
        raise ValueError("not_regular")
    if st.st_nlink != 1:
        raise ValueError("hardlink")
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        raise ValueError("owner_mismatch")
    return st


def _read_stable(path):
    pre = _lstat_regular(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("not_regular")
        if hasattr(os, "getuid") and before.st_uid != os.getuid():
            raise ValueError("owner_mismatch")
        if (before.st_dev, before.st_ino) != (pre.st_dev, pre.st_ino):
            raise ValueError("changed_during_read")
        if before.st_size > MAX_BYTES:
            raise ValueError("too_large")
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BYTES:
                raise ValueError("too_large")
            chunks.append(chunk)
        after = os.fstat(fd)
        if (before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev) != (
                after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev):
            raise ValueError("changed_during_read")
        post = _lstat_regular(path)
        if (post.st_ino, post.st_dev) != (after.st_ino, after.st_dev):
            raise ValueError("changed_during_read")
        return b"".join(chunks), (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    finally:
        os.close(fd)


def _load_sessions(path):
    raw, snap = _read_stable(path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("invalid_utf8")
    try:
        data = _strict_json(text)
    except (ValueError, json.JSONDecodeError):
        raise ValueError("invalid_sessions") from None
    if not isinstance(data, dict):
        raise ValueError("bad_sessions")
    return data, snap


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _entry_identity(entry):
    if not isinstance(entry, dict):
        raise ValueError("bad_entry")
    sid, sfile = entry.get("sessionId"), entry.get("sessionFile")
    if not isinstance(sid, str) or not isinstance(sfile, str):
        raise ValueError("bad_entry")
    if not _UUID_RE.fullmatch(sid):
        raise ValueError("bad_entry")
    try:
        if str(uuid.UUID(sid)) != sid:
            raise ValueError("bad_entry")
    except (ValueError, AttributeError):
        raise ValueError("bad_entry") from None
    if not os.path.isabs(sfile):
        raise ValueError("bad_session_file")
    return sid, sfile


def _canonical_session_file(root, sid, sfile):
    expected = os.path.join(root, sid + ".jsonl")
    if os.path.normpath(sfile) != expected:
        raise ValueError("bad_session_file")
    if os.path.dirname(os.path.normpath(sfile)) != root:
        raise ValueError("bad_session_file")
    return expected


def _parse_jsonl(raw):
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("invalid_utf8")
    if not text.endswith("\n"):
        raise ValueError("incomplete_jsonl")
    rows = []
    for line in text.split("\n")[:-1]:
        if len(line.encode("utf-8")) > MAX_LINE:
            raise ValueError("line_too_large")
        if not line:
            raise ValueError("empty_line")
        try:
            row = _strict_json(line)
        except (ValueError, json.JSONDecodeError):
            raise ValueError("invalid_jsonl") from None
        if not isinstance(row, dict):
            raise ValueError("bad_row")
        rows.append(row)
    return rows


def _content_blocks(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return content
    raise ValueError("bad_content")


def _extract_text(content):
    parts = []
    for block in _content_blocks(content):
        if not isinstance(block, dict):
            raise ValueError("bad_block")
        if block.get("type") in ("text", "output_text"):
            t = block.get("text")
            if not isinstance(t, str):
                raise ValueError("bad_text")
            parts.append(t)
    return "".join(parts)


def _extract_calls(content):
    calls = []
    for block in _content_blocks(content):
        if not isinstance(block, dict):
            raise ValueError("bad_block")
        if block.get("type") in ("toolCall", "tool_use"):
            cid, name = block.get("id"), block.get("name")
            args = block.get("arguments", block.get("input"))
            if not isinstance(cid, str) or not isinstance(name, str):
                raise ValueError("bad_call")
            calls.append({"id": cid, "name": name, "arguments": args})
    return calls


def _valid_usage(usage):
    if not isinstance(usage, dict):
        return None
    keys = ("input", "output", "cacheRead", "cacheWrite")
    vals = []
    for k in keys:
        v = usage.get(k)
        if type(v) is not int or v < 0:
            return None
        vals.append(v)
    total = usage.get("totalTokens")
    if type(total) is not int or total != sum(vals):
        return None
    return {"input": vals[0], "output": vals[1],
            "cacheRead": vals[2], "cacheWrite": vals[3], "totalTokens": total}


def _tool_success(name, result):
    if result.get("isError") is not False:
        return False
    details = result.get("details")
    if name == PIXEL_TOOL:
        if not isinstance(details, dict):
            return False
        if details.get("status") != "completed":
            return False
        code = details.get("exitCode")
        if type(code) is not int or code != 0:
            return False
        return True
    if name in GENERIC_TOOLS:
        if isinstance(details, dict) and "error" in details:
            return False
        if isinstance(details, dict) and "status" in details:
            if details["status"] not in TOOL_OK_STATUS:
                return False
        return True
    return False


class NativeReceiptWitness:
    def __init__(self, sessions_dir, manifest, chat_id, request_id, *, allow_existing_session=False):
        if type(allow_existing_session) is not bool:
            raise ValueError("invalid_allow_existing_session")
        self._errors = []
        try:
            self._sessions_dir = _abs_clean(sessions_dir)
            self._manifest = _strict_json(probe.canonical_json(pixel_adapter.validate_manifest(manifest)))
            messages = self._manifest["task"]["messages"]
            if len(messages) != 1 or messages[0]["role"] != "user":
                raise ValueError("unsupported_task_messages")
        except ValueError:
            raise
        except Exception:
            raise ValueError("witness_preflight_failed") from None
        if not isinstance(chat_id, str) or not pixel_adapter.SAFE_ID.fullmatch(chat_id):
            raise ValueError("invalid_chat_id")
        if not isinstance(request_id, str) or not pixel_adapter.SAFE_ID.fullmatch(request_id):
            raise ValueError("invalid_request_id")
        self._chat_id = chat_id
        self._request_id = request_id
        self._key = _native_key(chat_id)
        self._allow_existing_session = allow_existing_session
        self._before_utc = datetime.now(timezone.utc)
        try:
            self._before = self._capture_before()
        except ValueError:
            raise
        except Exception:
            raise ValueError("witness_preflight_failed") from None
        self._finished = False

    def _capture_before(self):
        root = self._sessions_dir
        st = os.lstat(root)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise ValueError("bad_sessions_dir")
        if hasattr(os, "getuid") and st.st_uid != os.getuid():
            raise ValueError("bad_sessions_dir")
        path = os.path.join(root, "sessions.json")
        data, snap = _load_sessions(path)
        if self._key not in data:
            return {"root": root, "root_dev": st.st_dev,
                    "root_ino": st.st_ino, "sessions_snap": snap,
                    "existing": None}
        if not self._allow_existing_session:
            raise ValueError("before_key_present")
        try:
            sid, sfile = _entry_identity(data[self._key])
            canonical = _canonical_session_file(root, sid, sfile)
        except ValueError:
            raise ValueError("before_entry_invalid") from None
        try:
            raw, jsonl_snap = _read_stable(canonical)
        except ValueError:
            raise ValueError("before_transcript_invalid") from None
        try:
            rows = _parse_jsonl(raw)
        except ValueError:
            raise ValueError("before_transcript_invalid") from None
        if not rows:
            raise ValueError("before_transcript_empty")
        # Metadata may follow the last message. A textual stop is only a
        # transcript boundary; the coordinator must establish runtime idleness.
        messages = [row["message"] for row in rows if "message" in row]
        msg = messages[-1] if messages else None
        if not isinstance(msg, dict):
            raise ValueError("before_transcript_invalid")
        if msg.get("role") != "assistant" or msg.get("stopReason") != "stop":
            raise ValueError("before_not_terminal")
        try:
            text = _extract_text(msg.get("content"))
            calls = _extract_calls(msg.get("content"))
        except ValueError:
            raise ValueError("before_transcript_invalid") from None
        if not text or calls:
            raise ValueError("before_not_terminal")
        # Bind the index on both sides of reading the existing transcript.
        before2, _ = _load_sessions(path)
        if self._key not in before2 or _entry_identity(before2[self._key]) != (sid, sfile):
            raise ValueError("before_entry_changed")
        return {"root": root, "root_dev": st.st_dev,
                "root_ino": st.st_ino, "sessions_snap": snap,
                "existing": {"sid": sid, "sfile": sfile,
                             "canonical": canonical, "raw": raw,
                             "snap": jsonl_snap}}

    def finish(self, api_record):
        if self._finished:
            return self._unproven(["already_finished"])
        self._finished = True
        try:
            return self._finish(api_record)
        except Exception:
            return self._unproven(self._errors or ["internal_error"])

    def _unproven(self, errors):
        return {
            "schema_version": 1,
            "chat_id": self._chat_id,
            "request_id": self._request_id,
            "task_id": self._manifest["task"]["id"],
            "task_sha256": _sha(probe.canonical_json(self._manifest["task"])),
            "native_prompt_sha256": None,
            "binding_proven": False,
            "terminal_match": False,
            "answer_quality_pass": False,
            "tool_execution_proven": None,
            "model_calls_observed": None,
            "model_calls_completed": None,
            "interim_stop_count": None,
            "interim_stops": None,
            "usage": None,
            "quality_pass": False,
            "success": False,
            "owned_cleanup_proven": None,
            "errors": sorted(set(errors)),
        }

    def _finish(self, api_record):
        errors = []
        after_utc = datetime.now(timezone.utc)
        if after_utc < self._before_utc:
            return self._unproven(["clock_regression"])
        root = self._before["root"]
        try:
            _abs_clean(root)
            root_st = os.lstat(root)
        except OSError:
            return self._unproven(["root_changed"])
        if (root_st.st_dev, root_st.st_ino) != (self._before["root_dev"], self._before["root_ino"]):
            return self._unproven(["root_changed"])
        path = os.path.join(root, "sessions.json")
        try:
            after, after_snap = _load_sessions(path)
        except ValueError as exc:
            return self._unproven([str(exc)])
        if self._key not in after:
            errors.append("after_key_absent")
            return self._unproven(errors)
        try:
            sid, sfile = _entry_identity(after[self._key])
            canonical = _canonical_session_file(root, sid, sfile)
        except ValueError as exc:
            errors.append(str(exc))
            return self._unproven(errors)
        existing = self._before.get("existing")
        if existing is not None:
            if (sid, sfile) != (existing["sid"], existing["sfile"]):
                return self._unproven(["session_identity_changed"])
            if canonical != existing["canonical"]:
                return self._unproven(["session_identity_changed"])
        try:
            raw, jsonl_snap = _read_stable(canonical)
        except ValueError as exc:
            errors.append(str(exc))
            return self._unproven(errors)
        if existing is not None:
            if (jsonl_snap[0], jsonl_snap[1]) != (existing["snap"][0], existing["snap"][1]):
                return self._unproven(["session_identity_changed"])
            prefix = existing["raw"]
            if len(raw) < len(prefix) or raw[:len(prefix)] != prefix:
                return self._unproven(["original_prefix_changed"])
            suffix = raw[len(prefix):]
            if not suffix:
                return self._unproven(["no_append"])
            if not suffix.endswith(b"\n"):
                return self._unproven(["partial_suffix"])
            try:
                appended = _parse_jsonl(suffix)
            except ValueError as exc:
                errors.append(str(exc))
                return self._unproven(errors)
            if not appended:
                return self._unproven(["no_append"])
            # The byte-identical prefix was parsed at capture time and ended
            # on a record boundary. Only the new turn contributes evidence.
            rows = appended
        else:
            try:
                rows = _parse_jsonl(raw)
            except ValueError as exc:
                errors.append(str(exc))
                return self._unproven(errors)
        # Re-read expected entry after JSONL read.
        try:
            after2, after2_snap = _load_sessions(path)
            sid2, sfile2 = _entry_identity(after2.get(self._key))
            if (sid2, sfile2) != (sid, sfile):
                errors.append("entry_changed")
                return self._unproven(errors)
        except ValueError as exc:
            errors.append(str(exc))
            return self._unproven(errors)
        try:
            post_raw, post_snap = _read_stable(canonical)
        except ValueError as exc:
            errors.append(str(exc))
            return self._unproven(errors)
        if post_snap != jsonl_snap or post_raw != raw:
            errors.append("jsonl_changed")
            return self._unproven(errors)
        if existing is not None:
            if (post_snap[0], post_snap[1]) != (existing["snap"][0], existing["snap"][1]):
                return self._unproven(["session_identity_changed"])
            if post_raw[:len(existing["raw"])] != existing["raw"]:
                return self._unproven(["original_prefix_changed"])

        task = self._manifest["task"]
        task_sha = _sha(probe.canonical_json(task))
        user_msgs = [m for m in task["messages"] if m.get("role") == "user"]
        if len(user_msgs) != 1 or len(task["messages"]) != 1:
            errors.append("unsupported_task_messages")
            return self._unproven(errors)
        expected_prompt = user_msgs[0]["content"]
        required = list(task.get("required_tools", []))

        # Walk messages.
        calls = {}
        results = {}
        model_observed = 0
        model_completed = 0
        usage_sum = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 0}
        usage_unknown = False
        aborted = False
        user_seen = 0
        last_message = None
        last_ts = None
        native_prompt_sha = None
        pending_calls = []
        interim_stops = []
        for row in rows:
            if "message" not in row:
                continue
            msg = row["message"]
            if not isinstance(msg, dict):
                errors.append("bad_message")
                return self._unproven(errors)
            try:
                ts = _parse_utc(row.get("timestamp"))
            except ValueError:
                errors.append("bad_timestamp")
                return self._unproven(errors)
            if last_ts is not None and ts < last_ts:
                errors.append("clock_regression")
                return self._unproven(errors)
            last_ts = ts
            if (self._before_utc - ts).total_seconds() > 2:
                errors.append("row_before_window")
                return self._unproven(errors)
            if (ts - after_utc).total_seconds() > 2:
                errors.append("row_after_window")
                return self._unproven(errors)
            role = msg.get("role")
            if role != "user" and user_seen != 1:
                return self._unproven(["user_not_first"])
            if last_message is not None and last_message.get("role") == "assistant" and last_message.get("stopReason") == "stop":
                # A model stop is not necessarily the owner request's terminal
                # result: ODS may continue an assistant that omitted a required
                # tool. Only an assistant can continue a complete textual stop;
                # final binding still uses the last row and retained API hash.
                if role != "assistant" or pending_calls or _extract_calls(last_message.get("content")):
                    return self._unproven(["message_after_final"])
                interim_text = _extract_text(last_message.get("content"))
                if not interim_text:
                    return self._unproven(["empty_interim_stop"])
                interim_stops.append({"model_call_index": model_observed,
                                      "answer_sha256": _sha(interim_text), "answer_chars": len(interim_text)})
            if role == "user":
                user_seen += 1
                if user_seen > 1:
                    errors.append("unsupported_native_continuation")
                    return self._unproven(errors)
                if last_message is not None:
                    errors.append("user_not_first")
                    return self._unproven(errors)
                text = _extract_text(msg.get("content"))
                if text == expected_prompt:
                    pass
                elif text.startswith(expected_prompt + DELIVERY_NOTE):
                    pass
                else:
                    errors.append("prompt_mismatch")
                    return self._unproven(errors)
                native_prompt_sha = _sha(text)
                last_message = msg
            elif role == "assistant":
                model_observed += 1
                stop = msg.get("stopReason")
                if stop in ("error", "aborted"):
                    aborted = True
                    errors.append("assistant_incomplete")
                call_list = _extract_calls(msg.get("content"))
                for c in call_list:
                    if c["id"] in calls:
                        errors.append("duplicate_call")
                        return self._unproven(errors)
                    calls[c["id"]] = c
                    pending_calls.append(c["id"])
                usage = _valid_usage(msg.get("usage"))
                if usage is None or stop in ("error", "aborted"):
                    usage_unknown = True
                else:
                    model_completed += 1
                    for k in usage_sum:
                        usage_sum[k] += usage[k]
                last_message = msg
            elif role == "toolResult":
                cid = msg.get("toolCallId")
                if not isinstance(cid, str):
                    errors.append("bad_result")
                    return self._unproven(errors)
                if cid in results:
                    errors.append("duplicate_result")
                    return self._unproven(errors)
                if cid not in calls:
                    errors.append("result_before_call")
                    return self._unproven(errors)
                if msg.get("toolName") != calls[cid]["name"]:
                    return self._unproven(["tool_name_mismatch"])
                if cid not in pending_calls:
                    errors.append("result_after_consumed")
                    return self._unproven(errors)
                pending_calls.remove(cid)
                results[cid] = msg
                last_message = msg
            else:
                errors.append("unknown_role")
                return self._unproven(errors)

        if user_seen != 1:
            errors.append("missing_user")
            return self._unproven(errors)
        if pending_calls:
            errors.append("pending_call")
            return self._unproven(errors)
        for cid in results:
            if cid not in calls:
                errors.append("unmatched_result")
                return self._unproven(errors)

        # Terminal check.
        terminal_match = False
        answer_sha = None
        final_text = None
        if last_message is not None and last_message.get("role") == "assistant":
            if last_message.get("stopReason") == "stop" and not _extract_calls(last_message.get("content")):
                final_text = _extract_text(last_message.get("content"))
                answer_sha = _sha(final_text)
                terminal_match = bool(final_text)

        # Tool proof.
        tool_proven = None
        if required:
            tool_proven = True
            for name in required:
                if name not in GENERIC_TOOLS and name != PIXEL_TOOL:
                    errors.append("unsupported_tool_receipt")
                ok = False
                for cid, call in calls.items():
                    if call["name"] != name:
                        continue
                    if _tool_success(name, results[cid]):
                        ok = True
                        break
                if not ok:
                    tool_proven = False
                    errors.append("tool_unproven")
                    break

        # API record validation.
        api_ok = isinstance(api_record, dict)
        if api_ok:
            if api_record.get("chat_id") != self._chat_id:
                api_ok = False
            if api_record.get("request_id") != self._request_id:
                api_ok = False
            if api_record.get("task_sha256") != task_sha:
                api_ok = False
            if api_record.get("answer_sha256") != answer_sha:
                api_ok = False
            if api_record.get("result_state") != "complete":
                api_ok = False
        if not api_ok:
            errors.append("api_record_mismatch")

        answer_quality = False
        if api_ok and final_text is not None:
            try:
                answer_quality = probe.validate_answer(task["validator"], final_text)[0]
            except Exception:
                answer_quality = False

        binding_proven = bool(terminal_match and api_ok and not aborted)
        quality = bool(binding_proven and answer_quality and (tool_proven is not False))
        http_status = api_record.get("http_status") if isinstance(api_record, dict) else None
        censored = api_record.get("censored") if isinstance(api_record, dict) else None
        activity = api_record.get("activity") if isinstance(api_record, dict) else None
        success = bool(
            quality
            and not aborted
            and censored is False
            and activity == "terminal"
            and type(http_status) is int
            and http_status == 200
        )
        return {
            "schema_version": 1,
            "chat_id": self._chat_id,
            "request_id": self._request_id,
            "task_id": task["id"],
            "task_sha256": task_sha,
            "native_prompt_sha256": native_prompt_sha,
            "binding_proven": binding_proven,
            "terminal_match": terminal_match,
            "answer_quality_pass": answer_quality,
            "tool_execution_proven": tool_proven,
            "model_calls_observed": model_observed,
            "model_calls_completed": model_completed,
            "interim_stop_count": len(interim_stops),
            "interim_stops": interim_stops,
            "usage": None if usage_unknown else usage_sum,
            "quality_pass": quality,
            "success": success,
            "owned_cleanup_proven": None,
            "errors": sorted(set(errors)),
        }
