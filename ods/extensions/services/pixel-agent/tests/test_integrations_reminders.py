import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires Unix sockets; run under Linux/WSL")

import datetime
import http.server
import pathlib
import tempfile
import threading
import unittest
import zoneinfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "host"))
import integrations_reminders as reminders  # noqa: E402
import integrations_service as service  # noqa: E402
from integrations_common import IntegrationError, Store, default_apps, local_zone  # noqa: E402

LISBON = datetime.timezone(datetime.timedelta(hours=0), "WET")
NOW = datetime.datetime(2026, 11, 3, 16, 20, 30, tzinfo=LISBON)


class DaylightSaving(unittest.TestCase):
    """In Lisbon summer time ends on 2026-10-25 at 02:00 (UTC+1 to UTC+0)."""
    ZONE = zoneinfo.ZoneInfo("Europe/Lisbon")
    BEFORE = datetime.datetime(2026, 10, 24, 23, 0, tzinfo=ZONE)  # UTC+1

    def test_a_clock_time_across_the_change_keeps_its_clock_time(self):
        due = reminders.due_at(self.BEFORE, at="09:00")
        self.assertEqual((due.day, due.hour, due.utcoffset()), (25, 9, datetime.timedelta(0)))
        self.assertEqual(due.astimezone(datetime.timezone.utc).hour, 9)

    def test_a_date_and_time_after_the_change_is_in_that_day_s_offset(self):
        due = reminders.due_at(self.BEFORE, at="2026-11-04T09:00")
        self.assertEqual(due.astimezone(datetime.timezone.utc), datetime.datetime(2026, 11, 4, 9, 0, tzinfo=datetime.timezone.utc))
        plan = reminders.plan(self.BEFORE, "", "Dentist", at="2026-11-04T09:00")
        self.assertEqual(plan["when"], "Wed 04 Nov 2026 at 09:00 WET")

    def test_minutes_from_now_are_counted_in_real_time(self):
        due = reminders.due_at(self.BEFORE, in_minutes=4 * 60)  # 23:00 + 4 h, across 02:00 → 01:00.
        utc = datetime.timezone.utc
        self.assertEqual(due.astimezone(utc) - self.BEFORE.astimezone(utc), datetime.timedelta(hours=4))
        self.assertEqual((due.hour, due.utcoffset()), (2, datetime.timedelta(0)))

    def test_the_host_zone_has_daylight_saving_rules(self):
        self.assertEqual(local_zone({"TZ": "Europe/Lisbon"}), self.ZONE)
        self.assertEqual(local_zone({"TZ": ":Asia/Tokyo"}), zoneinfo.ZoneInfo("Asia/Tokyo"))
        system = local_zone({}, "/usr/share/zoneinfo/Europe/Lisbon")
        self.assertEqual(datetime.datetime(2026, 7, 1, tzinfo=system).utcoffset(), datetime.timedelta(hours=1))
        self.assertEqual(datetime.datetime(2026, 12, 1, tzinfo=system).utcoffset(), datetime.timedelta(0))
        self.assertEqual(local_zone({}, "/nonexistent/localtime"), datetime.timezone.utc)


