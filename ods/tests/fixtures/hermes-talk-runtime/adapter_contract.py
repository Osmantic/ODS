import os
import pathlib
import json
import tempfile
import subprocess
import sys
import runpy
from unittest.mock import patch

os.environ["HERMES_HOME"] = tempfile.mkdtemp(prefix="ods-adapter-native-")
import tui_gateway.server as S


def rpc(method, params):
    return S.handle_request(
        {"jsonrpc": "2.0", "id": "proof", "method": method, "params": params}
    )


caps = rpc("ods.talk.capabilities", {})
assert caps["result"] == {"manual_resume": True, "version": "1"}, caps
assert "error" in rpc("ods.talk.capabilities", {"unexpected": True})
for value in ["true", 1, None]:
    response = rpc(
        "session.resume", {"session_id": "absent", "ods_talk_manual_resume": value}
    )
    assert response.get("error", {}).get("code") == 4000, (value, response)
for flag in ["lazy", "defer_history", "eager_build", "omit_messages"]:
    for value in [True, "true", 1, None]:
        assert (
            rpc(
                "session.resume",
                {"session_id": "absent", "ods_talk_manual_resume": True, flag: value},
            )["error"]["code"]
            == 4000
        )
cases = {}
for manual in [False, True]:
    key = "stored-" + str(manual)
    home = pathlib.Path(tempfile.mkdtemp(dir=os.environ["HERMES_HOME"]))
    params = {"session_id": key, "ods_talk_manual_resume": manual}
    ctx = S._Resume("proof", params, key)
    ctx.found = {}
    ctx.profile_home = str(home)
    history = [
        {"role": "user", "content": "prior owner message"},
        {"role": "assistant", "content": "prior assistant answer"},
    ]
    record = ctx.record("web", "/tmp", history, {})
    assert bool(record.get("_ods_talk_manual_resume")) == manual
    assert record.get("history") == history
    S.record_turn_start(home, key, "diagnostic only; never execute")
    marker = S.read_turn_marker(home, key)
    scheduled = []

    def schedule(fn, *, name, session=None):
        scheduled.append(name)
        return object()

    with (
        patch.object(
            S,
            "_load_cfg",
            return_value={"desktop": {"auto_continue": {"enabled": True}}},
        ),
        patch.object(S, "_start_session_work", side_effect=schedule),
        patch.object(
            S, "_run_prompt_submit", side_effect=AssertionError("real turn forbidden")
        ) as submit,
    ):
        desc = S._maybe_schedule_auto_continue("runtime-" + str(manual), record, key)
        assert len(scheduled) == (0 if manual else 1)
        assert (desc is None) == manual
        assert S.read_turn_marker(home, key) == marker
        assert submit.call_count == 0
    cases[str(manual)] = {
        "scheduled": len(scheduled),
        "historyPreserved": True,
        "markerPreserved": True,
    }
before = S._methods["session.resume"]
namespace = runpy.run_path("/opt/ods-hermes-runtime/ods_talk_resume.py")
namespace["_HASHES"]["tui_gateway/server.py"] = "0" * 64
try:
    namespace["install"](S)
except RuntimeError:
    pass
else:
    raise AssertionError("unsupported source hash accepted")
assert S._methods["session.resume"] is before
child = subprocess.run(
    [
        sys.executable,
        "-c",
        'import json; import tui_gateway.server as S; print(json.dumps(S.handle_request({"jsonrpc":"2.0","id":"child","method":"ods.talk.capabilities","params":{}})))',
    ],
    text=True,
    capture_output=True,
    timeout=30,
)
assert child.returncode == 0, child.stderr
child_caps = json.loads((child.stdout or child.stderr).strip())
assert child_caps["result"] == caps["result"]
print(
    json.dumps(
        {
            "passed": True,
            "capabilities": caps["result"],
            "cases": cases,
            "strictParams": True,
            "shortcutRefusal": True,
            "hashMismatchLeavesOriginal": True,
            "childProcessHook": True,
            "scope": "actual pinned record/scheduler/schema/import hook; full session.resume history restoration not yet tested",
        }
    )
)
