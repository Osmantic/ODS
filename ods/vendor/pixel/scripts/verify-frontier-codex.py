#!/usr/bin/env python3
"""Offline qualification of the exact Codex CLI config and exposed tool surface."""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE_BROKER = ROOT / "deploy/frontier-broker/broker.py"
BROKER_PATH = SOURCE_BROKER if SOURCE_BROKER.is_file() else Path(__file__).with_name("broker.py")
SPEC = importlib.util.spec_from_file_location("pixel_frontier_broker", BROKER_PATH)
BROKER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(BROKER)

# Current Codex publishes these built-ins after every relevant optional feature is
# disabled. They cannot execute commands, reach a network, or mutate files; exec has no
# interactive reply channel, and view_image receives no path because paths are replaced.
ALLOWED_INERT_TOOLS = {"request_user_input", "update_plan", "view_image"}
# Modern Codex requests the provider return opaque encrypted reasoning state so a
# stateless Responses API turn can preserve model context. The value is ciphertext,
# grants no tool or network capability, and is the only response expansion Pixel
# accepts. Keep this exact allowlist fail-closed as Codex evolves.
ALLOWED_RESPONSE_INCLUDES = {"reasoning.encrypted_content"}


def stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:  # pragma: no cover - deployment target is Linux
            process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:  # pragma: no cover - deployment target is Linux
            process.kill()
        process.wait(timeout=5)


def validate_response_includes(value: Any) -> list[str]:
    if value in (None, []):
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RuntimeError(f"Codex probe emitted a malformed response include surface: {value!r}")
    includes = set(value)
    unexpected = includes - ALLOWED_RESPONSE_INCLUDES
    if unexpected or len(value) != len(includes):
        raise RuntimeError(
            "Codex probe enabled an unexpected response include surface: "
            f"{value!r}"
        )
    return sorted(includes)


def qualify(binary: Path) -> dict[str, Any]:
    if not binary.is_absolute() or not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError("Codex probe requires an executable absolute path")
    captured: dict[str, Any] = {}
    received = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            length = int(self.headers.get("content-length", "0"))
            if length <= 0 or length > 4 * 1024 * 1024:
                self.send_error(413)
                return
            try:
                value = json.loads(self.rfile.read(length))
                if not isinstance(value, dict):
                    raise ValueError("request is not an object")
                captured.update(value)
                received.set()
                self.send_response(503)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"intentional offline qualification stop"}}')
            except (json.JSONDecodeError, ValueError):
                self.send_error(400)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    process: subprocess.Popen[bytes] | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="pixel-frontier-codex-probe-") as temporary:
            directory = Path(temporary)
            schema = directory / "output-schema.json"
            output = directory / "last-message.json"
            codex_home = directory / ".codex"
            codex_home.mkdir(mode=0o700)
            schema.write_text(json.dumps(BROKER.OUTPUT_SCHEMA), encoding="utf-8")
            port = server.server_address[1]
            environment = {
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "HOME": str(directory),
                "CODEX_HOME": str(codex_home),
                "TMPDIR": str(directory),
                "LANG": "C.UTF-8",
                "PIXEL_FRONTIER_PROBE_KEY": "test-only-offline-qualification-key",
            }
            for key in ("PATHEXT", "SystemRoot", "SystemDrive", "WINDIR", "COMSPEC", "TEMP", "TMP"):
                if key in os.environ:
                    environment[key] = os.environ[key]
            command = [
                str(binary), "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                "--strict-config", "--skip-git-repo-check", "--sandbox", "read-only", "--json",
                "--output-schema", str(schema), "-o", str(output), "-m", "frontier-offline-probe",
                *BROKER.HARDENED_CODEX_CONFIG,
                "-c", 'cli_auth_credentials_store="file"',
                "-c", 'model_provider="pixel_probe"',
                "-c", 'model_providers.pixel_probe.name="Pixel offline probe"',
                "-c", f'model_providers.pixel_probe.base_url="http://127.0.0.1:{port}/v1"',
                "-c", 'model_providers.pixel_probe.env_key="PIXEL_FRONTIER_PROBE_KEY"',
                "-c", 'model_providers.pixel_probe.wire_api="responses"',
                "-c", "model_providers.pixel_probe.requires_openai_auth=false",
                "-c", "model_providers.pixel_probe.supports_websockets=false",
                "-c", "model_providers.pixel_probe.request_max_retries=0",
                "-c", "model_providers.pixel_probe.stream_max_retries=0",
                "Return only the required JSON object. Do not use tools.",
            ]
            process = subprocess.Popen(
                command, cwd=directory, env=environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
            )
            deadline = time.monotonic() + 20
            while not received.is_set() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if not received.is_set():
                stop(process)
                stdout, stderr = process.communicate()
                diagnostic = (stdout + b"\n" + stderr).decode("utf-8", errors="replace")[-8000:]
                raise RuntimeError(f"Codex offline qualification did not produce a request: {diagnostic}")
            stop(process)
            process.communicate()
    finally:
        if process is not None:
            stop(process)
        server.shutdown()
        server.server_close()

    tools = captured.get("tools")
    if not isinstance(tools, list):
        raise RuntimeError("Codex probe request omitted its tool manifest")
    names = {
        str(tool.get("name") or tool.get("type"))
        for tool in tools
        if isinstance(tool, dict)
    }
    unexpected = names - ALLOWED_INERT_TOOLS
    if unexpected:
        raise RuntimeError(f"Codex exposes unexpected Frontier tools: {','.join(sorted(unexpected))}")
    response_includes = validate_response_includes(captured.get("include"))
    if captured.get("model") != "frontier-offline-probe":
        raise RuntimeError("Codex probe did not preserve the exact configured model")
    response_format = captured.get("text", {}).get("format", {}) if isinstance(captured.get("text"), dict) else {}
    if response_format.get("type") != "json_schema" or response_format.get("strict") is not True or response_format.get("schema") != BROKER.OUTPUT_SCHEMA:
        raise RuntimeError("Codex probe did not attach the exact strict output schema")
    return {
        "status": "pass",
        "tools": sorted(names),
        "responseIncludes": response_includes,
        "networkTarget": "loopback-only",
        "strictOutputSchema": True,
    }


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: verify-frontier-codex.py /absolute/path/to/codex", file=sys.stderr)
        return 2
    try:
        print(json.dumps(qualify(Path(sys.argv[1])), sort_keys=True))
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Pixel Frontier Codex qualification failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
