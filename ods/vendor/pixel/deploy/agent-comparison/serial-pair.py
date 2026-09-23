#!/usr/bin/env python3
"""Reproducible serial Pixel-vs-reference-harness comparison on an identical local model.

Runs one battery task through the Pixel (openclaw) arm then the reference (Codex CLI) arm,
serially and hermetically against the same local model endpoint, extracts both usage
accountings, independently verifies the produced artifact, and writes one content-free
comparison record. Requires the live model host; it is the documented method behind the
agent-comparison task battery, not a release-gate unit."""


import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE = "http://127.0.0.1:8000"
MODEL_ID = "DeepSeek-V4-Flash-0731"
PRIVATE = Path("/mnt/bulk/pixel-outcome-private")
RESULTS = PRIVATE / "serial-results"


def counters():
    out = subprocess.run(
        ["curl", "-s", "--max-time", "10", f"{BASE}/metrics"], capture_output=True, timeout=15,
    ).stdout.decode("utf-8", errors="replace")
    totals = {"prompt": 0.0, "generation": 0.0, "requests": 0.0}
    for line in out.splitlines():
        name = line.split("{", 1)[0].split(" ", 1)[0]
        key = {"vllm:prompt_tokens_total": "prompt", "vllm:generation_tokens_total": "generation",
               "vllm:request_success_total": "requests"}.get(name)
        if key and not line.startswith("#"):
            try:
                totals[key] += float(line.rsplit(" ", 1)[-1])
            except ValueError:
                pass
    return {key: int(value) for key, value in totals.items()}


def plant(workspace: Path, template: dict):
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(mode=0o700, parents=True)
    for name, content in template.items():
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def texts_of(node, found):
    if isinstance(node, dict):
        for key, item in node.items():
            if key == "text" and isinstance(item, str):
                found.append(item)
            else:
                texts_of(item, found)
    elif isinstance(node, list):
        for item in node:
            texts_of(item, found)


def run_measured(argv, *, timeout, env, time_path: Path, cwd=None):
    """Run argv capturing output, and record the arm's peak resident memory (kbytes) via
    /usr/bin/time -v when available. Latency-and-memory measurement is a named outcome-parity
    requirement; peakRssKb is None on hosts without /usr/bin/time so the run still proceeds."""
    time_bin = "/usr/bin/time" if os.path.exists("/usr/bin/time") else shutil.which("time")
    if not time_bin:
        return subprocess.run(argv, capture_output=True, timeout=timeout, env=env, cwd=cwd), None
    result = subprocess.run([time_bin, "-v", "-o", str(time_path), *argv],
                            capture_output=True, timeout=timeout, env=env, cwd=cwd)
    peak = None
    try:
        for line in time_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "Maximum resident set size" in line:
                peak = int(line.rsplit(":", 1)[1].strip())
                break
    except (OSError, ValueError):
        peak = None
    return result, peak


