"""Bounded, content-free minute buckets from recorded Token Spy requests."""
from datetime import datetime, timedelta, timezone
import math

MAX_EVENTS = 10000
FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def minute_timeline(events, now=None):
    now = now or datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if not isinstance(events, list):
        raise ValueError("Invalid telemetry response")
    truncated = len(events) >= MAX_EVENTS
    parsed = []
    invalid = 0
    for event in events[:MAX_EVENTS]:
        try:
            stamp = datetime.fromisoformat(str(event["timestamp"]).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            stamp = stamp.astimezone(timezone.utc)
            if not start <= stamp <= now:
                continue
            values = {}
            for field in FIELDS:
                value = event.get(field)
                if value is None and field.startswith("cache_"):
                    value = 0
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise ValueError("Invalid token count")
                values[field] = int(value)
            parsed.append((stamp, values))
        except (ValueError, TypeError, KeyError, OverflowError):
            invalid += 1
    # Do not present omitted history as zero activity if the source hit its cap.
    bucket_start = min(stamp for stamp, _ in parsed).replace(second=0, microsecond=0) if truncated and parsed else start
    points = {}
    cursor = bucket_start
    while cursor <= now:
        points[cursor] = {"date": cursor.isoformat(), **{field: 0 for field in FIELDS}, "requests": 0, "total_tokens": 0}
        cursor += timedelta(minutes=1)
    for stamp, values in parsed:
        row = points[stamp.replace(second=0, microsecond=0)]
        for field, value in values.items():
            row[field] += value
        row["total_tokens"] += sum(values.values())
        row["requests"] += 1
    return {"source": {"status": "ok", "name": "token-spy"}, "interval_seconds": 60,
            "period": {"start": bucket_start.isoformat(), "end": now.isoformat()},
            "points": list(points.values()), "truncated": truncated, "invalid_records": invalid,
            "recorded_requests": len(parsed)}
