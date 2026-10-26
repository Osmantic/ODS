"""Public task inputs cannot choose a command, model, gateway or delivery route."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "bin"))
import pixel_schedules as tasks  # noqa: E402


def proposal():
    return {"action": "create", "requestId": "6999783b-9f8e-490a-bc90-4feac1f3c7d3",
            "name": "Morning briefing", "task": "Summarize my meetings and important unread mail.",
            "schedule": {"kind": "recurring", "frequency": "weekdays", "time": "09:15",
                         "timeZone": "America/Sao_Paulo", "weekday": None}}


class TaskContractTests(unittest.TestCase):
    def test_the_adapter_tracks_the_openclaw_pinned_by_ods(self):
        manifest = json.loads((ROOT / "vendor/pixel/RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(tasks.OPENCLAW_VERSION, manifest["openclaw"])

    def test_weekday_briefing_compiles_to_the_native_scheduler_without_outbound_delivery(self):
        request = proposal()
        before = copy.deepcopy(request)
        job = tasks.native_task(request)
        self.assertEqual(job["schedule"], {"kind": "cron", "expr": "15 9 * * 1-5", "tz": "America/Sao_Paulo", "staggerMs": 0})
        self.assertEqual(job["payload"], {"kind": "agentTurn", "message": request["task"]})
        self.assertEqual(job["agentId"], "pixel")
        self.assertEqual(job["sessionTarget"], "isolated")
        self.assertEqual(job["delivery"], {"mode": "none"})
        self.assertIs(job["deleteAfterRun"], False, "one-time task results must remain discoverable too")
        self.assertEqual(request, before)

    def test_the_owner_can_choose_daily_weekdays_weekly_or_one_instant(self):
        for frequency, weekday, suffix in (("daily", None, "*"), ("weekdays", None, "1-5"), ("weekly", 0, "0")):
            value = dict(proposal()["schedule"], frequency=frequency, weekday=weekday)
            self.assertEqual(tasks.schedule(value)["expr"], "15 9 * * " + suffix)
        self.assertEqual(tasks.schedule({"kind": "at", "at": "2030-01-01T09:00:00-03:00"}),
                         {"kind": "at", "at": "2030-01-01T12:00:00Z"})

    def test_task_creation_cannot_select_a_command_agent_provider_or_external_delivery(self):
        for key, value in {"command": "echo fixture", "agentId": "other", "model": "remote/provider",
                           "delivery": {"mode": "webhook", "to": "https://example.invalid"},
                           "gatewayPort": 1234, "packageDir": "/tmp/other", "deleteAfterRun": True}.items():
            with self.subTest(field=key), self.assertRaises(tasks.ScheduleError):
                tasks.validate_request(dict(proposal(), **{key: value}))

    def test_invalid_and_ambiguous_time_inputs_are_refused_before_dispatch(self):
        for changes in ({"time": "24:00"}, {"time": "9:15"}, {"timeZone": "../etc"},
                        {"frequency": "weekly", "weekday": True}, {"weekday": 1}, {"frequency": "monthly"}):
            with self.subTest(changes=changes), self.assertRaises(tasks.ScheduleError):
                tasks.schedule(dict(proposal()["schedule"], **changes))
        with self.assertRaises(tasks.ScheduleError):
            tasks.schedule({"kind": "at", "at": "2030-01-01T12:00:00"})

    def test_native_jobs_belong_to_pixel_and_contain_agent_turns(self):
        job = dict(tasks.native_task(proposal()), id="fixture-job", updatedAtMs=1, createdAtMs=1)
        public = tasks.project_task(job)
        self.assertEqual(public["task"], proposal()["task"])
        self.assertNotIn("description", public)
        self.assertNotIn("payload", public)
        self.assertNotIn("token", public)
        for bad in (dict(job, agentId="other"), dict(job, payload={"kind": "command", "argv": ["echo", "fixture"]})):
            with self.assertRaises(tasks.ScheduleError):
                tasks.project_task(bad)

    def test_a_concurrent_edit_changes_the_revision_but_a_running_marker_does_not(self):
        job = dict(tasks.native_task(proposal()), id="fixture-job", updatedAtMs=1)
        original = tasks.task_revision(job)
        self.assertNotEqual(original, tasks.task_revision(dict(job, enabled=False)))
        self.assertNotEqual(original, tasks.task_revision(dict(job, payload={"kind": "agentTurn", "message": "Another task"})))
        self.assertEqual(original, tasks.task_revision(dict(job, state={"runningAtMs": 200})))

    def test_public_results_reject_private_fields_and_crossed_response_shapes(self):
        job = dict(tasks.native_task(proposal()), id="fixture-job", updatedAtMs=1, createdAtMs=1)
        public = tasks.project_task(job)
        valid = {"kind": "change", "outcome": "applied", "task": public}
        self.assertEqual(tasks.validate_response(valid), valid)
        for bad in (dict(valid, token="fixture-private"), dict(valid, task=dict(public, token="fixture-private")),
                    dict(valid, task=None), dict(valid, outcome="running")):
            with self.subTest(bad=bad), self.assertRaises(tasks.ScheduleError):
                tasks.validate_response(bad)


if __name__ == "__main__":
    unittest.main()
