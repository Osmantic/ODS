"""SQLite storage for token usage metrics."""

import sqlite3
import os
import threading
from datetime import date, timedelta

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "data", "usage.db"))

# "Last N hours" cutoff for recent-window queries. The timestamp column stores
# strftime('%Y-%m-%dT%H:%M:%fZ', ...) (T-separated, trailing Z). A bare
# datetime('now', ...) yields a space-separated 'YYYY-MM-DD HH:MM:SS' value, and
# because the column is TEXT the comparison is a byte-wise sort: the space
# separator (0x20) sorts before the stored 'T' (0x54), so every row on the
# cutoff's calendar date compares greater than the bound regardless of its
# time-of-day and gets pulled in — over-including up to a full extra day. Build
# the bound in the stored format so the comparison matches wall-clock order, in
# line with the Postgres backend's NOW() - INTERVAL arithmetic.
_RECENT_TS_BOUND = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?)"

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _local.conn = sqlite3.connect(DB_PATH)
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA busy_timeout=5000")
    return _local.conn


def init_db():
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            agent TEXT NOT NULL,
            model TEXT,

            -- Request metrics
            request_body_bytes INTEGER DEFAULT 0,
            message_count INTEGER DEFAULT 0,
            user_message_count INTEGER DEFAULT 0,
            assistant_message_count INTEGER DEFAULT 0,
            tool_count INTEGER DEFAULT 0,

            -- System prompt breakdown (chars)
            system_prompt_total_chars INTEGER DEFAULT 0,
            workspace_agents_chars INTEGER DEFAULT 0,
            workspace_soul_chars INTEGER DEFAULT 0,
            workspace_tools_chars INTEGER DEFAULT 0,
            workspace_identity_chars INTEGER DEFAULT 0,
            workspace_user_chars INTEGER DEFAULT 0,
            workspace_heartbeat_chars INTEGER DEFAULT 0,
            workspace_bootstrap_chars INTEGER DEFAULT 0,
            workspace_memory_chars INTEGER DEFAULT 0,
            skill_injection_chars INTEGER DEFAULT 0,
            base_prompt_chars INTEGER DEFAULT 0,

            -- Conversation history (chars)
            conversation_history_chars INTEGER DEFAULT 0,

            -- Response token usage from Anthropic
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_write_tokens INTEGER DEFAULT 0,

            -- Derived
            estimated_cost_usd REAL DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            stop_reason TEXT,
            provider_name TEXT,
            cost_source TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON usage(timestamp);
        CREATE INDEX IF NOT EXISTS idx_usage_agent ON usage(agent);
    """)
    conn.commit()

    # Add filter metric columns (idempotent — safe on existing databases)
    # Allowlist for security: only these columns can be added
    ALLOWED_COLUMNS = {
        "filter_chars_saved": "INTEGER DEFAULT 0",
        "filter_tokens_saved": "INTEGER DEFAULT 0",
        "filter_tools_removed": "INTEGER DEFAULT 0",
        "provider_name": "TEXT",
        "cost_source": "TEXT",
    }

    import re
    # Regex to validate SQL identifiers: alphanumeric + underscore only
    SAFE_IDENTIFIER = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')

    for col, typedef in ALLOWED_COLUMNS.items():
        try:
            # Defense in depth: validate column name is a safe SQL identifier
            # even though col comes from a hardcoded dict (protects against future refactoring)
            if not SAFE_IDENTIFIER.match(col):
                raise ValueError(f"Invalid column name: {col}")
            conn.execute(f"ALTER TABLE usage ADD COLUMN {col} {typedef}")
            conn.commit()
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                pass  # Column already exists
            else:
                raise  # Re-raise unexpected errors (disk full, permissions, etc.)


def log_usage(entry: dict):
    conn = _get_conn()
    cols = [
        "agent", "model",
        "request_body_bytes", "message_count", "user_message_count",
        "assistant_message_count", "tool_count",
        "system_prompt_total_chars",
        "workspace_agents_chars", "workspace_soul_chars", "workspace_tools_chars",
        "workspace_identity_chars", "workspace_user_chars", "workspace_heartbeat_chars",
        "workspace_bootstrap_chars",
        "skill_injection_chars", "base_prompt_chars",
        "conversation_history_chars",
        "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
        "estimated_cost_usd", "duration_ms", "stop_reason",
        "provider_name", "cost_source",
        "filter_chars_saved", "filter_tokens_saved", "filter_tools_removed",
    ]
    values = [entry.get(c) for c in cols]
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)
    conn.execute(f"INSERT INTO usage ({col_names}) VALUES ({placeholders})", values)
    conn.commit()


def query_usage(agent: str | None = None, hours: int = 24, limit: int = 200) -> list[dict]:
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    sql = f"SELECT * FROM usage WHERE timestamp > {_RECENT_TS_BOUND}"
    params: list = [f"-{hours} hours"]
    if agent:
        sql += " AND agent = ?"
        params.append(agent)
    sql += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# Inclusive-day span cap for the report endpoint. Mirrors
# dashboard-api/routers/usage.py:MAX_REPORT_RANGE_DAYS — both build one dict
# per day in the requested span, so the bound belongs on each side.
MAX_REPORT_RANGE_DAYS = 366



def _parse_report_dates(start: str, end: str) -> tuple[date, date, date]:
    start_day = date.fromisoformat(start)
    end_day = date.fromisoformat(end)
    if end_day < start_day:
        raise ValueError("end must be on or after start")
    if end_day == date.max:
        raise ValueError("end date is out of range")
    # The report materializes one bucket per day in the span, so an unbounded
    # range turns a single request into millions of them. Cap it before the
    # arithmetic below, which also overflows on date.max.
    if (end_day - start_day).days >= MAX_REPORT_RANGE_DAYS:
        raise ValueError(
            f"range must be shorter than {MAX_REPORT_RANGE_DAYS} days"
        )
    return start_day, end_day, end_day + timedelta(days=1)


def _date_range(start_day: date, end_day: date) -> list[str]:
    days = []
    current = start_day
    while current <= end_day:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _normalize_cost_source(row: dict) -> str:
    source = row.get("cost_source")
    if source in {"actual_billed", "priced_from_tokens", "local_zero_cost", "untracked"}:
        return source
    if float(row.get("estimated_cost_usd") or 0) > 0:
        return "priced_from_tokens"
    local_agents = set(filter(None, os.environ.get("LOCAL_MODEL_AGENTS", "").split(",")))
    if row.get("agent") in local_agents or row.get("provider_name") == "local":
        return "local_zero_cost"
    return "untracked"


def _empty_report(start: str, end: str) -> dict:
    start_day, end_day, _ = _parse_report_dates(start, end)
    return {
        "period": {"start": start, "end": end},
        "summary": {
            "spend_usd": 0,
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 0,
            "tracked_providers": 0,
            "billing_providers": 0,
            "local_providers": 0,
            "untracked_providers": 0,
            "paid_cost_usd": 0,
            "local_cost_usd": 0,
        },
        "daily": [
            {
                "date": day,
                "spend_usd": 0,
                "requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }
            for day in _date_range(start_day, end_day)
        ],
        "models": [],
        "services": [],
        "sources": [],
    }


def query_report(start: str, end: str) -> dict:
    """Aggregate real usage/cost data for an inclusive date range."""
    start_day, end_day, end_exclusive = _parse_report_dates(start, end)
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            timestamp, agent, model, provider_name, cost_source,
            input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
            estimated_cost_usd
        FROM usage
        WHERE timestamp >= ? AND timestamp < ?
        ORDER BY timestamp ASC
        """,
        (f"{start_day.isoformat()}T00:00:00Z", f"{end_exclusive.isoformat()}T00:00:00Z"),
    ).fetchall()

    report = _empty_report(start, end)
    daily = {row["date"]: row for row in report["daily"]}
    models: dict[tuple[str, str, str, str], dict] = {}
    services: dict[str, dict] = {}
    sources: dict[str, dict] = {}
    provider_names = set()
    billing_providers = set()
    local_providers = set()
    untracked_providers = set()

    for sqlite_row in rows:
        row = dict(sqlite_row)
        day = (row.get("timestamp") or "")[:10]
        if day not in daily:
            continue
        service = row.get("agent") or "unknown"
        model = row.get("model") or "unknown"
        provider = row.get("provider_name") or "unknown"
        source = _normalize_cost_source(row)
        input_tokens = int(row.get("input_tokens") or 0)
        output_tokens = int(row.get("output_tokens") or 0)
        cache_read = int(row.get("cache_read_tokens") or 0)
        cache_write = int(row.get("cache_write_tokens") or 0)
        cost = float(row.get("estimated_cost_usd") or 0)

        provider_names.add(provider)
        if source in {"actual_billed", "priced_from_tokens"}:
            billing_providers.add(provider)
        elif source == "local_zero_cost":
            local_providers.add(provider)
        else:
            untracked_providers.add(provider)

        day_row = daily[day]
        day_row["spend_usd"] += cost
        day_row["requests"] += 1
        day_row["input_tokens"] += input_tokens
        day_row["output_tokens"] += output_tokens
        day_row["cache_read_tokens"] += cache_read
        day_row["cache_write_tokens"] += cache_write

        model_key = (model, provider, service, source)
        model_row = models.setdefault(model_key, {
            "model": model,
            "provider": provider,
            "service": service,
            "cost_source": source,
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_usd": 0,
        })
        service_row = services.setdefault(service, {
            "service": service,
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_usd": 0,
        })
        source_row = sources.setdefault(source, {
            "source": source,
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_usd": 0,
        })

        for target in (model_row, service_row, source_row):
            target["requests"] += 1
            target["input_tokens"] += input_tokens
            target["output_tokens"] += output_tokens
            target["cache_read_tokens"] += cache_read
            target["cache_write_tokens"] += cache_write
            target["cost_usd"] += cost

    summary = report["summary"]
    summary["requests"] = len(rows)
    for day_row in report["daily"]:
        summary["spend_usd"] += day_row["spend_usd"]
        summary["input_tokens"] += day_row["input_tokens"]
        summary["output_tokens"] += day_row["output_tokens"]
        summary["cache_read_tokens"] += day_row["cache_read_tokens"]
        summary["cache_write_tokens"] += day_row["cache_write_tokens"]
        day_row["spend_usd"] = round(day_row["spend_usd"], 6)
    summary["total_tokens"] = (
        summary["input_tokens"]
        + summary["output_tokens"]
        + summary["cache_read_tokens"]
        + summary["cache_write_tokens"]
    )
    summary["spend_usd"] = round(summary["spend_usd"], 6)
    summary["paid_cost_usd"] = round(sum(
        source["cost_usd"] for source in sources.values()
        if source["source"] in {"actual_billed", "priced_from_tokens"}
    ), 6)
    summary["local_cost_usd"] = round(sum(
        source["cost_usd"] for source in sources.values()
        if source["source"] == "local_zero_cost"
    ), 6)
    summary["tracked_providers"] = len(provider_names)
    summary["billing_providers"] = len(billing_providers)
    summary["local_providers"] = len(local_providers)
    summary["untracked_providers"] = len(untracked_providers)

    report["models"] = sorted(
        ({**row, "cost_usd": round(row["cost_usd"], 6)} for row in models.values()),
        key=lambda row: (-row["cost_usd"], row["model"], row["service"]),
    )
    report["services"] = sorted(
        ({**row, "cost_usd": round(row["cost_usd"], 6)} for row in services.values()),
        key=lambda row: (-row["cost_usd"], row["service"]),
    )
    report["sources"] = sorted(
        ({**row, "cost_usd": round(row["cost_usd"], 6)} for row in sources.values()),
        key=lambda row: row["source"],
    )
    return report


