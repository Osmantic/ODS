import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PUBLISHERS = [
    (ROOT / "plugin-ops" / "publish-json.js").as_uri(),
    (ROOT / "plugin-frontier" / "publish-json.js").as_uri(),
]


@unittest.skipUnless(os.name == "posix", "POSIX file modes are required")
class PluginPublishTests(unittest.TestCase):
    def test_atomic_publish_survives_restrictive_service_umask(self):
        for publisher in PUBLISHERS:
            with self.subTest(publisher=publisher), tempfile.TemporaryDirectory() as directory:
                script = f"""
                import {{ publishBrokerJson }} from {json.dumps(publisher)};
                import {{ readdir, readFile, stat }} from 'node:fs/promises';
                process.umask(0o077);
                const directory = process.argv[1];
                const id = 'ops-1785945091317-f4355526f9b0';
                const outcomes = await Promise.allSettled([
                  publishBrokerJson(directory, id, {{jobId:id, value:1}}),
                  publishBrokerJson(directory, id, {{jobId:id, value:2}}),
                ]);
                const info = await stat(`${{directory}}/${{id}}.json`);
                const value = JSON.parse(await readFile(`${{directory}}/${{id}}.json`, 'utf8'));
                const names = await readdir(directory);
                console.log(JSON.stringify({{
                  fulfilled: outcomes.filter((item) => item.status === 'fulfilled').length,
                  rejected: outcomes.filter((item) => item.status === 'rejected').length,
                  mode: info.mode & 0o777,
                  value: value.value,
                  temporary: names.filter((name) => name.startsWith('.')).length,
                }}));
            """
                completed = subprocess.run(
                    ["node", "--input-type=module", "-e", script, directory],
                    check=True,
                    capture_output=True,
                    text=True,
                    cwd=ROOT,
                )
                result = json.loads(completed.stdout)
                self.assertEqual(result["fulfilled"], 1)
                self.assertEqual(result["rejected"], 1)
                self.assertEqual(result["mode"], 0o640)
                self.assertIn(result["value"], (1, 2))
                self.assertEqual(result["temporary"], 0)
