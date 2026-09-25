"""No-inference source-custody test; only copies of the pinned SDK are repaired."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('custody_repair', ROOT / 'host/openclaw_tool_recovery.py')
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)
INSTALLED = os.environ.get('OPENCLAW_PACKAGE_DIR')


@unittest.skipUnless(INSTALLED, 'OPENCLAW_PACKAGE_DIR must point to the pinned installed SDK')
class SandboxCustodyRepairTests(unittest.TestCase):
    def test_actual_four_modules_original_repair_idempotent_restore_unknown(self):
        package = Path(INSTALLED)
        metadata = json.loads((package / 'package.json').read_text())
        self.assertEqual(metadata['name'], 'openclaw')
        self.assertEqual(metadata['version'], '2026.6.33')
        with tempfile.TemporaryDirectory(prefix='ods-sandbox-custody-') as temporary:
            runtime = Path(temporary) / 'runtime'
            (runtime / 'dist').mkdir(parents=True)
            (runtime / 'package.json').write_text(json.dumps(metadata))
            for layer, name in repair.SANDBOX_CUSTODY_MODULES.items():
                with self.subTest(layer=layer):
                    manifest_path = ROOT / f'host/openclaw-sandbox-custody-{layer}.json'
                    manifest = json.loads(manifest_path.read_text())
                    installed = (package / 'dist' / name).read_bytes()
                    original = installed.decode()
                    if repair.digest(installed) == manifest['patchedSha256']:
                        for before, after in reversed(manifest['replacements']):
                            self.assertEqual(original.count(after), 1)
                            original = original.replace(after, before)
                    self.assertEqual(repair.digest(original.encode()), manifest['sourceSha256'])
                    module = runtime / 'dist' / name
                    module.write_bytes(original.encode())
                    module.chmod(0o600)
                    state = Path(temporary) / ('state-' + layer)
                    options = {'manifest_path': manifest_path, 'module_name': name}
                    repair.repair(runtime, state, **options)
                    self.assertEqual(repair.digest(module.read_bytes()), manifest['patchedSha256'])
                    repair.repair(runtime, state, **options)
                    self.assertEqual(repair.digest(module.read_bytes()), manifest['patchedSha256'])
                    repair.repair(runtime, state, restore=True, **options)
                    self.assertEqual(module.read_bytes(), original.encode())
                    module.write_bytes(original.encode() + b'\n// unexpected change\n')
                    changed = module.read_bytes()
                    with self.assertRaisesRegex(ValueError, 'differs from reviewed bytes'):
                        repair.repair(runtime, state, **options)
                    self.assertEqual(module.read_bytes(), changed)
                    self.assertEqual((package / 'dist' / name).read_bytes(), installed)


if __name__ == '__main__':
    unittest.main()
