import os
import pathlib
import tempfile
import json
from unittest.mock import patch

os.environ["HERMES_HOME"] = tempfile.mkdtemp(prefix="ods-cold-resume-")
import tui_gateway.server as S

db = S._get_db()
assert db is not None
key = "ods-cold-resume-proof"
parent = "ods-cold-resume-parent"
db.create_session(parent, source="web", model="native-parent-model")
db.append_message(parent, "user", "The venue is Birch.")
db.append_message(parent, "assistant", "I will remember Birch.")
db.end_session(parent, "compression")
db.create_session(
    key,
    source="web",
    model="native-test-model",
    model_config={"model": "native-test-model", "provider": "custom"},
    parent_session_id=parent,
)
db.append_message(
    key,
    "user",
    "Earlier context: venue Birch. The meeting room is Cedar and we have seven attendees.",
)
db.append_message(key, "assistant", "Cedar, seven attendees.")
db.end_session(key, "diagnostic")
home = pathlib.Path(os.environ["HERMES_HOME"])
S.record_turn_start(home, key, "interrupted diagnostic; never execute")
before = S.read_turn_marker(home, key)
scheduled = []


def schedule(fn, *, name, session=None):
    scheduled.append(name)
    return object()


with (
    patch.object(S, "_schedule_agent_build", return_value=None),
    patch.object(S, "_schedule_session_cap_enforcement", return_value=None),
    patch.object(S, "_start_session_work", side_effect=schedule),
    patch.object(
        S, "_run_prompt_submit", side_effect=AssertionError("real turn forbidden")
    ) as submit,
):
    result = S.handle_request(
        {
            "jsonrpc": "2.0",
            "id": "cold",
            "method": "session.resume",
            "params": {"session_id": key, "ods_talk_manual_resume": True},
        }
    )
    assert "error" not in result, result
    data = result["result"]
    runtime = data["session_id"]
    record = S._sessions[runtime]
    assert runtime != key
    assert (
        data.get("stored_session_id", data.get("session_key", data.get("resumed")))
        == key
    ), data
    assert data["ods_recovery_pending"] is True
    assert not scheduled and submit.call_count == 0
    assert S.read_turn_marker(home, key) == before
    text = json.dumps(record["history"])
    assert "Cedar" in text and "seven attendees" in text
    assert not record.get("lazy") and not record.get("resume_hydrating")
    assert record.get("_ods_talk_manual_resume") is True
    assert "I will remember Birch." in json.dumps(data["messages"]), data["messages"]
    assert record["model_override"]["model"] == "native-test-model"
    from tui_gateway.turn_marker import _marker_path

    marker_path = _marker_path(home)
    marker_path.write_text("{broken", encoding="utf-8")
    corrupt = S.handle_request(
        {
            "jsonrpc": "2.0",
            "id": "corrupt",
            "method": "session.resume",
            "params": {"session_id": key, "ods_talk_manual_resume": True},
        }
    )
    assert corrupt.get("error", {}).get("code") == 5000, corrupt
    assert marker_path.read_text(encoding="utf-8") == "{broken"
    assert submit.call_count == 0 and not scheduled
    out = {
        "passed": True,
        "runtimeId": runtime,
        "storedId": key,
        "history": record["history"],
        "modelOverride": record.get("model_override"),
        "runtimeOverrides": record.get("resume_runtime_overrides"),
        "ancestorDisplayPreserved": True,
        "recoveryPending": data["ods_recovery_pending"],
        "markerPreserved": True,
        "unreadableMarkerRefused": True,
        "scheduled": scheduled,
        "scope": "actual session.resume full cold branch, real isolated SQLite with parent/child and marker; model build and work scheduler intercepted",
    }
    print(json.dumps(out))
