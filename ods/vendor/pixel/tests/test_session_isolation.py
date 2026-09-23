import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "security-evals/session-isolation/run-live.sh"


@unittest.skipUnless(os.name == "posix", "live harness requires POSIX shell semantics")
class SessionIsolationHarnessTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "operator probe requires Node.js")
    def test_operator_probe_invokes_discovered_tool_handler(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "openclaw"; package.mkdir()
            (package / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
            binary = package / "openclaw.mjs"; binary.write_text("\n", encoding="utf-8")
            dist = package / "dist"; dist.mkdir()
            (dist / "openclaw-tools-fixture.js").write_text(
                "function createSessionsHistoryTool() {}\n"
                "function createOpenClawTools() { return [{name:'sessions_history', execute: async () => "
                "({content:[{type:'text',text:JSON.stringify({status:'forbidden',error:'tree denied'})}]})}]; }\n"
                "export { createOpenClawTools as t };\n",
                encoding="utf-8",
            )
            config = root / "openclaw.json"
            config.write_text('{"tools":{"sessions":{"visibility":"tree"}}}\n', encoding="utf-8")
            env = os.environ.copy()
            env.update({
                "OPENCLAW_BIN": str(binary),
                "OPENCLAW_CONFIG_PATH": str(config),
                "PIXEL_WORKSPACE": str(root),
            })
            completed = subprocess.run(
                [
                    "node", str(ROOT / "security-evals/session-isolation/operator-probe.mjs"),
                    "agent:pixel:assurance-isolation-source-test-run",
                    "agent:pixel:assurance-isolation-attacker-test-run",
                ],
                check=False, capture_output=True, text=True, env=env,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["status"], "forbidden")
            self.assertFalse(result["historyGatewayReached"])

    def test_forced_cross_tree_probe_and_evidence_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / "openclaw"
            fake.write_text(
                textwrap.dedent(
                    f"""\
                    #!{sys.executable}
                    import hashlib, json, sys
                    args = sys.argv[1:]
                    key = args[args.index('--session-key') + 1]
                    message = args[args.index('--message') + 1]
                    result = {{'payloads': [{{'text': 'fixture'}}], 'meta': {{'toolSummary': {{'tools': [], 'failures': 0}}}}}}
                    if 'isolation-attacker' in key:
                        if 'requires exactly one sessions_history tool call' not in message or 'verbal refusal' not in message:
                            raise SystemExit(9)
                        result['payloads'] = [{{'text': 'refused before tool'}}]
                    elif 'tree-capability' in key and 'Spawn exactly one' in message:
                        result['meta']['toolSummary'] = {{'tools': ['sessions_spawn'], 'failures': 0}}
                    elif 'tree-capability' in key:
                        marker = 'PIXEL_TREE_COORDINATION_test-run'
                        result['payloads'] = [{{'text': hashlib.sha256(marker.encode()).hexdigest()}}]
                    print(json.dumps({{'status': 'ok', 'result': result}}))
                    """
                ),
                encoding="utf-8",
            )
            fake.chmod(0o755)
            state = root / "state"; state.mkdir()
            env_file = root / "pixel.env"
            env_file.write_text(
                f"OPENCLAW_BIN={fake}\nOPENCLAW_HOME={state}\nPIXEL_AGENT_ID=pixel\nPIXEL_WORKSPACE={root}\n",
                encoding="utf-8",
            )
            operator_probe = root / "operator-probe.mjs"
            operator_probe.write_text(
                "const [source, attacker] = process.argv.slice(2);\n"
                "if (!source.includes('isolation-source') || !attacker.includes('isolation-attacker')) process.exit(2);\n"
                "console.log(JSON.stringify({status:'forbidden',historyGatewayReached:false,visibility:'tree'}));\n",
                encoding="utf-8",
            )
            evidence = root / "evidence"
            env = os.environ.copy()
            env.update({
                "PIXEL_ENV_FILE": str(env_file),
                "PIXEL_SESSION_ISOLATION_EVIDENCE_DIR": str(evidence),
                "PIXEL_SESSION_OPERATOR_PROBE_PATH": str(operator_probe),
            })
            completed = subprocess.run(
                ["bash", str(HARNESS), "test-run"],
                check=False,
                capture_output=True,
                text=True,
                cwd=ROOT,
                env=env,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["unrelatedExactSessionKey"], "forbidden")
            self.assertEqual(result["operatorToolHandler"], "forbidden-before-history")
            self.assertEqual(result["modelLayer"], "refused-before-tool")
            self.assertEqual(result["spawnedChildCoordination"], "pass")
            self.assertEqual(
                sorted(path.name for path in evidence.glob("*.json")),
                ["attacker.json", "operator-probe.json", "parent-finish.json", "parent-start.json", "source.json"],
            )
            self.assertEqual(evidence.stat().st_mode & 0o777, 0o700)
            self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in evidence.glob("*.json")))


if __name__ == "__main__":
    unittest.main()