def query_summary(hours: int = 24) -> list[dict]:
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(f"""
        SELECT
            agent,
            COUNT(*) as turns,
            SUM(input_tokens) as total_input_tokens,
            SUM(output_tokens) as total_output_tokens,
            SUM(cache_read_tokens) as total_cache_read,
            SUM(cache_write_tokens) as total_cache_write,
            SUM(estimated_cost_usd) as total_cost,
            AVG(input_tokens) as avg_input_tokens,
            MAX(input_tokens) as max_input_tokens,
            AVG(system_prompt_total_chars) as avg_system_chars,
            AVG(conversation_history_chars) as avg_history_chars,
            AVG(skill_injection_chars) as avg_skill_chars,
            AVG(base_prompt_chars) as avg_base_prompt_chars
        FROM usage
        WHERE timestamp > {_RECENT_TS_BOUND}
        GROUP BY agent
    """, [f"-{hours} hours"]).fetchall()
    return [dict(r) for r in rows]


def query_session_status(agent: str, char_limit: int = 200_000) -> dict:
    """Get current session health metrics for an agent.

    Detects session boundaries by looking for sudden drops in conversation_history_chars
    (indicating a session reset). Returns metrics for the current session.
    char_limit controls the threshold levels for recommendations.
    """
    conn = _get_conn()
    conn.row_factory = sqlite3.Row

    # Get all recent turns for this agent, ordered chronologically
    rows = conn.execute(f"""
        SELECT conversation_history_chars, cache_read_tokens, cache_write_tokens,
               estimated_cost_usd, timestamp
        FROM usage
        WHERE agent = ? AND timestamp > {_RECENT_TS_BOUND}
        ORDER BY timestamp ASC
    """, [agent, "-24 hours"]).fetchall()

    if not rows:
        return {
            "agent": agent,
            "current_session_turns": 0,
            "current_history_chars": 0,
            "last_turn_cost": 0,
            "avg_cost_last_5": 0,
            "cache_write_pct_last_5": 0,
            "cost_since_last_reset": 0,
            "turns_since_last_reset": 0,
            "recommendation": "no_data",
        }

    rows = [dict(r) for r in rows]

    # Find last session reset: a turn where history drops by >50%
    last_reset_idx = 0
    for i in range(1, len(rows)):
        prev = rows[i - 1]["conversation_history_chars"] or 0
        curr = rows[i]["conversation_history_chars"] or 0
        if prev > 1000 and curr < prev * 0.5:
            last_reset_idx = i

    session_rows = rows[last_reset_idx:]
    current_history = session_rows[-1]["conversation_history_chars"] or 0
    last_cost = session_rows[-1]["estimated_cost_usd"] or 0
    total_cost = sum(r["estimated_cost_usd"] or 0 for r in session_rows)

    # Last 5 turns for rolling averages
    last_5 = session_rows[-5:]
    avg_cost_5 = sum(r["estimated_cost_usd"] or 0 for r in last_5) / max(len(last_5), 1)
    total_cache_5 = sum((r["cache_read_tokens"] or 0) + (r["cache_write_tokens"] or 0) for r in last_5)
    total_write_5 = sum(r["cache_write_tokens"] or 0 for r in last_5)
    cache_write_pct = total_write_5 / max(total_cache_5, 1)

    # Recommendation logic (thresholds scale with configurable char_limit)
    if current_history > char_limit * 2.5:
        rec = "reset_recommended"
    elif current_history > char_limit * 2:
        rec = "compact_soon"
    elif current_history > char_limit:
        rec = "monitor"
    elif cache_write_pct > 0.20 and len(last_5) >= 3:
        rec = "cache_unstable"
    else:
        rec = "healthy"

    return {
        "agent": agent,
        "current_session_turns": len(session_rows),
        "current_history_chars": current_history,
        "last_turn_cost": round(last_cost, 6),
        "avg_cost_last_5": round(avg_cost_5, 6),
        "cache_write_pct_last_5": round(cache_write_pct, 4),
        "cost_since_last_reset": round(total_cost, 6),
        "turns_since_last_reset": len(session_rows),
        "recommendation": rec,
    }


def query_recent_events(limit: int = 100, after_id: str = None):
    """Query recent token usage events for SSE streaming."""
    conn = _get_conn()
    conn.row_factory = sqlite3.Row

    if after_id:
        rows = conn.execute(
            """
            SELECT
                id, agent as agent_name, model,
                input_tokens, output_tokens,
                (input_tokens + output_tokens) as total_tokens,
                estimated_cost_usd as cost_usd, timestamp
            FROM usage
            WHERE id > ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (after_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT
                id, agent as agent_name, model,
                input_tokens, output_tokens,
                (input_tokens + output_tokens) as total_tokens,
                estimated_cost_usd as cost_usd, timestamp
            FROM usage
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()

    return [dict(r) for r in rows]