class DueTimes(unittest.TestCase):
    def test_in_minutes_counts_from_now(self):
        self.assertEqual(reminders.due_at(NOW, in_minutes=2), NOW + datetime.timedelta(minutes=2))
        self.assertEqual(reminders.due_at(NOW, in_minutes="90"), NOW + datetime.timedelta(minutes=90))
        self.assertEqual(reminders.due_at(NOW, in_minutes=1.5), NOW + datetime.timedelta(seconds=90))

    def test_a_time_of_day_is_its_next_occurrence(self):
        self.assertEqual(reminders.due_at(NOW, at="17:30"), NOW.replace(hour=17, minute=30, second=0))
        self.assertEqual(reminders.due_at(NOW, at="9:05"), (NOW + datetime.timedelta(days=1)).replace(hour=9, minute=5, second=0))
        # The current minute has begun, so it is tomorrow's.
        self.assertEqual(reminders.due_at(NOW, at="16:20").day, 4)

    def test_a_date_and_time_is_local_unless_it_gives_an_offset(self):
        self.assertEqual(reminders.due_at(NOW, at="2026-11-04T09:00"), datetime.datetime(2026, 11, 4, 9, 0, tzinfo=LISBON))
        due = reminders.due_at(NOW, at="2026-11-04T09:00+02:00")
        self.assertEqual(due.utcoffset(), datetime.timedelta(hours=2))
        self.assertEqual(due.astimezone(LISBON).hour, 7)
        self.assertEqual(reminders.due_at(NOW, at="2026-11-04T09:00Z").utcoffset(), datetime.timedelta(0))

    def test_unusable_times_are_refused_with_a_reason(self):
        cases = {
            ("", ""): "Say when",
            (None, None): "Say when",
            (5, "17:30"): "not both",
            (0, None): "1 minute to a year",
            (-3, None): "1 minute to a year",
            (600000, None): "1 minute to a year",
            ("soon", None): "Say when",
            (True, None): "Say when",
            (None, "25:00"): "not a time of day",
            (None, "tomorrow"): "is not a time",
            (None, "2026-11-03T08:00"): "already passed",
            (None, "2028-01-01T00:00"): "1 minute to a year",
        }
        for (in_minutes, at), reason in cases.items():
            with self.assertRaises(IntegrationError, msg=(in_minutes, at)) as caught:
                reminders.due_at(NOW, in_minutes, at)
            self.assertEqual(caught.exception.code, "invalid-reminder")
            self.assertIn(reason, caught.exception.message, (in_minutes, at))


class Plans(unittest.TestCase):
    def test_a_plan_carries_the_reminder_in_the_job(self):
        plan = reminders.plan(NOW, "", "  Stretch your legs  ", in_minutes=2)
        self.assertEqual(plan["name"], "Reminder: Stretch your legs")
        self.assertEqual(plan["description"], "Portal reminder: Stretch your legs")
        self.assertEqual(plan["atMs"], int((NOW + datetime.timedelta(minutes=2)).timestamp() * 1000))
        self.assertEqual(plan["when"], "Tue 03 Nov 2026 at 16:22 WET")
        self.assertEqual(plan["text"], "Stretch your legs")
        self.assertIn("Done", plan["runMessage"])
        self.assertTrue(reminders.is_reminder(plan["description"]))
        self.assertEqual(reminders.notice(plan["name"], plan["description"]), ("Reminder: Stretch your legs", "Stretch your legs"))

    def test_a_title_names_the_job_and_a_long_message_is_shortened_in_the_name_only(self):
        self.assertEqual(reminders.plan(NOW, " Call\nmum ", "Ask about Sunday", at="18:00")["name"], "Reminder: Call mum")
        long = "Take the bread out of the oven " * 5
        plan = reminders.plan(NOW, None, long, in_minutes=40)
        self.assertEqual(len(plan["name"]), len("Reminder: ") + reminders.MAX_TITLE)
        self.assertEqual(plan["text"], long.strip())

    def test_a_plan_needs_a_message_of_reasonable_length(self):
        for message, reason in (("", "Give the reminder text"), ("   ", "Give the reminder text"), (None, "Give the reminder text"),
                                ("x" * 1001, "under 1000")):
            with self.assertRaises(IntegrationError) as caught:
                reminders.plan(NOW, "", message, in_minutes=5)
            self.assertIn(reason, caught.exception.message)
        with self.assertRaises(IntegrationError):
            reminders.plan(NOW, "t" * 81, "Stretch", in_minutes=5)

    def test_only_portal_reminder_jobs_count_as_reminders(self):
        self.assertFalse(reminders.is_reminder("Daily news digest"))
        self.assertFalse(reminders.is_reminder(None))
        self.assertFalse(reminders.is_reminder("portal reminder: lower case"))
        self.assertEqual(reminders.notice("", "Portal reminder: Stretch"), ("Reminder", "Stretch"))
        self.assertEqual(reminders.notice("Reminder: Tea", "Portal reminder: "), ("Reminder: Tea", "Reminder: Tea"))


class FakeNtfy(http.server.BaseHTTPRequestHandler):
    sent = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        FakeNtfy.sent.append({"title": self.headers["Title"], "message": body, "priority": self.headers["Priority"]})
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_):
        pass


