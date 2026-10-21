"""Opt-in real Docker acceptance for Pixel's agent sandbox image.

Builds vendor/pixel/deploy/sandbox with the arguments scripts/bootstrap.sh
uses, then runs it with the production confinement: render-config.mjs
agents.defaults.sandbox.docker, as normalized by installers/lib/
pixel-host-install.sh. CI runs it on amd64 (BuildKit and the legacy builder)
and arm64 (.github/workflows/pixel-sandbox-image.yml). It only creates its own
uniquely named containers and never touches an installed Pixel sandbox.

ODS_TEST_PIXEL_SANDBOX_IMAGE=1 enables it. ODS_TEST_PIXEL_SANDBOX_PLATFORM adds
--platform (macOS native Pixel builds linux/arm64), and
ODS_TEST_PIXEL_SANDBOX_IIDFILE keeps the built image ID for later CI steps.
"""
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import textwrap
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
PIXEL = ROOT / 'vendor/pixel'
MANIFEST = json.loads((PIXEL / 'RELEASE-MANIFEST.json').read_text(encoding='utf-8'))
NODE_VERSION = MANIFEST['sandboxNodeRuntime']['version']
ARCH = {'x86_64': ('amd64', 'x64'), 'aarch64': ('arm64', 'arm64'), 'arm64': ('arm64', 'arm64')}
# Generous first ceilings; CI prints the measured sizes so they can be tightened.
NODE_BINARY_CEILING = 160 * 1024 * 1024
IMAGE_CEILING = 1536 * 1024 * 1024

CANVAS_HARNESS = textwrap.dedent('''\
    // Runs a browser canvas script in node with a stubbed page and 2D context.
    const fs = require('node:fs');
    const vm = require('node:vm');
    let depth = 0;
    let queued = null;
    const ctx = new Proxy({}, {
      get: (_, key) => key === 'save' ? () => { depth++; } : key === 'restore' ? () => { depth--; } : () => {},
      set: () => true,
    });
    const canvas = {width: 320, height: 180, getContext: () => ctx};
    const page = vm.createContext({document: {getElementById: () => canvas}, requestAnimationFrame: (f) => { queued = f; }});
    vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), page, {filename: process.argv[2]});
    const frameMs = [];
    for (let frame = 0; frame < 60; frame++) {
      const next = queued;
      queued = null;
      const start = performance.now();
      next(frame * 16);
      frameMs.push(performance.now() - start);
    }
    console.log(JSON.stringify({frames: frameMs.length, saveDepth: depth, maxFrameMs: Math.max(...frameMs)}));
    process.exitCode = depth === 0 ? 0 : 1;
''')

LEAKY_GAME = textwrap.dedent('''\
    const canvas = document.getElementById('arena');
    const ctx = canvas.getContext('2d');
    function frame() {
      ctx.save();
      ctx.fillStyle = '#123';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.save();
      ctx.translate(10, 10);
      ctx.restore();
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
''')


def docker(*args, timeout=120, check=True):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise AssertionError(f'docker {args[0]} failed ({result.returncode}): {result.stderr[-4000:]}')
    return result


@unittest.skipUnless(os.environ.get('ODS_TEST_PIXEL_SANDBOX_IMAGE') == '1',
                     'requires explicitly enabled Docker sandbox image test')
class PixelSandboxImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.uid = os.getuid()
        cls.debian_arch, cls.node_arch = ARCH[platform.machine()]
        cls.temp = tempfile.TemporaryDirectory(prefix='ods-pixel-sandbox-image-')
        iid = Path(os.environ.get('ODS_TEST_PIXEL_SANDBOX_IIDFILE') or Path(cls.temp.name) / 'image-id')
        requested_platform = os.environ.get('ODS_TEST_PIXEL_SANDBOX_PLATFORM')
        version = MANIFEST['pixel']
        # The scripts/bootstrap.sh build, plus --iidfile (and --platform for the macOS form).
        docker('build', *(['--platform', requested_platform] if requested_platform else []),
               '--iidfile', str(iid),
               '--build-arg', f'PIXEL_SANDBOX_UID={cls.uid}',
               '--label', f'org.osmantic.pixel.sandbox-version={version}',
               '--label', f'org.osmantic.pixel.sandbox-uid={cls.uid}',
               '-t', f'pixel-sandbox-candidate:{version}-uid-{cls.uid}',
               str(PIXEL / 'deploy/sandbox'), timeout=1800)
        cls.image = iid.read_text(encoding='utf-8').strip()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        workspace = tempfile.TemporaryDirectory(prefix='ods-pixel-sandbox-workspace-')
        self.addCleanup(workspace.cleanup)
        self.workspace = Path(workspace.name)

    def write(self, name, text):
        path = self.workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def sandbox(self, *command, memory='8g', timeout=120):
        """Run one command with the production agent sandbox confinement."""
        name = 'ods-pixel-sandbox-test-' + uuid.uuid4().hex[:12]
        try:
            return docker('run', '--rm', '--name', name, '--network', 'none', '--read-only',
                          '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                          '--user', 'sandbox', '--workdir', '/workspace', '--env', 'LANG=C.UTF-8',
                          '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m',
                          '--tmpfs', '/var/tmp:rw,nosuid,nodev,size=128m',
                          '--tmpfs', '/run:rw,nosuid,nodev,size=64m',
                          '--pids-limit', '1024', '--memory', memory, '--memory-swap', memory,
                          '--ulimit', 'nofile=4096:4096',
                          '--mount', f'type=bind,source={self.workspace},target=/workspace',
                          self.image, *command, timeout=timeout, check=False)
        finally:
            # Only this test's uniquely named container; --rm normally removed it.
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=60)

    def shell(self, script, **kwargs):
        return self.sandbox('bash', '-c', script, **kwargs)

    def test_identity_labels_and_architecture(self):
        image = json.loads(docker('image', 'inspect', self.image).stdout)[0]
        labels = image['Config']['Labels']
        self.assertEqual(image['Config']['User'], 'sandbox')
        self.assertEqual(image['Os'], 'linux')
        self.assertEqual(image['Architecture'], self.debian_arch)
        self.assertEqual(labels['org.osmantic.pixel.sandbox-version'], MANIFEST['pixel'])
        self.assertEqual(labels['org.osmantic.pixel.sandbox-uid'], str(self.uid))
        self.assertEqual(labels['org.osmantic.pixel.sandbox-node'], NODE_VERSION)
        self.assertLess(image['Size'], IMAGE_CEILING)
        print(json.dumps({'imageBytes': image['Size'], 'architecture': image['Architecture']}))

    def test_node_is_the_pinned_build_for_this_architecture(self):
        result = self.shell('command -v node; node -p \'process.version+" "+process.arch+" "+process.getuid()\';'
                            ' stat -c %s /usr/local/bin/node; test -s /usr/local/share/doc/nodejs/LICENSE')
        self.assertEqual(result.returncode, 0, result.stderr)
        path, identity, size = result.stdout.split('\n')[:3]
        self.assertEqual(path, '/usr/local/bin/node')
        self.assertEqual(identity, f'v{NODE_VERSION} {self.node_arch} {self.uid}')
        self.assertLess(int(size), NODE_BINARY_CEILING)
        print(json.dumps({'nodeBinaryBytes': int(size)}))

    def test_install_proofs_accept_this_image(self):
        # The checks Linux/WSL bootstrap (scripts/lib/common.sh) and macOS native
        # preparation (pixel-native-bootstrap.py) run before any live change,
        # exactly as they run them, against this real image.
        linux = subprocess.run(['bash', '-c', 'source "$1"; pixel_prove_sandbox_runtime "$2" "$3"', '_',
                                str(PIXEL / 'scripts/lib/common.sh'), self.image, NODE_VERSION],
                               capture_output=True, text=True, timeout=180)
        self.assertEqual(linux.returncode, 0, linux.stdout + linux.stderr)
        spec = importlib.util.spec_from_file_location(
            'ods_pixel_native_bootstrap', ROOT / 'installers/macos/lib/pixel-native-bootstrap.py')
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        name = 'ods-pixel-sandbox-proof-' + uuid.uuid4().hex
        try:
            native = docker(*bootstrap.sandbox_proof_run_args(
                name=name, workspace=str(self.workspace), image_id=self.image), check=False)
        finally:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=60)
        self.assertEqual(native.returncode, 0, native.stderr)
        self.assertEqual(json.loads(native.stdout),
                         bootstrap.sandbox_proof_expected(self.uid, NODE_VERSION, self.node_arch))
        self.assertEqual((self.workspace / 'ods-proof.txt').read_text(encoding='utf-8'), 'ods-sandbox-proof')

    def test_node_check_reports_syntax_errors(self):
        self.write('good.js', 'const total = [1, 2, 3].reduce((a, b) => a + b, 0);\nconsole.log(total);\n')
        self.write('broken.js', 'function draw( {\n  return 1;\n}\n')
        good = self.sandbox('node', '--check', 'good.js')
        self.assertEqual((good.returncode, good.stdout, good.stderr), (0, '', ''))
        broken = self.sandbox('node', '--check', 'broken.js')
        self.assertEqual(broken.returncode, 1)
        self.assertIn('SyntaxError', broken.stderr)
        self.assertIn('broken.js', broken.stderr)

    def test_node_test_runner_passes_and_fails(self):
        self.write('suite/math.test.mjs', "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
                   "test('adds', () => assert.equal(1 + 1, 2));\n")
        self.write('suite/text.test.cjs', "const test = require('node:test');\nconst assert = require('node:assert');\n"
                   "test('joins', () => assert.equal(['a', 'b'].join(''), 'ab'));\n")
        self.write('failing.test.mjs', "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
                   "test('wrong', () => assert.equal(1 + 1, 3));\n")
        suite = self.shell('cd suite && node --test')
        self.assertEqual(suite.returncode, 0, suite.stdout + suite.stderr)
        self.assertRegex(suite.stdout, r'(?m)^ℹ pass 2$|^# pass 2$')
        failing = self.sandbox('node', '--test', 'failing.test.mjs')
        self.assertEqual(failing.returncode, 1)
        self.assertRegex(failing.stdout, r'(?m)^ℹ fail 1$|^# fail 1$')

    def test_esm_and_commonjs_modules_run(self):
        self.write('lib.mjs', 'export const add = (a, b) => a + b;\n')
        self.write('lib.cjs', 'exports.mul = (a, b) => a * b;\n')
        result = self.shell("node --input-type=module -e \"import {add} from './lib.mjs'; console.log(add(2, 3))\""
                            " && node -e \"console.log(require('./lib.cjs').mul(2, 3))\"")
        self.assertEqual((result.returncode, result.stdout), (0, '5\n6\n'), result.stderr)

    def test_canvas_harness_finds_a_save_restore_leak_and_times_frames(self):
        self.write('harness.cjs', CANVAS_HARNESS)
        self.write('leaky.js', LEAKY_GAME)
        self.write('fixed.js', LEAKY_GAME.replace('  requestAnimationFrame(frame);\n}',
                                                  '  ctx.restore();\n  requestAnimationFrame(frame);\n}'))
        leaky = self.sandbox('timeout', '60', 'node', 'harness.cjs', 'leaky.js')
        self.assertEqual(leaky.returncode, 1, leaky.stderr)
        report = json.loads(leaky.stdout)
        self.assertEqual((report['frames'], report['saveDepth']), (60, 60))
        self.assertGreaterEqual(report['maxFrameMs'], 0)
        fixed = self.sandbox('timeout', '60', 'node', 'harness.cjs', 'fixed.js')
        self.assertEqual(fixed.returncode, 0, fixed.stderr)
        self.assertEqual(json.loads(fixed.stdout)['saveDepth'], 0)

    def test_no_package_manager_or_registry(self):
        managers = self.shell('for tool in npm npx corepack yarn pnpm; do command -v "$tool"; done;'
                              ' test ! -e /usr/local/lib/node_modules')
        self.assertEqual((managers.returncode, managers.stdout), (0, ''), managers.stderr)
        required = self.sandbox('node', '-e', "require('npm')")
        self.assertEqual(required.returncode, 1)
        self.assertIn('MODULE_NOT_FOUND', required.stderr)

    def test_network_calls_fail_offline(self):
        probes = {
            'fetch': "fetch('https://registry.npmjs.org/').then(() => process.exit(0),"
                     " (error) => { console.error(error.cause?.code ?? error.message); process.exit(3); })",
            'dns': "require('node:dns').lookup('registry.npmjs.org', (error) => {"
                   " console.error(error?.code); process.exit(error ? 3 : 0); })",
            'connect': "const s = require('node:net').connect(443, '1.1.1.1');"
                       " s.on('connect', () => process.exit(0));"
                       " s.on('error', (error) => { console.error(error.code); process.exit(3); })",
        }
        for name, program in probes.items():
            with self.subTest(name):
                result = self.sandbox('timeout', '30', 'node', '-e', program)
                self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        curl = self.sandbox('curl', '-sS', '--max-time', '10', 'https://registry.npmjs.org/')
        self.assertNotEqual(curl.returncode, 0)

    def test_read_only_root_with_writable_workspace_and_tmp(self):
        result = self.sandbox('node', '-e', textwrap.dedent('''\
            const fs = require('node:fs');
            for (const path of ['/probe', '/usr/local/bin/probe']) {
              try { fs.writeFileSync(path, 'x'); console.log('wrote ' + path); }
              catch (error) { console.log(path + ' ' + error.code); }
            }
            fs.writeFileSync('/workspace/out.txt', 'kept');
            fs.writeFileSync('/tmp/scratch.txt', 'scratch');
            console.log('writable');
        '''))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['/probe EROFS', '/usr/local/bin/probe EROFS', 'writable'])
        self.assertEqual((self.workspace / 'out.txt').read_text(encoding='utf-8'), 'kept')

    def test_existing_tools_are_still_present(self):
        result = self.shell('for tool in bash curl git jq python3 rg rsync tesseract pdftotext catdoc unzip file xz; do'
                            ' command -v "$tool" >/dev/null || echo "missing $tool"; done;'
                            ' python3 -c "import docx, PIL"')
        self.assertEqual((result.returncode, result.stdout), (0, ''), result.stderr)
        # The prompt says python3 has no pip; that stays true.
        pip = self.shell('command -v pip pip3; python3 -m pip --version')
        self.assertNotEqual(pip.returncode, 0)
        self.assertEqual(pip.stdout, '')

    def test_memory_exhaustion_is_contained_by_the_container_limit(self):
        result = self.sandbox('node', '-e', 'const kept = []; for (;;) kept.push(Buffer.alloc(16 << 20, 1));',
                              memory='256m', timeout=180)
        # The kernel OOM killer (137) or V8's own out-of-memory abort (134).
        self.assertIn(result.returncode, (134, 137), result.stderr[-2000:])
        self.assertEqual(self.sandbox('true').returncode, 0)


if __name__ == '__main__':
    unittest.main()
