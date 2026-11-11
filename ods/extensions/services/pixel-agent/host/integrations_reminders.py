"""Reminders: Portal schedules each one as a one-off OpenClaw cron job, and the service sends it
through ntfy when the job starts. The job carries the reminder in its description, so nothing
else has to remember it."""
import datetime
import re

from integrations_common import IntegrationError

MARK = "Portal reminder: "  # Starts the description of every reminder job.
MAX_MINUTES = 366 * 24 * 60
MAX_TEXT = 1000
MAX_TITLE = 80
CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")
WHEN_HELP = "Say when: inMinutes (such as 10) or at (a time such as 17:30, or a date and time such as 2026-11-04T09:00)."
# What the job's own agent turn does when it runs; the reminder itself has gone out already.
RUN_MESSAGE = "This reminder was already sent to the owner's phone. Do nothing else. Reply with exactly: Done."


def _minutes(value):
    if isinstance(value, bool):
        raise IntegrationError("invalid-reminder", WHEN_HELP)
    try:
        minutes = float(value)
    except (TypeError, ValueError):
        raise IntegrationError("invalid-reminder", WHEN_HELP) from None
    if not 1 <= minutes <= MAX_MINUTES:
        raise IntegrationError("invalid-reminder", "A reminder can be set from 1 minute to a year ahead.")
    return minutes


def due_at(now, in_minutes=None, at=None):
    """When the reminder is due, as an aware datetime. now is aware, in the host's time zone (a zone
    with its daylight-saving rules, such as a zoneinfo.ZoneInfo, so a time across a change keeps its
    clock time); a time without a date is its next occurrence, and a date and time without an offset
    is local."""
    given = [value not in (None, "") for value in (in_minutes, at)]
    if given == [False, False]:
        raise IntegrationError("invalid-reminder", WHEN_HELP)
    if given == [True, True]:
        raise IntegrationError("invalid-reminder", "Give inMinutes or at, not both.")
    if given[0]:
        # Real elapsed time: adding to a zoned time adds to its clock, an hour off across a change.
        utc = now.astimezone(datetime.timezone.utc) + datetime.timedelta(minutes=_minutes(in_minutes))
        return utc.astimezone(now.tzinfo)
    text = str(at).strip()
    clock = CLOCK.match(text)
    if clock:
        hour, minute = int(clock.group(1)), int(clock.group(2))
        if hour > 23 or minute > 59:
            raise IntegrationError("invalid-reminder", f"{text} is not a time of day.")
        due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return due if due > now else due + datetime.timedelta(days=1)
    try:
        due = datetime.datetime.fromisoformat(text)
    except ValueError:
        raise IntegrationError("invalid-reminder", f"{text} is not a time. {WHEN_HELP}") from None
    if due.tzinfo is None:
        due = due.replace(tzinfo=now.tzinfo)
    if due <= now:
        raise IntegrationError("invalid-reminder", f"{text} has already passed. Give a time in the future.")
    if due - now > datetime.timedelta(minutes=MAX_MINUTES):
        raise IntegrationError("invalid-reminder", "A reminder can be set from 1 minute to a year ahead.")
    return due


def _clean(value, field, limit, *, required):
    text = value if isinstance(value, str) else ""
    text = " ".join(text.split()) if field == "title" else text.strip()
    if required and not text:
        raise IntegrationError("invalid-reminder", "Give the reminder text in message.")
    if len(text) > limit:
        raise IntegrationError("invalid-reminder", f"Keep the reminder {field} under {limit} characters.")
    return text


def plan(now, title, message, in_minutes=None, at=None):
    """The cron job's details for one reminder; now is aware, in the host's local time."""
    text = _clean(message, "message", MAX_TEXT, required=True)
    title = _clean(title, "title", MAX_TITLE, required=False) or " ".join(text.split())[:MAX_TITLE]
    due = due_at(now, in_minutes, at)
    return {"name": f"Reminder: {title}", "description": MARK + text, "atMs": int(due.timestamp() * 1000),
            "when": due.strftime("%a %d %b %Y at %H:%M %Z").strip(), "text": text, "runMessage": RUN_MESSAGE}


def is_reminder(description):
    return isinstance(description, str) and description.startswith(MARK)


def notice(name, description):
    """The notification for a due reminder: its title and text."""
    title = name if isinstance(name, str) and name.strip() else "Reminder"
    return title.strip()[:200], description[len(MARK):].strip() or title