def run_pixel(task, workspace: Path, wall_seconds: int):
    scratch = Path(subprocess.run(["mktemp", "-d", "/mnt/bulk/pixel-arm-XXXXXX"],
                                  capture_output=True, timeout=10).stdout.decode().strip())
    config = {
        "models": {"providers": {"tower": {
            "baseUrl": f"{BASE}/v1", "apiKey": "local-no-auth", "api": "openai-responses",
            "models": [{"id": MODEL_ID, "name": "DSV4 serial", "reasoning": True,
                        "input": ["text"], "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                        "contextWindow": 131072, "maxTokens": 4096}],
        }}},
        "agents": {"defaults": {"model": {"primary": f"tower/{MODEL_ID}"},
                                "workspace": str(workspace), "sandbox": {"mode": "off"}},
                   "list": [{"id": "serialarm"}]},
        "plugins": {"allow": []}, "channels": {},
    }
    (scratch / "openclaw.json").write_text(json.dumps(config), encoding="utf-8")
    (scratch / "state").mkdir()
    env = dict(os.environ, OPENCLAW_STATE_DIR=str(scratch / "state"),
               OPENCLAW_CONFIG_PATH=str(scratch / "openclaw.json"), TMPDIR=str(scratch))
    started = time.monotonic()
    result, peak_rss = run_measured(
        ["openclaw", "agent", "--local", "--agent", "serialarm", "--json", "--thinking", "off",
         "--timeout", str(wall_seconds), "-m", task["prompt"]],
        timeout=wall_seconds + 30, env=env, time_path=scratch / "time.txt",
    )
    wall_ms = int((time.monotonic() - started) * 1000)
    record = {"exitCode": result.returncode, "wallMs": wall_ms, "reply": None, "usage": None, "modelMs": None, "peakRssKb": peak_rss}
    try:
        value = json.loads(result.stdout.decode("utf-8"))
        found = []
        texts_of(value, found)
        record["reply"] = found[-1] if found else None
        meta = value.get("meta", {})
        record["usage"] = meta.get("agentMeta", {}).get("usage")
        record["modelMs"] = meta.get("durationMs")
    except ValueError:
        record["reply"] = None
    shutil.rmtree(scratch, ignore_errors=True)
    return record


def run_codex(task, workspace: Path, wall_seconds: int):
    scratch = Path(subprocess.run(["mktemp", "-d", "/mnt/bulk/codex-arm-XXXXXX"],
                                  capture_output=True, timeout=10).stdout.decode().strip())
    codex_home = scratch / ".codex"
    codex_home.mkdir()
    env = dict(os.environ, CODEX_HOME=str(codex_home), TMPDIR=str(scratch),
               PIXEL_OUTCOME_KEY="local-no-auth")
    argv = [
        "codex", "exec", "--ephemeral", "--skip-git-repo-check", "-s", "workspace-write",
        "--json", "-o", str(scratch / "last.txt"), "-m", MODEL_ID,
        "-c", 'approval_policy="never"', "-c", 'history.persistence="none"',
        "-c", "analytics.enabled=false", "-c", "feedback.enabled=false",
        "-c", 'web_search="disabled"', "-c", "tools.web_search=false",
        "-c", 'cli_auth_credentials_store="file"',
        "-c", 'model_provider="pixel_outcome"',
        "-c", 'model_providers.pixel_outcome.name="Pixel outcome DSV4"',
        "-c", f'model_providers.pixel_outcome.base_url="{BASE}/v1"',
        "-c", 'model_providers.pixel_outcome.env_key="PIXEL_OUTCOME_KEY"',
        "-c", 'model_providers.pixel_outcome.wire_api="responses"',
        "-c", "model_providers.pixel_outcome.requires_openai_auth=false",
        "-c", "model_providers.pixel_outcome.request_max_retries=0",
        "-c", "model_providers.pixel_outcome.stream_max_retries=0",
        task["prompt"],
    ]
    started = time.monotonic()
    result, peak_rss = run_measured(argv, timeout=wall_seconds + 30, env=env,
                                    time_path=scratch / "time.txt", cwd=workspace)
    wall_ms = int((time.monotonic() - started) * 1000)
    usage = None
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
                usage = event["usage"]
    reply = None
    last = scratch / "last.txt"
    if last.exists():
        reply = last.read_text(encoding="utf-8").strip()
    shutil.rmtree(scratch, ignore_errors=True)
    return {"exitCode": result.returncode, "wallMs": wall_ms, "reply": reply, "usage": usage, "peakRssKb": peak_rss}


def verify(task, workspace: Path):
    spec = task["verify"]
    result = subprocess.run(spec["command"], capture_output=True, timeout=60, cwd=workspace)
    stdout = result.stdout.decode("utf-8", errors="replace").strip()
    return {
        "exitCode": result.returncode,
        "stdoutMatches": stdout == spec["expectStdout"],
        "stdoutHead": stdout[:120],
    }


def main():
    task = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    wall_seconds = int(task.get("wallSeconds", 240))
    RESULTS.mkdir(mode=0o700, exist_ok=True)
    comparison = {"taskId": task["taskId"], "model": MODEL_ID, "arms": {}}
    for arm, runner in (("pixel", run_pixel), ("codex", run_codex)):
        workspace = PRIVATE / "serial-workspaces" / task["taskId"] / arm
        plant(workspace, task["workspace"])
        before = counters()
        record = runner(task, workspace, wall_seconds)
        record["serverDelta"] = {key: counters()[key] - before[key] for key in before}
        record["verify"] = verify(task, workspace)
        expected = task.get("expectReply")
        record["replyExact"] = (record["reply"] == expected) if expected else None
        comparison["arms"][arm] = record
    target = RESULTS / f"{task['taskId']}.json"
    target.write_text(json.dumps(comparison, indent=1), encoding="utf-8")
    os.chmod(target, 0o600)
    summary = {
        "taskId": task["taskId"],
        "pixel": {k: comparison["arms"]["pixel"][k] for k in ("exitCode", "wallMs", "modelMs", "usage", "replyExact", "peakRssKb")},
        "pixelVerify": comparison["arms"]["pixel"]["verify"],
        "codex": {k: comparison["arms"]["codex"][k] for k in ("exitCode", "wallMs", "usage", "replyExact", "peakRssKb")},
        "codexVerify": comparison["arms"]["codex"]["verify"],
    }
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
