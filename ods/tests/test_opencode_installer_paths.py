"""Actual installer writers, synthetic config paths, no HOME override."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ODS = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('oc_installer', ODS / 'bin/opencode_config.py')
CORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CORE)
KEY = 'SYNTHETIC_ODS_ONLY'
OWNER = {'provider': {'owner': {'options': {'apiKey': 'SYNTHETIC_OWNER_ONLY'}}},
         'mcp': {'owner-mcp': {'type': 'remote', 'url': 'https://example.invalid/mcp'}}, 'theme': 'system'}
BODY = json.dumps(OWNER)
COMMENTED = '// owner header\n{ /* owner inline */\n' + BODY[1:-1] + ',\n}\n'
LINUX = (ODS / 'installers/phases/07-devtools.sh').read_text()
START = LINUX.index('        # Delegate to the shared JSONC-preserving core')
END = LINUX.index('        unset _opencode_helper', START) + len('        unset _opencode_helper')


class InstallerPreservation(unittest.TestCase):
    def test_writer_does_not_create_config_below_symlink(self):
        for surface in ['macos', 'linux']:
            with self.subTest(surface=surface), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                actual = root / 'actual'
                actual.mkdir()
                (root / 'linked').symlink_to(actual, target_is_directory=True)
                result = self.run_writer(root / 'linked' / 'config', surface)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(list(actual.iterdir()), [])

    def test_context_and_output_budgets(self):
        for surface in ['macos', 'linux']:
            with self.subTest(surface=surface), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp).resolve()
                for context, output in [(32768, 8192), (131072, 32768), (65536, 16384)]:
                    result = self.run_writer(directory, surface, context)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    limits = CORE.read_effective(directory)['provider']['llama-server']['models']['new-model']['limit']
                    self.assertEqual(limits, {'context': context, 'output': output})

    def run_writer(self, directory, surface, context=32768, model='new-model'):
        env = dict(os.environ, INSTALL_DIR=str(ODS), OPENCODE_CONFIG_DIR=str(directory),
                   ODS_OPENCODE_MODEL=model, ODS_OPENCODE_BASE_URL='http://127.0.0.1:9/v1',
                   ODS_OPENCODE_API_KEY=KEY, ODS_OPENCODE_CONTEXT=str(context),
                   _opencode_model_id=model, _opencode_model_name='New model',
                   _opencode_provider_name='ODS inference', _opencode_url='http://127.0.0.1:9/v1',
                   _opencode_key=KEY, _opencode_context=str(context))
        if surface == 'macos':
            script = '''source "$1"
_write_macos_opencode_config "$OPENCODE_CONFIG_DIR/opencode.json" "$ODS_OPENCODE_MODEL" "$ODS_OPENCODE_BASE_URL" "$ODS_OPENCODE_API_KEY" "$ODS_OPENCODE_CONTEXT"
'''
            args = [str(ODS / 'installers/macos/lib/post-pixel-install.sh')]
        else:
            script = 'ai_ok(){ :; }; ai_bad(){ echo "configuration rejected" >&2; };\n' + LINUX[START:END]
            args = []
        result = subprocess.run(['bash', '-euo', 'pipefail', '-c', script, '--', *args],
                                env=env, text=True, capture_output=True, timeout=30)
        for secret in [KEY, 'SYNTHETIC_OWNER_ONLY']:
            self.assertNotIn(secret, result.stdout + result.stderr)
        return result

    def test_preservation_matrix(self):
        cases = [{'opencode.json': BODY}, {'opencode.json': COMMENTED}, {'config.json': COMMENTED},
                 {'config.json': COMMENTED, 'opencode.json': '{"theme":"dark"}'},
                 {'config.json': BODY, 'opencode.json': '{"theme":"dark"}',
                  'opencode.jsonc': '// highest owner comment\n{"theme":"system",}'}]
        for surface in ['macos', 'linux']:
            for files in cases:
                with self.subTest(surface=surface, files=list(files)), tempfile.TemporaryDirectory() as tmp:
                    directory = Path(tmp).resolve()
                    for name, text in files.items():
                        (directory / name).write_bytes(text.encode())
                    before = {f.name: f.read_bytes() for f in directory.iterdir()}
                    effective = CORE.read_effective(directory)
                    result = self.run_writer(directory, surface)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    target = next(n for n in reversed(CORE.FILENAMES) if n in files)
                    self.assertEqual(set(f.name for f in directory.iterdir()), set(files))
                    for name, data in before.items():
                        if name != target:
                            self.assertEqual((directory / name).read_bytes(), data)
                    for comment in ['// owner header', '/* owner inline */', '// highest owner comment']:
                        if comment in files[target]:
                            self.assertIn(comment, (directory / target).read_text())
                    after = CORE.read_effective(directory)
                    self.assertEqual(after['provider']['owner'], effective['provider']['owner'])
                    for key in ['mcp', 'theme']:
                        self.assertEqual(after[key], effective[key])
                    self.assertEqual(after['model'], 'llama-server/new-model')
                    provider = after['provider']['llama-server']
                    self.assertEqual(provider['options'], {'baseURL': 'http://127.0.0.1:9/v1', 'apiKey': KEY})
                    self.assertEqual(provider['models']['new-model']['limit'], {'context':32768,'output':8192})
                    saved = {f.name: f.read_bytes() for f in directory.iterdir()}
                    self.assertEqual(self.run_writer(directory, surface).returncode, 0)
                    self.assertEqual({f.name: f.read_bytes() for f in directory.iterdir()}, saved)

    def test_invalid_inputs_preserve_every_config(self):
        for surface in ['macos', 'linux']:
            for invalid in ['{ broken', '[1,2]', '{"provider":null}', '{"provider":[]}']:
                with self.subTest(surface=surface, invalid=invalid), tempfile.TemporaryDirectory() as tmp:
                    directory = Path(tmp).resolve()
                    (directory / 'config.json').write_text(invalid)
                    (directory / 'opencode.json').write_text('{"theme":"system"}')
                    before = {f.name: f.read_bytes() for f in directory.iterdir()}
                    self.assertNotEqual(self.run_writer(directory, surface).returncode, 0)
                    self.assertEqual({f.name: f.read_bytes() for f in directory.iterdir()}, before)

    def test_platform_model_policy(self):
        initial = {'$schema':'owner-schema', 'model':'llama-server/old', 'small_model':'owner/small',
                   'agent':{'build':{'model':'llama-server/old','temperature':0.25}, 'plan':{'model':'custom/plan'}},
                   'provider':{'llama-server':{'options':{'timeout':123},'models':{'owner':{'name':'keep'}}}}}
        for surface in ['macos', 'linux']:
            with self.subTest(surface=surface), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp).resolve()
                (directory/'opencode.json').write_text(json.dumps(initial))
                self.assertEqual(self.run_writer(directory,surface).returncode,0)
                after=CORE.read_effective(directory)
                self.assertEqual(after['$schema'],'owner-schema')
                self.assertEqual(after['agent']['plan']['model'],'custom/plan')
                self.assertEqual(after['agent']['build']['temperature'],0.25)
                self.assertEqual(after['agent']['build']['model'],'llama-server/new-model')
                self.assertEqual(after['small_model'],'llama-server/new-model' if surface=='linux' else 'owner/small')
                self.assertEqual(after['provider']['llama-server']['options']['timeout'],123)
                self.assertEqual(after['provider']['llama-server']['models']['owner'],{'name':'keep'})

    def test_fresh_macos_routes_build_plan_and_small_model_locally(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve()
            result = self.run_writer(directory, 'macos', model='ods/current')
            self.assertEqual(result.returncode, 0, result.stderr)
            after = CORE.read_effective(directory)
            self.assertEqual(after['model'], 'llama-server/ods/current')
            self.assertEqual(after['small_model'], after['model'])
            for role in ['build', 'plan']:
                self.assertEqual(after['agent'][role]['model'], after['model'])
            self.assertIn('ods/current', after['provider']['llama-server']['models'])

    def test_macos_only_refreshes_unset_or_previous_ods_selections(self):
        cases = [
            ({}, None, 'llama-server/ods/current'),
            ({'model': 'llama-server/old'}, 'llama-server/old', 'llama-server/ods/current'),
            ({'model': 'llama-server/old'}, 'owner/custom', 'owner/custom'),
            ({'model': 'llama-server/old'}, 'llama-server/owner-other', 'llama-server/owner-other'),
            ({'model': 'owner/old'}, 'owner/old', 'owner/old'),
            ({'model': 'owner/old'}, 'llama-server/old', 'llama-server/old'),
        ]
        for initial, current, expected in cases:
            with self.subTest(current=current, initial=initial), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp).resolve()
                initial = dict(initial, small_model=current,
                               agent={'build': {'model': current, 'temperature': 0.25},
                                      'plan': {'model': current, 'prompt': 'Owner instructions'}})
                (directory/'config.json').write_text('// owner selections\n'+json.dumps(initial)+'\n')
                result = self.run_writer(directory, 'macos', model='ods/current')
                self.assertEqual(result.returncode, 0, result.stderr)
                after = CORE.read_effective(directory)
                self.assertEqual(after['small_model'], expected)
                self.assertEqual(after['agent']['build']['model'], expected)
                self.assertEqual(after['agent']['plan']['model'], expected)
                self.assertEqual(after['agent']['build']['temperature'], 0.25)
                self.assertEqual(after['agent']['plan']['prompt'], 'Owner instructions')
                self.assertTrue((directory/'config.json').read_text().startswith('// owner selections'))
                saved = (directory/'config.json').read_bytes()
                self.assertEqual(self.run_writer(directory, 'macos', model='ods/current').returncode, 0)
                self.assertEqual((directory/'config.json').read_bytes(), saved)

    def test_macos_invalid_agent_shapes_leave_config_untouched(self):
        for agent in [None, [], {'build': None}, {'plan': []}]:
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp).resolve()
                path = directory/'opencode.jsonc'
                original = '// owner content\n'+json.dumps({'agent': agent})+'\n'
                path.write_text(original)
                self.assertNotEqual(self.run_writer(directory, 'macos').returncode, 0)
                self.assertEqual(path.read_text(), original)


if __name__ == '__main__':
    unittest.main()
