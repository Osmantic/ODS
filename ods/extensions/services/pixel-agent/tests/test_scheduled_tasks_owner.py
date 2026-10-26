"""Native task effects, durable uncertainty and isolation, without a scheduler clone."""
import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("The owner adapter requires POSIX custody and file locks")

import copy
import json
from pathlib import Path
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / "bin"), str(Path(__file__).resolve().parents[1] / "host")]
from pixel_schedules import ScheduleError, native_task, task_revision  # noqa: E402
from scheduled_tasks_owner import NativeTasks  # noqa: E402


def proposal():
    return {"action": "create", "requestId": str(uuid.uuid4()), "name": "Daily briefing", "task": "Summarize my day.",
            "schedule": {"kind": "at", "at": "2030-01-01T09:00:00Z"}}


@pytest.fixture
def owner(tmp_path):
    """A controlled native RPC boundary. It records every effect and exposes native-shaped data."""
    jobs, calls, runs = {}, [], []
    def call(method, params):
        calls.append((method, copy.deepcopy(params)))
        if method == "cron.add":
            job = dict(copy.deepcopy(params), id=str(uuid.uuid4()), createdAtMs=1000, updatedAtMs=1000, state={})
            jobs[job["id"]] = job
            return copy.deepcopy(job)
        if method == "cron.list":
            selected = [copy.deepcopy(job) for job in jobs.values() if job["agentId"] == params["agentId"]]
            offset, limit = params["offset"], params["limit"]
            items = selected[offset:offset + limit]
            more = offset + len(items) < len(selected)
            return {"jobs": items, "hasMore": more, "nextOffset": offset + len(items) if more else None}
        if method == "cron.get":
            if params["id"] not in jobs:
                raise ScheduleError("task-not-found")
            return copy.deepcopy(jobs[params["id"]])
        if method == "cron.remove":
            return {"ok": True, "removed": jobs.pop(params["id"], None) is not None}
        if method == "cron.update":
            jobs[params["id"]].update(params["patch"], updatedAtMs=2000)
            return copy.deepcopy(jobs[params["id"]])
        if method == "cron.runs":
            return {"entries": [item for item in runs if item["jobId"] == params["id"]], "hasMore": False, "nextOffset": None}
        raise AssertionError(method)
    api = NativeTasks(tmp_path / "state", call, clock=lambda: 1793037927)
    return api, jobs, calls, runs, call


def test_creation_is_durable_before_dispatch_and_an_identical_request_is_not_replayed(owner):
    api, jobs, calls, _runs, call = owner
    request = proposal()
    def inspect(method, params):
        if method == "cron.add":
            receipt = api.store.read()["requests"][request["requestId"]]
            assert receipt["state"] == "pending" and receipt["jobId"] is None
        return call(method, params)
    api.call = inspect
    first = api.operate(request)
    assert first["outcome"] == "applied" and len(jobs) == 1
    assert api.operate(request)["task"]["id"] == first["task"]["id"]
    assert [name for name, _ in calls].count("cron.add") == 1
    with pytest.raises(ScheduleError, match="task-request-changed"):
        api.operate(dict(request, task="A different task"))


@pytest.mark.parametrize("accepted", [False, True], ids=["request-lost", "reply-lost"])
def test_unconfirmed_creation_is_reconciled_without_sending_add_again(owner, accepted):
    api, jobs, calls, _runs, call = owner
    def ambiguous(method, params):
        if method == "cron.add":
            if accepted:
                call(method, params)
            else:
                calls.append((method, copy.deepcopy(params)))
            raise ScheduleError("scheduler-unavailable")
        return call(method, params)
    api.call = ambiguous
    request = proposal()
    assert api.operate(request)["outcome"] == "unconfirmed"
    expected = "unchanged" if accepted else "unconfirmed"
    assert api.operate(request)["outcome"] == expected
    assert [method for method, _ in calls].count("cron.add") == 1
    assert len(jobs) == int(accepted)
    api.operate({"action": "dismiss-create", "requestId": request["requestId"]})
    assert api.operate({"action": "list", "offset": 0})["pendingCount"] == 0
    api.operate(request)
    assert [method for method, _ in calls].count("cron.add") == 1


def test_an_incomplete_search_never_claims_a_unique_creation_match(owner):
    api, _jobs, _calls, _runs, _call = owner
    request = proposal()
    matching = dict(native_task(request), id="fixture-job", createdAtMs=1000, updatedAtMs=1000, state={})
    seen = []
    def partial(method, params):
        if method == "cron.add":
            raise ScheduleError("scheduler-unavailable")
        assert method == "cron.list"
        seen.append(params["offset"])
        return {"jobs": [matching] if params["offset"] == 0 else [], "hasMore": True, "nextOffset": params["offset"] + 200}
    api.call = partial
    assert api.operate(request)["outcome"] == "unconfirmed"
    assert api.operate(request)["outcome"] == "unconfirmed"
    assert len(seen) == 10