class ServiceReminders(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(pathlib.Path(temporary.name) / "state")
        self.service = service.Integrations(self.store)
        self.ntfy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeNtfy)
        threading.Thread(target=self.ntfy.serve_forever, daemon=True).start()
        self.addCleanup(self.ntfy.server_close)
        self.addCleanup(self.ntfy.shutdown)
        FakeNtfy.sent.clear()
        self.save(tasks="off")

    def save(self, *, tasks="off", notify=True):
        settings = {"notifications": {"enabled": notify, "url": f"http://127.0.0.1:{self.ntfy.server_address[1]}", "topic": "portal",
                                      "username": "", "notifyApprovals": False, "approvalLink": "", "scheduledTasks": tasks},
                    "notes": {"enabled": False, "vaultPath": "", "write": "ask"}, "mcp": {"servers": []}, "apps": default_apps()}
        revision = self.owner("get")["settings"]["revision"]
        self.owner("save", expectedRevision=revision, settings=settings, secrets={})

    def owner(self, action, **fields):
        return self.service.handle("owner", {"schemaVersion": 1, "action": action, **fields})

    def agent(self, action, **fields):
        return self.service.handle("agent", {"schemaVersion": 1, "action": action, **fields})

    def test_portal_plans_a_reminder_and_it_is_logged(self):
        before = datetime.datetime.now().astimezone()
        plan = self.agent("reminder-plan", title="", message="Stretch", inMinutes=2)["plan"]
        self.assertEqual(plan["description"], "Portal reminder: Stretch")
        self.assertAlmostEqual(plan["atMs"] / 1000, (before + datetime.timedelta(minutes=2)).timestamp(), delta=5)
        self.assertEqual(self.owner("get")["activity"][0]["action"], "reminder-set")
        self.assertEqual(FakeNtfy.sent, [], "planning sends nothing yet")

    def test_a_reminder_needs_notifications(self):
        self.save(notify=False)
        with self.assertRaises(IntegrationError) as caught:
            self.agent("reminder-plan", message="Stretch", inMinutes=2)
        self.assertEqual(caught.exception.code, "notifications-off")
        with self.assertRaises(IntegrationError) as caught:
            self.agent("reminder-plan", message="Stretch")
        self.assertEqual(caught.exception.code, "notifications-off")

    def test_a_bad_time_is_refused_by_the_service(self):
        with self.assertRaises(IntegrationError) as caught:
            self.agent("reminder-plan", message="Stretch", at="whenever")
        self.assertIn("is not a time", caught.exception.message)

    def test_a_due_reminder_is_sent_once_even_when_scheduled_task_notices_are_off(self):
        due = {"jobId": "r1", "name": "Reminder: Stretch", "description": "Portal reminder: Stretch your legs"}
        self.assertEqual([self.agent("reminder-due", **due)["notified"] for _ in range(3)], [True, False, False])
        self.assertEqual(FakeNtfy.sent, [{"title": "Reminder: Stretch", "message": "Stretch your legs", "priority": "default"}])
        self.assertTrue(self.agent("reminder-due", **{**due, "jobId": "r2"})["notified"], "another reminder is its own")

    def test_a_due_reminder_with_notifications_off_is_logged_not_sent(self):
        self.save(notify=False)
        self.assertFalse(self.agent("reminder-due", jobId="r1", name="Reminder: Tea", description="Portal reminder: Tea")["notified"])
        self.assertEqual(FakeNtfy.sent, [])
        self.assertIn("notifications are off", self.owner("get")["activity"][0]["outcome"])

    def test_only_reminder_jobs_can_be_sent_as_reminders(self):
        with self.assertRaises(IntegrationError):
            self.agent("reminder-due", jobId="x", name="Backup", description="Nightly backup")

    def test_a_reminder_s_own_run_does_not_also_notify_as_a_scheduled_task(self):
        self.save(tasks="all")
        for status in ("ok", "error"):
            self.assertFalse(self.agent("task-finished", jobId="r1", name="Reminder: Stretch", description="Portal reminder: Stretch",
                                        status=status, summary="Done.", runAtMs=1)["notified"])
        self.assertEqual(FakeNtfy.sent, [])
        self.assertTrue(self.agent("task-finished", jobId="b1", name="Backup", description="", status="ok", summary="Done.",
                                   runAtMs=1)["notified"])


if __name__ == "__main__":
    unittest.main()
