// Exercise the pinned process supervisor and its actual child stdout/stderr
// event handling. All runtime mutations stay in a disposable copy; the live
// package is only read. No gateway, model, or provider is involved.
import test from 'node:test';
import assert from 'node:assert/strict';
import {cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const installed = process.env.OPENCLAW_PACKAGE;
test('real supervisor preserves split output and flushes incomplete tails once', {
  skip: !installed || process.platform === 'win32', timeout: 120000,
}, async () => {
  const root = mkdtempSync(join(tmpdir(), 'ods-utf8-runtime-'));
  try {
    assert.equal(JSON.parse(readFileSync(join(installed, 'package.json'))).version, '2026.6.33');
    const results = {};
    for (const mode of ['pinned', 'repaired']) {
      const runtime = join(root, mode);
      mkdirSync(runtime);
      cpSync(join(installed, 'package.json'), join(runtime, 'package.json'));
      cpSync(join(installed, 'dist'), join(runtime, 'dist'), {recursive: true});
      if (existsSync(join(installed, 'node_modules'))) {
        symlinkSync(join(installed, 'node_modules'), join(runtime, 'node_modules'), 'dir');
      }
      if (mode === 'repaired') {
        const repair = spawnSync('python3', ['-c', `
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('repair', sys.argv[1])
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)
repair.repair(Path(sys.argv[2]), Path(sys.argv[3]),
    manifest_path=Path(sys.argv[1]).with_name('openclaw-utf8-output.json'),
    module_name=repair.UTF8_OUTPUT_MODULE)
`, fileURLToPath(new URL('../host/openclaw_tool_recovery.py', import.meta.url)), runtime, join(root, 'custody')], {encoding: 'utf8'});
        assert.equal(repair.status, 0, repair.stderr);
      }
      const {t: getSupervisor} = await import(pathToFileURL(join(runtime, 'dist/supervisor-DzTnKyyV.js')));
      const stdoutEvents = [], stderrEvents = [];
      const run = await getSupervisor().spawn({
        mode: 'child', sessionId: `utf8-${mode}`, cwd: root, timeoutMs: 10000,
        argv: [process.execPath, '-e', `
process.stdout.write(Buffer.from('636166c3', 'hex'));
process.stderr.write(Buffer.from('f09f', 'hex'));
setTimeout(() => {
  process.stdout.write(Buffer.from('a90ae282', 'hex'));
  process.stderr.write(Buffer.from('99820ae282', 'hex'));
}, 250);
`],
        onStdout: text => stdoutEvents.push(text), onStderr: text => stderrEvents.push(text),
      });
      results[mode] = await run.wait();
      assert.equal(results[mode].exitCode, 0);
      assert.equal(results[mode].stdout, stdoutEvents.join(''));
      assert.equal(results[mode].stderr, stderrEvents.join(''));
    }
    assert.notEqual(results.pinned.stdout, 'café\n�');
    assert.notEqual(results.pinned.stderr, '🙂\n�');
    assert.equal(results.repaired.stdout, 'café\n�');
    assert.equal(results.repaired.stderr, '🙂\n�');
  } finally {
    rmSync(root, {recursive: true, force: true});
  }
});
