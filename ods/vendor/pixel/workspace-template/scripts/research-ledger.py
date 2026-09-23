#!/usr/bin/env python3
"""Keep a private, structured record of a multi-query research campaign."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parent.parent
ledger_path = root / "context" / "research" / "ledger.json"


def load():
    try:
        value = json.loads(ledger_path.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save(entries):
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = ledger_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    temporary.replace(ledger_path)


def record(args):
    entries = load()
    entries.append({
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "topic": args.topic,
        "query": args.query,
        "method": args.method,
        "outcome": args.outcome,
        "url": args.url,
        "note": args.note,
    })
    save(entries)
    print(f"Recorded {args.method} probe for {args.topic}.")


def status(args):
    entries = [entry for entry in load() if not args.topic or entry.get("topic") == args.topic]
    print(json.dumps(entries[-args.limit :], indent=2))


parser = argparse.ArgumentParser(description="private Pixel research campaign ledger")
sub = parser.add_subparsers(dest="command", required=True)
add = sub.add_parser("record")
add.add_argument("--topic", required=True)
add.add_argument("--query", required=True)
add.add_argument("--method", choices=["web_search", "web_fetch", "courier"], required=True)
add.add_argument("--outcome", choices=["useful", "thin", "blocked", "failed"], required=True)
add.add_argument("--url", default="")
add.add_argument("--note", default="")
add.set_defaults(function=record)
show = sub.add_parser("status")
show.add_argument("--topic", default="")
show.add_argument("--limit", type=int, choices=range(1, 501), default=25, metavar="1..500")
show.set_defaults(function=status)
arguments = parser.parse_args()
arguments.function(arguments)
