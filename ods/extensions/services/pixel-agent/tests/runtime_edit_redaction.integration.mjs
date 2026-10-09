// Actual native file tools and redaction; never mutate the pinned package.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {isAbsolute, join} from 'node:path';
import test from 'node:test';
import {pathToFileURL} from 'node:url';
import vm from 'node:vm';

const packageDir = process.env.OPENCLAW_PACKAGE_DIR;
assert.ok(packageDir && isAbsolute(packageDir), 'Set OPENCLAW_PACKAGE_DIR to the pinned pristine runtime.');
const manifest = JSON.parse(readFileSync(new URL('../host/openclaw-compaction-resume.json', import.meta.url)));
const redaction = JSON.parse(readFileSync(new URL('../host/openclaw-run-id-redaction.json', import.meta.url)));
const sha = value => createHash('sha256').update(value).digest('hex');
const sessionsName = 'sessions-CZbwb3_c.js', redactName = 'redact-cvFSPoXf.js';
const sessionsPath = join(packageDir, 'dist', sessionsName), redactPath = join(packageDir, 'dist', redactName);
const original = readFileSync(sessionsPath, 'utf8'), originalRedactor = readFileSync(redactPath, 'utf8');
assert.equal(sha(original), manifest.sourceSha256);
assert.equal(sha(originalRedactor), redaction.sourceSha256);
assert.equal(JSON.parse(readFileSync(join(packageDir, 'package.json'))).version, '2026.6.33');
function transformed(source, recipe) {
  for (const [before, after] of recipe.replacements) {
    assert.equal(source.split(before).length, 2, 'reviewed replacement is unique');
    source = source.replace(before, () => after);
  }
  assert.equal(sha(source), recipe.patchedSha256);
  return source;
}
const invoke = (tool, args) => tool.execute('owned-fixture-call', args, undefined, () => {}, {});
const textOf = value => value.content.filter(item => item.type === 'text').map(item => item.text).join('\n');
const hint = /Read output may contain redacted values/;
const originalPage = `<script>
const STORAGE_KEY = 'harborCounterValue';
const DEFAULT_VALUE = 3;
let count = parseInt(localStorage.getItem(STORAGE_KEY)) || DEFAULT_VALUE;
function update() { localStorage.setItem(STORAGE_KEY, count.toString()); }
function increase() { count++; update(); }
function reset() { count = DEFAULT_VALUE; update(); }
update();
globalThis.controls = {value: () => count, increase, reset};
</script>\n`;
function pageControls(page) {
  const context = {localStorage: new Proxy({}, {get() { throw new Error('SecurityError fixture'); }})};
  vm.runInNewContext(page.match(/<script>([\s\S]*?)<\/script>/)[1], context, {timeout: 1000});
  return context.controls;
}

