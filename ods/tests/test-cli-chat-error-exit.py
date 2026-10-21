#!/usr/bin/env python3
"""Real HTTP responses must drive useful process exit codes in both CLIs."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ODS = Path(__file__).resolve().parents[1]


class ChatErrorExitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ods chat response ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.status = 200
        self.response = {"choices": [{"message": {"content": "fixture answer"}}]}
        self.requests = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fixture.requests.append((self.path, body))
                self.send_response(fixture.status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(fixture.response).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = "http://127.0.0.1:" + str(self.server.server_port)

    def macos(self):
        if os.name == "nt" or not shutil.which("bash") or not shutil.which("jq"):
            self.skipTest("POSIX Bash and jq are required")
        source = (ODS / "installers/macos/ods-macos.sh").read_text(encoding="utf-8")
        functions = []
        for name in ("curl_with_bearer", "cmd_chat"):
            match = re.search(r"^" + name + r"\(\) \{.*?^\}", source, re.M | re.S)
            self.assertIsNotNone(match, name)
            functions.append(match.group())
        script = """set -euo pipefail
ai() { printf '%s\\n' "$*"; }
ai_err() { printf '%s\\n' "$*"; }
resolve_cli_llm_route() {
    CLI_LLM_MODE=local
    CLI_LLM_API_KEY=''
    CLI_LLM_BASE_URL="$ODS_CHAT_FIXTURE_URL"
}
""" + "\n".join(functions) + '\ncmd_chat "fixture request"\n'
        path = self.root / "chat.sh"
        path.write_text(script, encoding="utf-8")
        return subprocess.run(["bash", str(path)], env={**os.environ, "ODS_CHAT_FIXTURE_URL": self.url},
                              text=True, capture_output=True, timeout=10)

    def windows(self):
        powershell = shutil.which("powershell.exe") if os.name == "nt" else None
        if not powershell:
            self.skipTest("Windows PowerShell is required")
        # Parse the shipped function with PowerShell's AST rather than sourcing
        # the CLI dispatcher or running any installed lifecycle initialization.
        source = ODS / "installers/windows/ods.ps1"
        script = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($env:ODS_CHAT_SOURCE, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw $errors[0] }
$definition = $ast.Find({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-Chat'
}, $true)
if (-not $definition) { throw 'Missing shipped chat function' }
. ([scriptblock]::Create($definition.Extent.Text))
function Write-AI { param($Message); Write-Host $Message }
function Write-AIError { param($Message); Write-Host $Message }
function Get-NativeInferenceBackend { 'none' }
function Get-WindowsLocalLlmEndpoint { @{ ChatCompletionsUrl = $env:ODS_CHAT_FIXTURE_URL + '/v1/chat/completions' } }
Invoke-Chat -Message 'fixture request'
exit 0
'''
        path = self.root / "chat.ps1"
        path.write_text(script, encoding="utf-8-sig")
        return subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-File", str(path)], text=True, encoding="utf-8", capture_output=True,
                              env={**os.environ, "ODS_CHAT_SOURCE": str(source),
                                   "ODS_CHAT_FIXTURE_URL": self.url}, timeout=15)

    def exercise(self, platform):
        invoke = getattr(self, platform)
        for case in ("answer", "http-error", "api-error", "missing-content", "empty-content"):
            with self.subTest(case=case):
                self.status = 503 if case == "http-error" else 200
                if case in ("http-error", "api-error"):
                    self.response = {"error": {"message": "fixture backend unavailable"}}
                elif case == "missing-content":
                    self.response = {"choices": []}
                else:
                    self.response = {"choices": [{"message": {"content": "fixture answer" if case == "answer" else ""}}]}
                self.requests.clear()
                result = invoke()
                self.assertEqual(len(self.requests), 1, result.stdout + result.stderr)
                self.assertEqual(self.requests[0][0], "/v1/chat/completions")
                self.assertEqual(self.requests[0][1]["messages"][0]["content"], "fixture request")
                if case == "answer":
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("fixture answer", result.stdout)
                else:
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    diagnostic = result.stdout + result.stderr
                    expected = ("Chat request failed" if case == "http-error" else
                                "fixture backend unavailable" if case == "api-error" else
                                "No assistant text in the response")
                    self.assertIn(expected, diagnostic)
                    self.assertNotIn("fixture answer", result.stdout)

    @unittest.skipIf(os.name == "nt" or not shutil.which("bash") or not shutil.which("jq"),
                     "POSIX Bash and jq are required")
    def test_macos_command_exit_matches_http_response(self):
        self.exercise("macos")

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"),
                         "Windows PowerShell is required")
    def test_windows_command_exit_matches_http_response(self):
        self.exercise("windows")


if __name__ == "__main__":
    unittest.main()
