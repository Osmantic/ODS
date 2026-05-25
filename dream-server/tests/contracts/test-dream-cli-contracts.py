#!/usr/bin/env python3
"""Static contracts for the large Bash dream CLI.

These checks intentionally avoid executing dream-cli. They are a decomposition
guardrail: before the CLI is split into smaller files, the public command help,
main dispatch table, and command implementation functions should agree.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
DREAM_CLI = ROOT_DIR / "dream-cli"


COMMANDS = {
    "gpu": ("cmd_gpu", "gpu [status|topology|assignment|validate|reassign]"),
    "status": ("cmd_status", "status [--json]"),
    "status-json": ("cmd_status_json", "status-json"),
    "list": ("cmd_list", "list [--json]"),
    "enable": ("cmd_enable", "enable <service>"),
    "disable": ("cmd_disable", "disable <service>"),
    "purge": ("cmd_purge", "purge <service>"),
    "preset": ("cmd_preset", "preset <action>"),
    "mode": ("cmd_mode", "mode [local|cloud|hybrid]"),
    "model": ("cmd_model", "model [current|list|swap]"),
    "stt": ("cmd_stt", "stt [current|status|download]"),
    "backup": ("cmd_backup", "backup [options]"),
    "restore": ("cmd_restore", "restore [backup_id]"),
    "rollback": ("cmd_rollback", "rollback"),
    "logs": ("cmd_logs", "logs <service>"),
    "restart": ("cmd_restart", "restart [service]"),
    "repair": ("cmd_repair", "repair|fix [voice"),
    "start": ("cmd_start", "start [service]"),
    "stop": ("cmd_stop", "stop [service]"),
    "update": ("cmd_update", "update [--force]"),
    "shell": ("cmd_shell", "shell <service>"),
    "config": ("cmd_config", "config [show|edit|validate]"),
    "chat": ("cmd_chat", 'chat "<message>"'),
    "benchmark": ("cmd_benchmark", "benchmark"),
    "doctor": ("cmd_doctor", "doctor [report|--json]"),
    "audit": ("cmd_audit", "audit [extensions]"),
    "template": ("cmd_template", "template [action]"),
    "agent": ("cmd_agent", "agent [action]"),
}


def fail(message: str) -> None:
    print(f"[FAIL] {message}")
    raise SystemExit(1)


def require(pattern: str, text: str, message: str) -> None:
    if not re.search(pattern, text, flags=re.MULTILINE):
        fail(message)


def dispatch_pattern(command: str, function_name: str) -> str:
    """Match a main-case branch where command may have aliases."""
    token = re.escape(command)
    return (
        rf"^\s*(?:[A-Za-z0-9_-]+\|)*{token}"
        rf"(?:\|[A-Za-z0-9_-]+)*\)\s*(?:shift;\s*)?{function_name}\b"
    )


def main() -> int:
    if not DREAM_CLI.exists():
        fail(f"Missing dream CLI: {DREAM_CLI}")

    text = DREAM_CLI.read_text(encoding="utf-8")

    require(r"^#!/usr/bin/env bash$", text, "dream-cli should remain a Bash entrypoint")
    require(
        r"Usage: dream <command> \[options\]",
        text,
        "dream-cli help must expose the top-level usage line",
    )
    require(
        r'^case "\$\{1:-help\}" in$',
        text,
        "dream-cli must keep a top-level command dispatch case",
    )
    require(r"^cmd_help\(\) \{$", text, "dream-cli must define cmd_help")

    for command, (function_name, help_snippet) in COMMANDS.items():
        require(
            rf"^{function_name}\(\) \{{",
            text,
            f"{command} is missing implementation function {function_name}",
        )
        if help_snippet not in text:
            fail(f"{command} is missing help entry: {help_snippet}")
        require(
            dispatch_pattern(command, function_name),
            text,
            f"{command} is missing main dispatch to {function_name}",
        )

    print("[PASS] dream-cli static command contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
