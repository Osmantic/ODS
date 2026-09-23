#!/usr/bin/env python3
"""Run rendered Operations cases through a live OpenClaw Gateway and retain evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path


SAFE_CASE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


def verify_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    expected = manifest.pop("manifestSha256", None)
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    if not expected or hashlib.sha256(canonical).hexdigest() != expected:
        raise ValueError("manifest hash mismatch")
    manifest["manifestSha256"] = expected
    return manifest


def detect_launcher_options(openclaw_bin: Path) -> tuple[str, str]:
    """Fail before evidence capture when the selected launcher is incompatible."""
    completed = subprocess.run(
        [str(openclaw_bin), "agent", "--help"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=30,
    )
    output = completed.stdout.decode("utf-8", "replace")
    if completed.returncode != 0:
        raise RuntimeError(f"OpenClaw launcher preflight failed: {output[-2000:]}")
    if "--message-file" in output:
        message_option = "--message-file"
    elif re.search(r"(?:^|\s)--message(?:[=\s]|$)", output):
        message_option = "--message"
    else:
        raise RuntimeError("OpenClaw launcher does not advertise --message-file or --message")
    if not re.search(r"(?:^|\s)--session-key(?:[=\s]|$)", output):
        raise RuntimeError(
            "OpenClaw launcher does not advertise --session-key; use the pinned Pixel launcher"
        )
    return message_option, "--session-key"


def verify_agent_available(openclaw_bin: Path, agent: str) -> None:
    completed = subprocess.run(
        [str(openclaw_bin), "agents", "list", "--json"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        detail = completed.stderr[-2000:].decode("utf-8", "replace")
        raise RuntimeError(f"OpenClaw agent preflight failed: {detail}")
    try:
        agents = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OpenClaw agent preflight returned invalid JSON") from exc
    if not isinstance(agents, list) or not any(
        isinstance(item, dict) and item.get("id") == agent for item in agents
    ):
        raise RuntimeError(
            f'OpenClaw agent preflight could not find "{agent}"; load the Pixel deployment environment'
        )


def read_session_records(path: Path) -> list[dict]:
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"session JSONL line {number} is incomplete or invalid") from exc
        if not isinstance(record, dict):
            raise ValueError(f"session JSONL line {number} is not an object")
        records.append(record)
    return records


def last_yield_index(records: list[dict]) -> int | None:
    found = None
    for index, record in enumerate(records):
        content = record.get("message", {}).get("content", [])
        for part in content if isinstance(content, list) else []:
            if isinstance(part, dict) and part.get("type") == "toolCall" and part.get("name") == "sessions_yield":
                found = index
    return found


def terminal_assistant_text(records: list[dict], after: int) -> str | None:
    for record in reversed(records[after + 1:]):
        message = record.get("message", {})
        if message.get("role") != "assistant":
            continue
        if message.get("api") == "cli":
            continue
        content = message.get("content", [])
        if not isinstance(content, list) or any(
            isinstance(part, dict) and part.get("type") == "toolCall" for part in content
        ):
            return None
        text = "\n".join(
            str(part.get("text")) for part in content
            if isinstance(part, dict) and part.get("type") == "text" and part.get("text")
        ).strip()
        return text or None
    return None


def wait_for_yield_completion(path: Path, timeout: int) -> str | None:
    """Wait for the final parent turn when a child completion resumes a yielded session."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            records = read_session_records(path)
        except (OSError, ValueError):
            records = []
        yielded_at = last_yield_index(records)
        if yielded_at is None:
            return None
        final_text = terminal_assistant_text(records, yielded_at)
        if final_text is not None:
            return final_text
        if time.monotonic() >= deadline:
            raise TimeoutError("yielded session did not produce a final parent response")
        time.sleep(0.25)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--openclaw-bin", type=Path, required=True)
    parser.add_argument("--agent", default="pixel")
    parser.add_argument("--session-prefix", required=True)
    parser.add_argument("--cases", help="Comma-separated case IDs; default is every case")
    parser.add_argument("--responses-dir", type=Path, required=True)
    parser.add_argument("--transcripts-dir", type=Path, required=True)
    parser.add_argument("--sessions-root", type=Path)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    if not SAFE_CASE.fullmatch(args.agent) or not SAFE_CASE.fullmatch(args.session_prefix):
        parser.error("--agent and --session-prefix must be safe lowercase IDs")
    if not args.openclaw_bin.is_absolute() or not args.openclaw_bin.is_file():
        parser.error("--openclaw-bin must be an absolute regular file")
    if not 30 <= args.timeout <= 3600:
        parser.error("--timeout must be 30..3600 seconds")
    message_option, session_option = detect_launcher_options(args.openclaw_bin)
    verify_agent_available(args.openclaw_bin, args.agent)
    manifest = verify_manifest(args.manifest)
    selected = set(args.cases.split(",")) if args.cases else {case["id"] for case in manifest["cases"]}
    known = {case["id"] for case in manifest["cases"]}
    if not selected or not selected <= known or any(not SAFE_CASE.fullmatch(case_id) for case_id in selected):
        parser.error("--cases contains an unknown or unsafe case ID")
    sessions_root = (args.sessions_root or (Path.home() / ".openclaw" / "agents" / args.agent / "sessions")).resolve()
    if not sessions_root.is_absolute() or sessions_root == Path("/"):
        parser.error("--sessions-root must be an absolute non-root path")
    args.responses_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.transcripts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    results = {}
    for case in manifest["cases"]:
        case_id = case["id"]
        if case_id not in selected:
            continue
        prompt = (args.manifest.parent / case["promptFile"]).resolve()
        if prompt.parent != args.manifest.parent.resolve() or not prompt.is_file():
            raise ValueError(f"unsafe or absent prompt file for {case_id}")
        response_path = args.responses_dir / f"{case_id}.json"
        transcript_path = args.transcripts_dir / f"{case_id}.jsonl"
        if response_path.exists() or transcript_path.exists():
            raise FileExistsError(f"evidence already exists for {case_id}")
        message_value = str(prompt) if message_option == "--message-file" else prompt.read_text(encoding="utf-8")
        completed = subprocess.run(
            [
                str(args.openclaw_bin), "agent", "--agent", args.agent,
                session_option, f"agent:{args.agent}:{args.session_prefix}-{case_id}",
                message_option, message_value, "--json", "--timeout", str(args.timeout),
            ],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False, timeout=args.timeout + 60,
        )
        response_path.write_bytes(completed.stdout)
        os.chmod(response_path, 0o600)
        if completed.returncode != 0:
            raise RuntimeError(f"OpenClaw case {case_id} failed: {completed.stderr[-2000:].decode('utf-8', 'replace')}")
        response = json.loads(completed.stdout)
        if response.get("status") != "ok":
            raise RuntimeError(f"OpenClaw case {case_id} returned non-ok status")
        session_value = response.get("result", {}).get("meta", {}).get("agentMeta", {}).get("sessionFile")
        if not isinstance(session_value, str):
            raise ValueError(f"OpenClaw case {case_id} omitted its session file")
        session = Path(session_value)
        session_info = session.lstat()
        resolved_session = session.resolve(strict=True)
        if session.is_symlink() or not stat.S_ISREG(session_info.st_mode) or resolved_session.parent != sessions_root or resolved_session.suffix != ".jsonl":
            raise ValueError(f"OpenClaw case {case_id} returned an unsafe session file")
        records = read_session_records(resolved_session)
        if last_yield_index(records) is not None:
            final_text = wait_for_yield_completion(resolved_session, args.timeout)
            payloads = response.get("result", {}).get("payloads")
            if final_text and not payloads:
                response["result"]["payloads"] = [{"text": final_text}]
                temporary_response = response_path.with_name(f".{case_id}.response.tmp")
                with temporary_response.open("x", encoding="utf-8") as destination:
                    json.dump(response, destination, separators=(",", ":"))
                    destination.write("\n")
                os.chmod(temporary_response, 0o600)
                os.replace(temporary_response, response_path)
        with resolved_session.open("rb") as source, transcript_path.open("xb") as destination:
            shutil.copyfileobj(source, destination)
        os.chmod(transcript_path, 0o600)
        results[case_id] = {
            "status": "captured",
            "responseBytes": response_path.stat().st_size,
            "transcriptBytes": transcript_path.stat().st_size,
        }
    print(json.dumps({"schemaVersion": 1, "cases": results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
