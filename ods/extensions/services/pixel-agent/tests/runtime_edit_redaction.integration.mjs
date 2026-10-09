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
  vm.runInNewContext(page.match(/<script>([\s\S]*?)<\/script>/i)[1], context, {timeout: 1000});
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
  const suggestions = error => {
    const match = error.message.match(/retry edit with this edits array: (\{"edits":.*\})\. This is a partial retry:/);
    return match ? JSON.parse(match[1]).edits : null;
  };
  async function rejectedEdit(content, oldText, newText) {
    resetFile(content);
    let failure;
    await assert.rejects(invoke(native.X(workspace), {path:'counter.html',edits:[{oldText,newText}]}), error => {
      failure=error; return true;
    });
    assert.equal(readFileSync(file,'utf8'),content,'suggestion never applies an edit');
    return failure;
  }
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
    const uppercase = repaired.replace('<script>', '<SCRIPT>').replace('</script>', '</SCRIPT>');
    const mixedCase = repaired.replace('<script>', '<ScRiPt>').replace('</script>', '</sCrIpT>');
    assert.equal(pageControls(uppercase).value(), 3);
    assert.equal(pageControls(mixedCase).value(), 3);
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

  await t.test('failed whole-block repair offers only exact safe requested hunks for explicit retry', async () => {
    resetFile();
    const visible=redact(textOf(await invoke(native.H(workspace),{path:'counter.html'})),{});
    const requested=visible.replace(/const STORAGE_KEY = '[^']*';\n/, '')
      .replace('parseInt(localStorage.getItem(STORAGE_KEY)) || DEFAULT_VALUE','DEFAULT_VALUE')
      .replace('function update() { localStorage.setItem(STORAGE_KEY, count.toString()); }','function update() {}');
    const failure=await rejectedEdit(originalPage,visible,requested);
    const edits=suggestions(failure);
    assert.equal(edits?.length,1,'adjacent changed lines form one exact hunk');
    assert.match(failure.message,/partial retry: 1 changed block\(s\) were omitted/);
    assert.doesNotMatch(JSON.stringify(edits),/harbor|\u2026|\*{3}|const STORAGE_KEY/);
    assert.equal(redact(JSON.stringify(edits),{}),JSON.stringify(edits),'public redaction leaves retry anchors exact');
    await invoke(native.X(workspace),{path:'counter.html',edits});
    const repaired=readFileSync(file,'utf8');
    assert.ok(repaired.includes("const STORAGE_KEY = 'harborCounterValue';"));
    assert.doesNotMatch(repaired,/localStorage|sessionStorage/);
    const controls=pageControls(repaired);
    assert.equal(controls.value(),3);controls.increase();assert.equal(controls.value(),4);
    controls.reset();assert.equal(controls.value(),3);assert.equal(pageControls(repaired).value(),3);
  });

  await t.test('real STORAGE_KEY and API credentials stay masked and byte-preserved through suggested edits', async () => {
    const secret='fixture-secret-authentication-value';
    const source=originalPage.replace('harborCounterValue',secret).replace('const DEFAULT_VALUE',`const API_KEY = '${secret}';\nconst DEFAULT_VALUE`);
    resetFile(source);
    const visible=redact(textOf(await invoke(native.H(workspace),{path:'counter.html'})),{});
    assert.ok(!visible.includes(secret));
    const desired=visible.replace('parseInt(localStorage.getItem(STORAGE_KEY)) || DEFAULT_VALUE','DEFAULT_VALUE')
      .replace('function update() { localStorage.setItem(STORAGE_KEY, count.toString()); }','function update() {}');
    const failure=await rejectedEdit(source,visible,desired);
    const edits=suggestions(failure);assert.equal(edits?.length,1);
    // The native error already appends file context internally; the public
    // redaction boundary must still mask it, and the new proposal adds none.
    assert.ok(!redact(failure.message,{}).includes(secret));
    assert.ok(!JSON.stringify(edits).includes(secret));
    assert.doesNotMatch(JSON.stringify(edits),/API_KEY|const STORAGE_KEY|\u2026|\*{3}/);
    await invoke(native.X(workspace),{path:'counter.html',edits});
    const result=readFileSync(file,'utf8');
    assert.ok(result.includes(`const STORAGE_KEY = '${secret}';`));
    assert.ok(result.includes(`const API_KEY = '${secret}';`));
  });

  await t.test('masked-only or new secret changes never become suggestions', async () => {
    for(const value of ['***','harbor\u2026alue','[REDACTED]']) {
      const error=await rejectedEdit(originalPage,`const STORAGE_KEY = '${value}';\n`, '');
      assert.equal(suggestions(error),null);
    }
    for(const replacement of ["const API_KEY = 'fixture-secret-authentication-value';\n", "const next = 'sk-fixture1234567890';\n", "const next = '***';\n"]) {
      const error=await rejectedEdit(originalPage,"const STORAGE_KEY = '***';\nconst DEFAULT_VALUE = 3;\n",`const STORAGE_KEY = '***';\n${replacement}`);
      assert.equal(suggestions(error),null);assert.ok(!error.message.includes('fixture-secret-authentication-value'));
    }
  });

  await t.test('ambiguous, insertion-only, multiple and oversized requests retain refusal without suggestions', async () => {
    const masked="const STORAGE_KEY = '***';\n";
    let failure=await rejectedEdit('const STORAGE_KEY = \'harborCounterValue\';\nalpha\nalpha\n',masked+'alpha\n',masked+'beta\n');
    assert.equal(suggestions(failure),null,'duplicate anchor');
    failure=await rejectedEdit(originalPage,masked,masked+'new statement\n');
    assert.equal(suggestions(failure),null,'insert-only has no source anchor');
    for(const extra of ['x'.repeat(32769), 'x\n'.repeat(257)]) {
      failure=await rejectedEdit(originalPage,masked+extra,masked+'updated\n');
      assert.equal(suggestions(failure),null);
    }
    resetFile();
    await assert.rejects(invoke(native.X(workspace),{path:'counter.html',edits:[
      {oldText:masked+'const DEFAULT_VALUE = 3;\n',newText:masked+'const DEFAULT_VALUE = 4;\n'},
      {oldText:'unrelated missing',newText:''},
    ]}),error=>{assert.equal(suggestions(error),null);return true;});
    assert.equal(readFileSync(file,'utf8'),originalPage);
  });

  await t.test('suggestions cannot authorize a stale or newly ambiguous subsequent edit', async () => {
    const masked=redact(originalPage,{});
    const desired=masked.replace('const DEFAULT_VALUE = 3;','const DEFAULT_VALUE = 4;');
    const error=await rejectedEdit(originalPage,masked,desired), edits=suggestions(error);
    assert.equal(edits?.length,1);
    for(const changed of [originalPage.replace('const DEFAULT_VALUE = 3;','const DEFAULT_VALUE = 5;'), originalPage+'const DEFAULT_VALUE = 3;\n']) {
      resetFile(changed);
      await assert.rejects(invoke(native.X(workspace),{path:'counter.html',edits}));
      assert.equal(readFileSync(file,'utf8'),changed);
    }
  });

  await t.test('unique hunk in a wrong or only partially matching block is not offered', async () => {
    const masked=redact(originalPage,{});
    for(const oldText of [masked.replace('<script>','<section>'), masked.replace('function increase()', 'function absent()'),
      masked.slice(1), masked.slice(0,-3)]) {
      const error=await rejectedEdit(originalPage,oldText,oldText.replace('const DEFAULT_VALUE = 3;','const DEFAULT_VALUE = 4;'));
      assert.equal(suggestions(error),null,'the whole requested block must bind to a current full-line location');
    }
    const error=await rejectedEdit(originalPage+originalPage,masked,masked.replace('const DEFAULT_VALUE = 3;','const DEFAULT_VALUE = 4;'));
    assert.equal(suggestions(error),null,'the masked block must itself be unique');
  });

  await t.test('CRLF file keeps its line endings when a suggested explicit retry succeeds', async () => {
    const source=originalPage.replaceAll('\n','\r\n');
    const oldText=redact(source,{}), newText=oldText.replace('const DEFAULT_VALUE = 3;','const DEFAULT_VALUE = 4;');
    const error=await rejectedEdit(source,oldText,newText),edits=suggestions(error);
    assert.equal(edits?.length,1);
    await invoke(native.X(workspace),{path:'counter.html',edits});
    assert.equal(readFileSync(file,'utf8'),source.replace('const DEFAULT_VALUE = 3;','const DEFAULT_VALUE = 4;'));
  });

  await t.test('exact retained Mac counter edit yields two safe requested edits with no source disclosure', {skip:!process.env.ODS_OWNED_EDIT_FIXTURE}, async () => {
    const fixture=JSON.parse(readFileSync(process.env.ODS_OWNED_EDIT_FIXTURE,'utf8'));
    const source=readFileSync(process.env.ODS_OWNED_COUNTER_HTML,'utf8');
    assert.equal(sha(source),'3ef54a3392affb6e03cea4169a61124117d8e2afe20101b36c7e26214ef4b803');
    assert.equal(sha(fixture.edits[0].oldText),'79407337398943c4349b5fe3f828cf0c095253e048445a3cdcafff426102c789');
    assert.equal(sha(fixture.edits[0].newText),'3a2634c33907eb7410c932a044a1e1523f69bfe79c9d21b171f3da762ff20048');
    const {oldText,newText}=fixture.edits[0];
    const failure=await rejectedEdit(source,oldText,newText),edits=suggestions(failure);
    assert.equal(edits?.length,2);assert.match(failure.message,/partial retry: 1 changed block/);
    assert.doesNotMatch(JSON.stringify(edits),/harbor|const STORAGE_KEY|\*{3}/);
    await invoke(native.X(workspace),{path:'counter.html',edits});
    const result=readFileSync(file,'utf8');
    assert.doesNotMatch(result,/localStorage|sessionStorage/);
    assert.ok(result.includes("const STORAGE_KEY = 'harborCounterValue';"));
    function liveControls(html) {
      const handlers={},elements={};
      for(const id of ['counterDisplay','increaseBtn','resetBtn'])
        elements[id]={textContent:'3',addEventListener:(event,fn)=>{handlers[id+':'+event]=fn;}};
      const document={getElementById:id=>elements[id],addEventListener:(event,fn)=>{handlers['document:'+event]=fn;}};
      const context={document,window:{},setTimeout:()=>{},localStorage:new Proxy({}, {get(){throw new Error('SecurityError fixture');}})};
      vm.runInNewContext(html.match(/<script>([\s\S]*?)<\/script>/i)[1],context,{timeout:1000});
      return {handlers,elements};
    }
    assert.throws(()=>liveControls(source),/SecurityError fixture/);
    const controls=liveControls(result);
    assert.equal(controls.elements.counterDisplay.textContent,3);
    controls.handlers['increaseBtn:click']();assert.equal(controls.elements.counterDisplay.textContent,4);
    controls.handlers['resetBtn:click']();assert.equal(controls.elements.counterDisplay.textContent,3);
    assert.equal(liveControls(result).elements.counterDisplay.textContent,3);
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
