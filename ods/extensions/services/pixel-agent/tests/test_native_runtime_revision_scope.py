"""Run native revision controls on a private production-repaired pinned SDK.

This is a Linux CI fixture, not an installer or model/provider test. It reuses
the production 25-recipe staging implementation and the existing Node suite.
The shared npm package is only read; SDK writes and Node state are disposable.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest


ODS = Path(__file__).resolve().parents[4]
NODE_SUITE = Path(__file__).with_name('runtime_revision_tool_scope.integration.mjs')
PACKAGE = os.environ.get('OPENCLAW_PACKAGE_DIR')
SCOPE_MANIFESTS = {'openclaw-finalize-tool-scope.json', 'openclaw-yield-usage.json',
                   'openclaw-compaction-budget.json'}


def inventory(root):
    """Hash shared package code without following dependency or other links."""
    result = {}
    total = 0
    for directory, folders, files in os.walk(root, followlinks=False):
        entries = sorted(folders + files)
        if Path(directory) == root:
            # Dependencies are shared read-only, never staged or patched here.
            folders[:] = [name for name in folders if name != 'node_modules']
        for name in entries:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                result[relative] = ['link', mode, os.readlink(path)]
            elif stat.S_ISDIR(info.st_mode):
                result[relative] = ['directory', mode]
            elif stat.S_ISREG(info.st_mode):
                if info.st_size > 256 << 20:
                    raise ValueError('SDK fixture file exceeds its bound')
                total += info.st_size
                if total > 2 << 30:
                    raise ValueError('SDK fixture package exceeds its bound')
                result[relative] = ['file', mode, info.st_size,
                                    hashlib.sha256(path.read_bytes()).hexdigest()]
            else:
                raise ValueError('unsupported SDK fixture entry')
            if len(result) > 100000:
                raise ValueError('SDK fixture inventory exceeds its bound')
    return result


def run_native_suite(package, node_suite=None):
    if sys.platform != 'linux':
        raise ValueError('native revision CI fixture is Linux-only')
    package = Path(package).resolve(strict=True)
    node_suite = NODE_SUITE if node_suite is None else node_suite
    metadata = json.loads((package / 'package.json').read_bytes())
    if metadata.get('name') != 'openclaw' or metadata.get('version') != '2026.6.33':
        raise ValueError('select the already installed pinned OpenClaw package')
    spec = importlib.util.spec_from_file_location('native_scope_production_bundle',
        ODS / 'installers/macos/lib/pixel-runtime-bundle.py')
    bundle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bundle)
    if len(bundle.SHARED_REPAIRS) != 25 or bundle.SHARED_REPAIRS[-1][0] != 'openclaw-compaction-budget.json':
        raise ValueError('production repair set/order changed')
    before = inventory(package)
    try:
        with tempfile.TemporaryDirectory(prefix='ods-native-revision-ci-') as directory:
            directory = Path(directory)
            private = directory / 'openclaw'
            shutil.copytree(package, private, symlinks=True,
                            ignore=shutil.ignore_patterns('node_modules'))
            dist = private / 'dist'
            if dist.is_symlink() or not dist.is_dir():
                raise ValueError('private SDK dist must be a real directory')
            for _, name in bundle.SHARED_REPAIRS:
                info = (dist / name).lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('private SDK repair targets must be unaliased regular files')
            dependencies = package / 'node_modules'
            if not dependencies.is_dir():
                dependencies = package.parent  # npm's already installed hoisted tree.
            (private / 'node_modules').symlink_to(dependencies, target_is_directory=True)
            # Production staging verifies exact sources, backups, dependencies,
            # receipts and final bytes. It does not load a gateway or provider.
            contracts = bundle._apply_shared_repairs(private, directory)
            if len({row['module'] for row in contracts}) != 25:
                raise ValueError('duplicate production repair target')
            finals = {}
            vectors = {'version': '2026.6.33', 'modules': {}, 'reviewedReadDependencies': {}}
            for row in contracts:
                for name, digest in {row['module']: row['patchedSha256'],
                                     **row['reviewedDependencies']}.items():
                    if name in finals and finals[name] != digest:
                        raise ValueError('production dependency hash conflict')
                    finals[name] = digest
                if row['manifest'] in SCOPE_MANIFESTS:
                    vectors['modules'][row['module']] = {'patchedSha256': row['patchedSha256']}
                if row['manifest'] == 'openclaw-compaction-budget.json':
                    vectors['reviewedReadDependencies'] = row['reviewedDependencies']
            if len(vectors['modules']) != 3 or len(finals) != 39:
                raise ValueError('native revision source closure changed')
            for name, digest in finals.items():
                if hashlib.sha256((private / 'dist' / name).read_bytes()).hexdigest() != digest:
                    raise ValueError('private SDK differs from the production recipes')
            vector_path = directory / 'production-native-revision-expectations.json'
            vector_path.write_text(json.dumps(vectors), encoding='utf-8')
            state = directory / 'state'
            state.mkdir(mode=0o700)
            environment = {**os.environ, 'ODS_REVIEW_TOOL_SCOPE_SDK': str(private),
                'ODS_REVIEW_TOOL_SCOPE_VECTORS': str(vector_path), 'TMPDIR': str(state),
                'OPENCLAW_STATE_DIR': str(state), 'OPENCLAW_CONFIG_PATH': str(state / 'config.json')}
            (state / 'config.json').write_text('{}\n', encoding='utf-8')
            subprocess.run(['node', '--test', '--test-timeout=60000', str(node_suite)],
                           cwd=directory, env=environment, check=True, timeout=180)
    finally:
        if inventory(package) != before:
            raise AssertionError('shared pinned SDK changed during the private native fixture')
    if directory.exists():
        raise AssertionError('private native fixture was not cleaned')
    print(json.dumps({'productionRepairCount': 25, 'sdkCodeFiles': len(finals),
        'nativeScopeModules': 3, 'sourceClosureVerified': True,
        'sharedSourceUnchanged': True, 'privateStateCleaned': True,
        'gatewayOrModelStarted': False, 'prototypePatchArtifactUsed': False}))


@unittest.skipUnless(sys.platform == 'linux', 'Linux native revision fixture')
class NativeRevisionScopeTests(unittest.TestCase):
    @unittest.skipUnless(PACKAGE, 'requires the existing pinned OpenClaw package')
    def test_production_repairs_and_native_controls_leave_shared_source_unchanged(self):
        run_native_suite(PACKAGE)

    def test_shared_dist_alias_refuses_before_any_runtime_repair(self):
        with tempfile.TemporaryDirectory(prefix='ods-revision-alias-') as directory:
            root = Path(directory)
            package, outside = root / 'shared', root / 'outside'
            package.mkdir()
            outside.mkdir()
            (package / 'package.json').write_text(json.dumps({'name': 'openclaw', 'version': '2026.6.33'}))
            (package / 'dist').symlink_to(outside, target_is_directory=True)
            sentinel = outside / 'owned-sentinel'
            sentinel.write_text('unchanged')
            with self.assertRaisesRegex(ValueError, 'private SDK dist'):
                run_native_suite(package)
            self.assertEqual(sentinel.read_text(), 'unchanged')
            self.assertEqual(list(outside.iterdir()), [sentinel])


if __name__ == '__main__':
    if not PACKAGE:
        raise SystemExit('Set OPENCLAW_PACKAGE_DIR to the existing pinned runtime')
    unittest.main()