test('pinned edit recovery preserves native failure and secret masking', {timeout: 60000}, async t => {
  const scratch = mkdtempSync(join(tmpdir(), 'ods-native-edit-recovery-'));
  const candidate = join(scratch, 'candidate'), workspace = join(scratch, 'workspace');
  mkdirSync(candidate); mkdirSync(workspace);
  // A private module copy keeps every relative import intact on all platforms.
  // Only node_modules is shared read-only; a Windows junction needs no symlink privilege.
  cpSync(join(packageDir, 'dist'), join(candidate, 'dist'), {recursive: true});
  writeFileSync(join(candidate, 'package.json'), readFileSync(join(packageDir, 'package.json')));
  symlinkSync(join(packageDir, 'node_modules'), join(candidate, 'node_modules'), process.platform === 'win32' ? 'junction' : 'dir');
  writeFileSync(join(candidate, 'dist', sessionsName), transformed(original, manifest));
  writeFileSync(join(candidate, 'dist', redactName), transformed(originalRedactor, redaction));
  const previous = process.env.OPENCLAW_STATE_DIR;
  process.env.OPENCLAW_STATE_DIR = join(scratch, 'state');
  t.after(() => {
    if (previous === undefined) delete process.env.OPENCLAW_STATE_DIR;
    else process.env.OPENCLAW_STATE_DIR = previous;
    rmSync(scratch, {recursive: true, force: true});
    assert.equal(sha(readFileSync(sessionsPath)), manifest.sourceSha256);
    assert.equal(sha(readFileSync(redactPath)), redaction.sourceSha256);
  });
  const baseline = await import(pathToFileURL(sessionsPath));
  const native = await import(pathToFileURL(join(candidate, 'dist', sessionsName)));
  const redact = (await import(pathToFileURL(join(candidate, 'dist', redactName)))).d;
  const file = join(workspace, 'counter.html');
  const resetFile = (content = originalPage) => writeFileSync(file, content, {mode: 0o600});
  const maskedEdit = value => ({path: 'counter.html', edits: [
    {oldText: `const STORAGE_KEY = '${value}';`, newText: ''},
    {oldText: 'const DEFAULT_VALUE = 3;', newText: 'const DEFAULT_VALUE = 4;'},
  ]});

  await t.test('baseline actual masked read causes unchanged-file mismatch without recovery guidance', async () => {
    resetFile();
    const visible = redact(textOf(await invoke(baseline.H(workspace), {path: 'counter.html'})), {});
    assert.ok(visible.includes("const STORAGE_KEY = 'harbor\u2026alue';"));
    await assert.rejects(invoke(baseline.X(workspace), maskedEdit('harbor\u2026alue')), error => {
      assert.match(error.message, /^Could not find edits\[0\]/); assert.doesNotMatch(error.message, hint); return true;
    });
    assert.equal(readFileSync(file, 'utf8'), originalPage);
  });

  await t.test('single and multiple failed edits retain failure and give fixed safe recovery', async () => {
    for (const value of ['harbor\u2026alue', '***']) {
      for (const count of [1, 2]) {
        resetFile();
        const visible = redact(textOf(await invoke(native.H(workspace), {path: 'counter.html'})), {});
        assert.ok(visible.includes('harbor\u2026alue'));
        const args = maskedEdit(value); args.edits.length = count;
        await assert.rejects(invoke(native.X(workspace), args), error => {
          assert.match(error.message, count === 1 ? /^Could not find the exact text in/ : /^Could not find edits\[0\]/);
          assert.match(error.message, hint);
          assert.match(error.message, /Never use redaction placeholders/);
          assert.match(error.message, /small, unique unredacted anchors/);
          assert.match(error.message, /preserve unrelated masked content/);
          assert.match(error.message, /instead of overwriting the file or claiming it was repaired/);
          return true;
        });
        assert.equal(readFileSync(file, 'utf8'), originalPage, 'no automatic edit or guessed secret');
      }
    }
  });

  await t.test('safe native anchor edit repairs behavior without unmasking or replacing the namespace', async () => {
    resetFile();
    await invoke(native.H(workspace), {path: 'counter.html'});
    await assert.rejects(invoke(native.X(workspace), maskedEdit('***')), hint);
    await invoke(native.X(workspace), {path: 'counter.html', edits: [
      {oldText: 'let count = parseInt(localStorage.getItem(STORAGE_KEY)) || DEFAULT_VALUE;', newText: 'let count = DEFAULT_VALUE;'},
      {oldText: 'function update() { localStorage.setItem(STORAGE_KEY, count.toString()); }', newText: 'function update() {}'},
    ]});
    const repaired = readFileSync(file, 'utf8');
    assert.ok(repaired.includes("const STORAGE_KEY = 'harborCounterValue';"));
    assert.doesNotMatch(repaired, /localStorage|sessionStorage/);
    assert.throws(() => pageControls(originalPage), /SecurityError/);
    const controls = pageControls(repaired);
    assert.equal(controls.value(), 3); controls.increase(); assert.equal(controls.value(), 4);
    controls.reset(); assert.equal(controls.value(), 3); assert.equal(pageControls(repaired).value(), 3);
  });

  await t.test('real secret shapes remain masked in actual reads and mismatch errors', async () => {
    const secret = 'fixture-secret-authentication-value';
    for (const source of [`const API_KEY = '${secret}';`, `const STORAGE_KEY = '${secret}';`,
      `Authorization: Bearer ${secret}`, "const STORAGE_NAMESPACE = 'sk-fixture1234567890';"]) {
      resetFile(source);
      const visible = redact(textOf(await invoke(native.H(workspace), {path: 'counter.html'})), {});
      assert.notEqual(visible, source); assert.ok(!visible.includes(secret));
      await assert.rejects(invoke(native.X(workspace), {path:'counter.html', edits:[{oldText:'missing unique text',newText:''}]}), error => {
        const publicError = redact(error.message, {});
        assert.match(publicError, hint); assert.ok(!publicError.includes(secret));
        assert.ok(!publicError.includes('sk-fixture1234567890')); return true;
      });
      assert.equal(readFileSync(file, 'utf8'), source);
    }
    for (const source of ["const STORAGE_NAMESPACE = 'harborCounterValue';", "localStorage.getItem('harborCounterValue');"])
      assert.equal(redact(source, {}), source);
  });

  await t.test('successful edits, duplicate text, invalid input and missing files keep their native behavior', async () => {
    resetFile('alpha\nalpha\n');
    await assert.rejects(invoke(native.X(workspace), {path:'counter.html',edits:[{oldText:'alpha',newText:'beta'}]}), error => {
      assert.match(error.message,/Found 2 occurrences/); assert.doesNotMatch(error.message,hint); return true;
    });
    await assert.rejects(invoke(native.X(workspace), {path:'counter.html',edits:[]}), error => {
      assert.match(error.message,/edits must contain at least one/); assert.doesNotMatch(error.message,hint); return true;
    });
    await assert.rejects(invoke(native.X(workspace), {path:'absent.html',edits:[{oldText:'a',newText:'b'}]}), error => {
      assert.doesNotMatch(error.message,hint); return true;
    });
    resetFile('alpha\n');
    const result=await invoke(native.X(workspace),{path:'counter.html',edits:[{oldText:'alpha',newText:'beta'}]});
    assert.notEqual(result.isError,true); assert.doesNotMatch(textOf(result),hint);
    assert.equal(readFileSync(file,'utf8'),'beta\n');
  });
});