def test_recovery_never_adopts_a_task_with_different_content(owner):
    api, jobs, calls, _runs, call = owner
    def accepted_without_reply(method, params):
        result = call(method, params)
        if method == "cron.add":
            raise ScheduleError("scheduler-unavailable")
        return result
    api.call = accepted_without_reply
    request = proposal()
    assert api.operate(request)["outcome"] == "unconfirmed"
    next(iter(jobs.values()))["payload"]["message"] = "Changed task content"
    assert api.operate(request)["outcome"] == "unconfirmed"
    assert [method for method, _ in calls].count("cron.add") == 1


def test_foreign_agents_and_command_jobs_cannot_be_changed_or_read_through_this_surface(owner):
    api, jobs, calls, _runs, _call = owner
    created = api.operate(proposal())["task"]
    job = jobs[created["id"]]
    for changes in ({"agentId": "unrelated"}, {"payload": {"kind": "command", "argv": ["echo", "fixture"]}}):
        jobs[created["id"]] = dict(job, **changes)
        for action in ({"action": "set-enabled", "id": created["id"], "revision": created["revision"], "enabled": False},
                       {"action": "remove", "id": created["id"], "revision": created["revision"]},
                       {"action": "runs", "id": created["id"], "offset": 0}):
            with pytest.raises(ScheduleError, match="task-not-found"):
                api.operate(action)
    assert not any(method in ("cron.update", "cron.remove", "cron.runs") for method, _ in calls)


def test_stale_edits_are_refused_but_pausing_an_already_paused_task_is_harmless(owner):
    api, jobs, calls, _runs, _call = owner
    created = api.operate(proposal())["task"]
    jobs[created["id"]]["payload"]["message"] = "Changed in another client"
    request = {"action": "set-enabled", "id": created["id"], "revision": created["revision"], "enabled": False}
    with pytest.raises(ScheduleError, match="task-changed"):
        api.operate(request)
    jobs[created["id"]]["enabled"] = False
    assert api.operate(request)["outcome"] == "unchanged"
    assert not any(method == "cron.update" for method, _ in calls)


def test_a_lost_removal_reply_keeps_a_checkable_receipt_and_native_results(owner):
    api, _jobs, _calls, runs, call = owner
    created = api.operate(proposal())["task"]
    runs.append({"jobId": created["id"], "ts": 1000, "status": "ok", "summary": "A saved briefing", "durationMs": 50})
    def lose_reply(method, params):
        result = call(method, params)
        if method == "cron.remove":
            raise ScheduleError("scheduler-unavailable")
        return result
    api.call = lose_reply
    request = {"action": "remove", "id": created["id"], "revision": created["revision"]}
    assert api.operate(request)["outcome"] == "unconfirmed"
    assert api.operate({"action": "list", "offset": 0})["pendingRemovals"][0]["id"] == created["id"]
    assert api.operate(request)["task"]["removed"] is True
    assert api.operate({"action": "archived", "offset": 0})["items"][0]["id"] == created["id"]
    assert api.operate({"action": "runs", "id": created["id"], "offset": 0})["items"][0]["summary"] == "A saved briefing"


def test_an_update_reply_for_another_task_is_not_a_success(owner):
    api, jobs, _calls, _runs, call = owner
    created = api.operate(proposal())["task"]
    def wrong_receipt(method, params):
        result = call(method, params)
        if method == "cron.update":
            result["id"] = "another-job"
        return result
    api.call = wrong_receipt
    with pytest.raises(ScheduleError, match="invalid-scheduler-response"):
        api.operate({"action": "set-enabled", "id": created["id"], "revision": task_revision(jobs[created["id"]]), "enabled": False})


def test_private_state_is_not_reset_when_it_is_corrupt_or_readable_by_other_users(owner):
    api, _jobs, calls, _runs, _call = owner
    api.operate(proposal())
    calls.clear()
    original = api.store.path.read_bytes()
    api.store.path.chmod(0o644)
    with pytest.raises(ScheduleError, match="unsafe-schedule-state"):
        api.operate({"action": "list", "offset": 0})
    api.store.path.chmod(0o600)
    data = json.loads(original)
    data["version"] = True
    api.store.path.write_text(json.dumps(data))
    with pytest.raises(ScheduleError, match="invalid-schedule-state"):
        api.operate({"action": "list", "offset": 0})
    assert calls == []
