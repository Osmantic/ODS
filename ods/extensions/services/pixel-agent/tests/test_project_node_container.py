"""Opt-in real Docker acceptance for the candidate project runtime.

Does not deploy or grant Portal access. Dependency acquisition and the broker
remain separate work; this checks offline execution and writable output only.
"""
import json
import io
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import tempfile
import unittest
import uuid
import threading


HOST = Path(__file__).resolve().parents[1] / "host"
sys.path.insert(0, str(HOST))
from project_runtime import run_stage
from project_runtime_protocol import validate_project_lock


@unittest.skipUnless(os.environ.get("ODS_TEST_PROJECT_NODE") == "1",
                     "requires explicitly enabled Docker runtime test")
class ProjectNodeContainerTests(unittest.TestCase):
    def test_offline_nonroot_project_scripts_with_readonly_root(self):
        name = "ods-project-node-qa-" + uuid.uuid4().hex[:12]
        image_id = None
        def run(*args):
            return subprocess.run(args, check=True, capture_output=True,
                                  text=True, timeout=180)

        with tempfile.TemporaryDirectory(prefix=name) as temp:
            iid = Path(temp) / "image-id"
            try:
                run("docker", "build", "--iidfile", str(iid), "-f",
                    str(HOST / "Dockerfile.project-node"), str(HOST))
                image_id = iid.read_text().strip()
                package = json.dumps({"private": True, "scripts": {
                    "test": "node --test /workspace/check.test.cjs",
                    "build": "node -e \"require('fs').mkdirSync('dist'); require('fs').writeFileSync('dist/index.html','<h1>Built</h1>')\"",
                }})
                script = """
const fs = require('node:fs');
const assert = require('node:assert/strict');
assert.notEqual(process.getuid(), 0);
assert.ok(Number(process.versions.node.split('.')[0]) >= 22);
assert.throws(() => fs.writeFileSync('/etc/ods-project-probe', 'denied'));
assert.equal(fs.existsSync('/var/run/docker.sock'), false);
const status = fs.readFileSync('/proc/self/status', 'utf8');
assert.match(status, /NoNewPrivs:\\s+1/);
assert.match(status, /CapEff:\\s+0+\\n/);
"""
                bootstrap = (
                    "const f=require('fs');"
                    "f.writeFileSync('package.json'," + json.dumps(package) + ");"
                    "f.writeFileSync('check.test.cjs'," + json.dumps(script) + ");"
                )
                result = run("docker", "run", "--name", name, "--network", "none",
                    "--read-only", "--cap-drop", "ALL", "--security-opt",
                    "no-new-privileges", "--pids-limit", "128", "--memory", "512m",
                    "--cpus", "1", "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
                    "--tmpfs", "/workspace:rw,nosuid,nodev,size=64m,uid=1000,gid=1000",
                    image_id, "sh", "-ec", "node -e " + shlex.quote(bootstrap)
                    + " && npm test && npm run build && test -s dist/index.html")
                self.assertIn("# fail 0", result.stdout)
                state = json.loads(run("docker", "inspect", name).stdout)[0]
                self.assertEqual(state["HostConfig"]["NetworkMode"], "none")
                self.assertTrue(state["HostConfig"]["ReadonlyRootfs"])
                self.assertEqual(state["State"]["ExitCode"], 0)
            finally:
                # Unique test container only; never prune or touch ODS services.
                subprocess.run(["docker", "rm", "-f", name], capture_output=True)

    @unittest.skipUnless(os.environ.get("ODS_TEST_PROJECT_NEXT") == "1",
                         "downloads Next fixture dependencies into a disposable volume")
    def test_next_export_with_separate_dependency_acquisition(self):
        name = "ods-project-" + uuid.uuid4().hex[:24]
        def run(*args):
            result = subprocess.run(args, capture_output=True, text=True, timeout=240)
            self.assertEqual(result.returncode, 0, result.stdout[-4000:] + result.stderr[-4000:])
            return result

        with tempfile.TemporaryDirectory(prefix=name) as temp:
            iid = Path(temp) / "image-id"
            run("docker", "build", "--iidfile", str(iid), "-f",
                str(HOST / "Dockerfile.project-node"), str(HOST))
            image = iid.read_text().strip()
            run("docker", "volume", "create", "--label", "ods.qa=project-node", name)
            package = {"private": True, "scripts": {"build": "next build --webpack"},
                       "dependencies": {"next": "16.3.6", "react": "19.2.0", "react-dom": "19.2.0"}}
            files = {
                "package.json": json.dumps(package),
                "next.config.mjs": 'export default {output:"export",assetPrefix:".",images:{unoptimized:true}};',
                "pages/index.jsx": 'export default function Page(){return <h1>Portal isolated build</h1>}',
            }
            bootstrap = "const f=require('fs');f.mkdirSync('pages',{recursive:true});"
            bootstrap += "for(const [p,b] of Object.entries(" + json.dumps(files) + "))f.writeFileSync(p,b);"
            common = ["--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                      "--pids-limit", "256", "--memory", "2g", "--cpus", "2",
                      "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m",
                      "--mount", "type=volume,source=" + name + ",target=/home/node",
                      "--workdir", "/home/node"]
            try:
                # No host paths, credentials or Docker socket. The acquisition
                # fixture has network; dependency lifecycle scripts are disabled.
                run("docker", "run", "--name", name + "-fetch", *common, image,
                    "sh", "-ec", "node -e " + shlex.quote(bootstrap)
                    + " && npm install --ignore-scripts --no-audit --no-fund")
                def read_container_file(container, path):
                    archive = subprocess.check_output(["docker", "cp", container + ":" + path, "-"], timeout=30)
                    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
                        members = stream.getmembers()
                        self.assertEqual(len(members), 1)
                        self.assertTrue(members[0].isfile())
                        return stream.extractfile(members[0]).read()
                lock = json.loads(read_container_file(name + "-fetch", "/home/node/package-lock.json"))
                validate_project_lock(package, lock)
                acquired = run_stage(image, name, "acquire", cancel=threading.Event())
                self.assertEqual(acquired["status"], "succeeded", acquired)
                result = run_stage(image, name, "build", cancel=threading.Event())
                self.assertEqual(result["status"], "succeeded", result)
                html = read_container_file(name + "-build", "/home/node/out/index.html").decode()
                self.assertIn("Portal isolated build", html)
                self.assertIn("./_next/", html)
            finally:
                for suffix in ("-fetch", "-acquire", "-build"):
                    subprocess.run(["docker", "rm", "-f", name + suffix], capture_output=True)
                subprocess.run(["docker", "volume", "rm", name], capture_output=True)


if __name__ == "__main__":
    unittest.main()
