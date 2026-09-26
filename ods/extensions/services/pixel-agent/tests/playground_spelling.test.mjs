import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {routePlaygroundTool} from '../plugin/playground-projects.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

const CORRECTION = /^Not written\. For a new project, use a workspace-relative path such as Playground\/snake-game\/index\.html/;

function fixture(t, intent = 'build me a todo app i can use in the browser') {
  const root = fs.mkdtempSync(path.join(tmpdir(), 'ods-playground-spelling-'));
  t.after(() => fs.rmSync(root, {recursive:true, force:true}));
  const state = {};
  const call = (tool, params, overrides = {}) => routePlaygroundTool({state, tool, params, root, session:'owner-session', intent, ...overrides});
  return {root, state, call};
}
const caseInsensitive = root => {
  const probe = path.join(root, 'Case-Probe');
  fs.mkdirSync(probe);
  try { return fs.existsSync(path.join(root, 'case-probe')); } finally { fs.rmdirSync(probe); }
};

test('first new-project write with a Playground misspelling proceeds to the canonical project path', t => {
  const variants = [
    'playground/todo-app/index.html',
    '/playground/todo-app/index.html',
    '/Playground/todo-app/index.html',
    'PLAYGROUND/todo-app/index.html',
    './playground/todo-app/index.html',
    '/workspace/playground/todo-app/index.html',
    'playground\\todo-app\\index.html',
  ];
  for (const value of [...variants, 'ROOT/playground/todo-app/index.html']) {
    for (const wrapped of [false, true]) {
      const {root, state, call} = fixture(t);
      const raw = value.replace('ROOT', root);
      const args = {path:raw, content:'<!doctype html><html></html>'};
      const decision = wrapped ? call('tool_call', {id:'openclaw:core:write', args}) : call('write', args);
      assert.notEqual(decision?.block, true, `${raw}: ${decision?.blockReason}`);
      const actual = wrapped ? decision.params.args : decision.params;
      assert.equal(actual.path, 'Playground/todo-app/index.html', raw);
      assert.equal(actual.content, args.content);
      assert.equal(args.path, raw, 'caller params are not mutated');
      assert.equal(state.binding.directory, 'Playground/todo-app');
      assert.ok(fs.lstatSync(path.join(root, 'Playground/todo-app')).isDirectory());
    }
  }
});

test('Playground misspellings that are not a descriptive project path keep the correction', t => {
  for (const value of [
    'playground/index.html',
    '/playground/index.html',
    'playground/project/index.html',
    '/playground/app/index.html',
    'playground/src/main.js',
    '/playground/CON/index.html',
    'playground/../outside/index.html',
    '/playground/todo-app/../../outside.html',
    '//playground/todo-app/index.html',
    '/tmp/playground/todo-app/index.html',
    '/home/owner/playground/todo-app/index.html',
    '/playgrounds/todo-app/index.html',
  ]) {
    const {root, state, call} = fixture(t);
    const decision = call('write', {path:value, content:'x'});
    assert.equal(decision?.block, true, value);
    assert.match(decision.blockReason, CORRECTION, value);
    assert.equal(state.binding ?? null, null, value);
    assert.equal(fs.existsSync(path.join(root, 'Playground')), false, value);
  }
});

test('a distinct lowercase playground entry makes the spelling ambiguous and keeps the correction', t => {
  {
    const {root, call} = fixture(t);
    const insensitive = caseInsensitive(root);
    fs.mkdirSync(path.join(root, 'playground'));
    fs.writeFileSync(path.join(root, 'playground/keep.txt'), 'keep');
    const decision = call('write', {path:'playground/todo-app/index.html', content:'x'});
    if (insensitive) {
      // One folder entry: the spelling names the same Playground folder.
      assert.equal(decision.params.path, 'Playground/todo-app/index.html');
    } else {
      assert.equal(decision?.block, true);
      assert.match(decision.blockReason, CORRECTION);
      assert.equal(fs.existsSync(path.join(root, 'Playground')), false);
    }
    assert.equal(fs.readFileSync(path.join(root, 'playground/keep.txt'), 'utf8'), 'keep');
  }
  {
    const {root, call} = fixture(t);
    const outside = fs.mkdtempSync(path.join(tmpdir(), 'ods-playground-outside-'));
    t.after(() => fs.rmSync(outside, {recursive:true, force:true}));
    fs.symlinkSync(outside, path.join(root, 'playground'), 'junction');
    const decision = call('write', {path:'/playground/todo-app/index.html', content:'x'});
    assert.equal(decision?.block, true);
    assert.deepEqual(fs.readdirSync(outside), []);
  }
});

test('owner-named, non-project and already canonical paths are never rewritten by the spelling rule', t => {
  for (const intent of [
    'Build a todo app in playground/todo-app/index.html.',
    'Build a todo app at /playground/todo-app/index.html.',
    'Create a todo app in the folder playground.',
  ]) {
    const {root, call} = fixture(t, intent);
    const value = intent.includes('/playground/') ? '/playground/todo-app/index.html' : 'playground/todo-app/index.html';
    assert.equal(call('write', {path:value, content:'x'}), undefined, intent);
    assert.equal(fs.existsSync(path.join(root, 'Playground')), false, intent);
  }
  {
    // Routing is inactive for an ordinary request without a bound project.
    const {root, call} = fixture(t, 'Summarize my notes.');
    for (const tool of ['write', 'edit', 'read']) assert.equal(call(tool, {path:'/playground/todo-app/index.html'}), undefined, tool);
    assert.equal(call('pixel_ods_workspace_preview', {relativeDirectory:'playground/todo-app'}), undefined);
    assert.equal(call('exec', {command:'ls', workdir:'playground/todo-app'}), undefined);
    assert.equal(fs.existsSync(path.join(root, 'Playground')), false);
  }
  {
    const {call} = fixture(t);
    assert.equal(call('write', {path:'Playground/todo-app/index.html', content:'x'}), undefined, 'canonical first write is unchanged');
  }
});

test('later write, edit, read, preview and exec calls agree on the bound project for every spelling', t => {
  for (const collision of [false, true]) {
    const {root, call} = fixture(t);
    if (collision) fs.mkdirSync(path.join(root, 'Playground/todo-app'), {recursive:true});
    const directory = collision ? 'Playground/todo-app-2' : 'Playground/todo-app';
    assert.equal(call('write', {path:'/playground/todo-app/index.html', content:'x'}).params.path, `${directory}/index.html`);
    const cases = [
      ['write', {path:'playground/todo-app/app.js', content:'y'}, 'path', `${directory}/app.js`],
      ['write', {path:'/Playground/todo-app/style.css', content:'z'}, 'path', `${directory}/style.css`],
      ['edit', {path:'/playground/todo-app/index.html', oldText:'x', newText:'w'}, 'path', `${directory}/index.html`],
      ['read', {path:'playground/todo-app/index.html'}, 'path', `${directory}/index.html`],
      ['pixel_ods_workspace_preview', {relativeDirectory:'playground/todo-app'}, 'relativeDirectory', directory],
      ['pixel_ods_workspace_preview', {relativeDirectory:'/playground/todo-app'}, 'relativeDirectory', directory],
      ['exec', {command:'ls', workdir:'playground/todo-app'}, 'workdir', `/workspace/${directory}`],
      ['exec', {command:'ls', workdir:'/workspace/playground/todo-app'}, 'workdir', `/workspace/${directory}`],
      ['exec', {command:'ls', workdir:'/playground/todo-app'}, 'workdir', `/workspace/${directory}`],
    ];
    for (const [tool, params, key, expected] of cases) {
      const decision = call(tool, params);
      assert.notEqual(decision?.block, true, `${tool} ${params[key]}: ${decision?.blockReason}`);
      assert.equal(decision.params[key], expected, `${tool} ${params[key]}`);
      const wrapped = call('tool_call', {id:tool === 'pixel_ods_workspace_preview' ? tool : `openclaw:core:${tool}`, args:params});
      assert.equal(wrapped.params.args[key], expected, `wrapped ${tool} ${params[key]}`);
    }
    const input = '*** Begin Patch\n*** Add File: playground/todo-app/extra.js\n+x\n*** Update File: /playground/todo-app/app.js\n@@\n-y\n+z\n*** End Patch';
    assert.equal(call('apply_patch', {input}).params.input,
      input.replace('Add File: playground/todo-app/', `Add File: ${directory}/`).replace('Update File: /playground/todo-app/', `Update File: ${directory}/`));
    // A misspelling of a different Playground project is not redirected.
    assert.equal(call('read', {path:'playground/other-app/index.html'}), undefined);
    assert.equal(call('pixel_ods_workspace_preview', {relativeDirectory:'playground/other-app'}), undefined);
    assert.equal(call('exec', {command:'ls', workdir:'playground/other-app'}), undefined);
    assert.deepEqual(fs.readdirSync(path.join(root, 'Playground')).sort(), collision ? ['todo-app', 'todo-app-2'] : ['todo-app']);
  }
});

test('replay: strixy todo-app sequence now writes Playground/todo-app/index.html on the first attempt', t => {
  // Recorded 2026-09-26 on strixy (Qwen3.6-35B-A3B), open prompt 02-todo-app:
  // write /playground/todo-app/index.html (8897 chars) -> correction,
  // write playground/todo-app/index.html (same) -> correction,
  // preview playground/todo-app -> refused, then exec `ls playground/todo-app/`
  // (no workdir, then workdir /workspace) until the progress fuse stopped the
  // run with nothing saved. Each full-page write cost about 70 s of generation.
  // This replays the first write and the later recorded calls with their
  // recorded arguments. The recorded retry write was a reaction to the first
  // correction, so it is not part of the replay.
  const html = `<!DOCTYPE html><html lang="en"><head><title>Todo</title></head><body><h1>Todo</h1>${'<!-- pad -->'.repeat(800)}</body></html>`.slice(0, 8897 - 7) + '</html>';
  assert.equal(html.length, 8897);
  for (const executionHost of ['sandbox', 'gateway']) {
    const root = fs.mkdtempSync(path.join(tmpdir(), 'ods-playground-replay-'));
    t.after(() => fs.rmSync(root, {recursive:true, force:true}));
    const context = {agentId:'pixel', runId:`strixy-todo-${executionHost}`, sessionId:'bdbc1086-5626-4cd3-8c6d-85920e61d3ec'};
    const guard = createToolLoopGuard();
    guard.observeRun(context, 'pixel', {prompt:'build me a todo app i can use in the browser'}, {workspaceRoot:root, executionHost});
    const run = (toolName, params, id) => {
      const decision = guard.beforeToolCall({toolName, toolCallId:id, params}, context);
      assert.notEqual(decision?.block, true, `${toolName}: ${decision?.blockReason}`);
      return decision?.params ?? params;
    };
    const written = {content:[{type:'text', text:'Successfully wrote 8897 bytes'}]};
    const first = run('write', {path:'/playground/todo-app/index.html', content:html}, 'NE5VNUkOws87AONddLQEYxRoyFC0iR3c');
    assert.equal(first.path, 'Playground/todo-app/index.html');
    assert.equal(first.content, html);
    fs.writeFileSync(path.join(root, first.path), first.content);
    guard.afterToolCall({toolName:'write', toolCallId:'NE5VNUkOws87AONddLQEYxRoyFC0iR3c', params:first, result:written}, context);
    const preview = run('pixel_ods_workspace_preview', {relativeDirectory:'playground/todo-app'}, 'So7BuyO8Q9viw4SQtSYJtQKzYl4J7dD3');
    assert.equal(preview.relativeDirectory, 'Playground/todo-app');
    // The recorded listings name the project with the lowercase spelling. No
    // inferred cwd resolves that operand on a case-sensitive workspace (the
    // recording is from Linux), so the model is told the exact folder instead
    // of being shown a missing one. On APFS or NTFS the operand resolves from
    // the workspace root, so the listing runs there as written.
    const insensitive = caseInsensitive(root);
    for (const [params, id] of [[{command:'ls playground/todo-app/'}, 'exec-1'], [{command:'ls playground/todo-app/', workdir:'/workspace'}, 'exec-2']]) {
      const listing = guard.beforeToolCall({toolName:'exec', toolCallId:id, params}, context);
      if (insensitive) {
        assert.notEqual(listing?.block, true, `${JSON.stringify(params)}: ${listing?.blockReason}`);
        assert.doesNotMatch(String((listing?.params ?? params).workdir ?? ''), /Playground/, JSON.stringify(params));
        continue;
      }
      assert.equal(listing?.block, true, JSON.stringify(params));
      assert.match(listing.blockReason, /^This project is in Playground\/todo-app\. Use that exact spelling: playground\/todo-app is a different path on a case-sensitive workspace\. .*Set exec workdir to \/workspace\/Playground\/todo-app /);
    }
    assert.deepEqual(fs.readdirSync(path.join(root, 'Playground')), ['todo-app']);
    assert.equal(fs.readFileSync(path.join(root, 'Playground/todo-app/index.html'), 'utf8'), html);
    if (!caseInsensitive(root)) assert.equal(fs.existsSync(path.join(root, 'playground')), false);
  }
});

test('a lowercase spelling of the bound file shares the canonical file record for repeated writes', t => {
  // A consistency property of the rewrite, not replay evidence: the guard's
  // per-path records see one file under either spelling.
  const html = '<!DOCTYPE html><html><head><title>Todo</title></head><body><h1>Todo</h1></body></html>';
  const root = fs.mkdtempSync(path.join(tmpdir(), 'ods-playground-repeat-'));
  t.after(() => fs.rmSync(root, {recursive:true, force:true}));
  const context = {agentId:'pixel', runId:'playground-repeat', sessionId:'playground-repeat-session'};
  const guard = createToolLoopGuard();
  guard.observeRun(context, 'pixel', {prompt:'build me a todo app i can use in the browser'}, {workspaceRoot:root, executionHost:'sandbox'});
  const first = guard.beforeToolCall({toolName:'write', toolCallId:'w1', params:{path:'/playground/todo-app/index.html', content:html}}, context).params;
  fs.writeFileSync(path.join(root, first.path), first.content);
  guard.afterToolCall({toolName:'write', toolCallId:'w1', params:first, result:{content:[{type:'text', text:'Successfully wrote'}]}}, context);
  const repeat = guard.beforeToolCall({toolName:'write', toolCallId:'w2', params:{path:'playground/todo-app/index.html', content:html}}, context);
  assert.equal(repeat?.block, true);
  assert.match(repeat.blockReason, /repeats content already recorded for this path/);
  const revised = guard.beforeToolCall({toolName:'write', toolCallId:'w3', params:{path:'playground/todo-app/index.html', content:html.replace('<h1>Todo</h1>', '<h1>Todos</h1>')}}, context);
  assert.notEqual(revised?.block, true, revised?.blockReason);
  assert.equal(revised.params.path, 'Playground/todo-app/index.html');
});

function existingProject(root) {
  fs.mkdirSync(path.join(root, 'Playground/todo-app'), {recursive:true});
  fs.writeFileSync(path.join(root, 'Playground/todo-app/index.html'), 'old');
  fs.writeFileSync(path.join(root, 'Playground/todo-app/style.css'), 'css');
}

test('a Playground misspelling the model already read is kept as read, never relocated to a new project', t => {
  // On APFS and NTFS a lowercase read of Playground/todo-app succeeds and is
  // recorded with the model's spelling. Writing that same spelling updates
  // the file that was read, exactly as before this spelling rule existed.
  for (const [read, write] of [
    ['playground/todo-app/index.html', 'playground/todo-app/index.html'],
    ['playground/todo-app/index.html', './playground/todo-app/index.html'],
    ['playground/todo-app/index.html', '/workspace/playground/todo-app/index.html'],
    ['PLAYGROUND/todo-app/index.html', 'PLAYGROUND/todo-app/index.html'],
  ]) {
    const {root, state, call} = fixture(t);
    existingProject(root);
    const existingPaths = [read];
    assert.equal(call('write', {path:write, content:'new'}, {existingPaths}), undefined, write);
    assert.equal(state.preserved, true, write);
    assert.equal(state.binding, null, write);
    assert.equal(state.spellingAlias, undefined, write);
    assert.deepEqual(fs.readdirSync(path.join(root, 'Playground')), ['todo-app'], write);
    // Routing stays off for the rest of the run, as for any preserved path.
    assert.equal(call('read', {path:'playground/todo-app/style.css'}, {existingPaths}), undefined);
    assert.equal(call('pixel_ods_workspace_preview', {relativeDirectory:'playground/todo-app'}, {existingPaths}), undefined);
  }
  {
    // A rooted spelling that was read may be a real host folder: it keeps
    // the unspelled correction instead of being moved into the workspace.
    const {root, state, call} = fixture(t);
    const decision = call('write', {path:'/playground/todo-app/index.html', content:'x'}, {existingPaths:['/playground/todo-app/index.html']});
    assert.equal(decision?.block, true);
    assert.match(decision.blockReason, CORRECTION);
    assert.equal(state.binding ?? null, null);
    assert.equal(fs.existsSync(path.join(root, 'Playground')), false);
  }
  {
    // Through the full guard: the recorded read path feeds existingPaths.
    const root = fs.mkdtempSync(path.join(tmpdir(), 'ods-playground-read-'));
    t.after(() => fs.rmSync(root, {recursive:true, force:true}));
    existingProject(root);
    const context = {agentId:'pixel', runId:'playground-read-spelling', sessionId:'playground-read-spelling-session'};
    const guard = createToolLoopGuard();
    guard.observeRun(context, 'pixel', {prompt:'build me a todo app i can use in the browser'}, {workspaceRoot:root, executionHost:'sandbox'});
    const readParams = guard.beforeToolCall({toolName:'read', toolCallId:'r1', params:{path:'playground/todo-app/index.html'}}, context)?.params
      ?? {path:'playground/todo-app/index.html'};
    assert.equal(readParams.path, 'playground/todo-app/index.html');
    // The read result is simulated; it succeeds like this on APFS and NTFS.
    guard.afterToolCall({toolName:'read', toolCallId:'r1', params:readParams, result:{content:[{type:'text', text:'old'}]}}, context);
    const writeParams = {path:'playground/todo-app/index.html', content:'<!doctype html><h1>Todos</h1>'};
    const decision = guard.beforeToolCall({toolName:'write', toolCallId:'w1', params:writeParams}, context);
    assert.notEqual(decision?.block, true, decision?.blockReason);
    assert.equal((decision?.params ?? writeParams).path, 'playground/todo-app/index.html');
    assert.deepEqual(fs.readdirSync(path.join(root, 'Playground')), ['todo-app']);
  }
});

test('a misspelled write onto a project read as Playground keeps that folder spelling for the rest of the run', t => {
  const {root, state, call} = fixture(t);
  existingProject(root);
  const existingPaths = ['Playground/todo-app/index.html'];
  const first = call('write', {path:'playground/todo-app/index.html', content:'new'}, {existingPaths});
  assert.equal(first.params.path, 'Playground/todo-app/index.html');
  assert.equal(state.preserved, true);
  assert.equal(state.binding, null);
  assert.equal(state.spellingAlias, 'Playground/todo-app');
  const cases = [
    ['write', {path:'playground/todo-app/app.js', content:'y'}, 'path', 'Playground/todo-app/app.js'],
    ['write', {path:'/playground/todo-app/css/style.css', content:'z'}, 'path', 'Playground/todo-app/css/style.css'],
    ['edit', {path:'PLAYGROUND/todo-app/index.html', oldText:'a', newText:'b'}, 'path', 'Playground/todo-app/index.html'],
    ['read', {path:'playground\\todo-app\\style.css'}, 'path', 'Playground/todo-app/style.css'],
    ['pixel_ods_workspace_preview', {relativeDirectory:'playground/todo-app'}, 'relativeDirectory', 'Playground/todo-app'],
    ['pixel_ods_workspace_preview', {relativeDirectory:'/playground/todo-app'}, 'relativeDirectory', 'Playground/todo-app'],
    ['exec', {command:'ls', workdir:'playground/todo-app'}, 'workdir', '/workspace/Playground/todo-app'],
    ['exec', {command:'ls', workdir:'/workspace/playground/todo-app'}, 'workdir', '/workspace/Playground/todo-app'],
  ];
  for (const [tool, params, key, expected] of cases) {
    const decision = call(tool, params, {existingPaths});
    assert.notEqual(decision?.block, true, `${tool} ${params[key]}: ${decision?.blockReason}`);
    assert.equal(decision.params[key], expected, `${tool} ${params[key]}`);
    const wrapped = call('tool_call', {id:tool === 'pixel_ods_workspace_preview' ? tool : `openclaw:core:${tool}`, args:params}, {existingPaths});
    assert.equal(wrapped.params.args[key], expected, `wrapped ${tool} ${params[key]}`);
  }
  const input = '*** Begin Patch\n*** Add File: playground/todo-app/extra.js\n+x\n*** Update File: notes/readme.md\n@@\n-y\n+z\n*** End Patch';
  assert.equal(call('apply_patch', {input}).params.input, input.replace('Add File: playground/', 'Add File: Playground/'));
  // Everything else stays unrouted, as in any preserved run.
  for (const [tool, params] of [
    ['write', {path:'Playground/todo-app/other.js', content:'x'}],
    ['write', {path:'app.js', content:'x'}],
    ['write', {path:'todo-app/app.js', content:'x'}],
    ['read', {path:'playground/other-app/index.html'}],
    ['pixel_ods_workspace_preview', {relativeDirectory:'playground/other-app'}],
    ['exec', {command:'ls', workdir:'/workspace/Playground/todo-app'}],
    ['exec', {command:'ls Playground/todo-app/'}],
    ['exec', {command:'ls'}],
    ['apply_patch', {input:'*** Begin Patch\n*** Add File: notes/a.md\n+x\n*** End Patch'}],
  ]) assert.equal(call(tool, params, {existingPaths}), undefined, `${tool} ${JSON.stringify(params)}`);
  const listing = call('exec', {command:'ls playground/todo-app/'}, {existingPaths});
  if (caseInsensitive(root)) assert.equal(listing, undefined, 'the operand resolves from the workspace root here');
  else {
    assert.equal(listing?.block, true);
    assert.match(listing.blockReason, /^This project is in Playground\/todo-app\. .*Set exec workdir to \/workspace\/Playground\/todo-app /);
  }
  assert.deepEqual(fs.readdirSync(path.join(root, 'Playground')), ['todo-app']);
  {
    // A canonical write onto the read project is unchanged: preserved with no
    // spelling alias, exactly as before this rule.
    const {root: other, state: otherState, call: otherCall} = fixture(t);
    existingProject(other);
    assert.equal(otherCall('write', {path:'Playground/todo-app/index.html', content:'new'}, {existingPaths}), undefined);
    assert.equal(otherState.preserved, true);
    assert.equal(otherState.spellingAlias, undefined);
    assert.equal(otherCall('write', {path:'playground/todo-app/app.js', content:'y'}, {existingPaths}), undefined);
  }
});

test('a shell operand that misspells the bound project gets its exact folder instead of an inferred cwd', t => {
  for (const collision of [false, true]) {
    const {root, call} = fixture(t);
    if (collision) fs.mkdirSync(path.join(root, 'Playground/todo-app'), {recursive:true});
    const directory = collision ? 'Playground/todo-app-2' : 'Playground/todo-app';
    const name = directory.slice('Playground/'.length);
    const insensitive = caseInsensitive(root);
    assert.equal(call('write', {path:'/playground/todo-app/index.html', content:'x'}).params.path, `${directory}/index.html`);
    for (const [params, spelling] of [
      [{command:`ls playground/${name}/`}, 'playground'],
      [{command:`ls playground/${name}/`, workdir:'/workspace'}, 'playground'],
      [{command:`ls playground/${name}`, workdir:'.'}, 'playground'],
      [{command:`ls -la playground/${name}/ 2>&1 || echo "DIR_NOT_FOUND"`}, 'playground'],
      [{command:`ls /workspace/playground/${name}/`}, 'playground'],
      [{command:`cat "./PLAYGROUND/${name}/index.html"`}, 'PLAYGROUND'],
      [{command:`dir playground\\${name}`}, 'playground'],
    ]) {
      const decision = call('exec', params);
      if (insensitive) {
        // APFS/NTFS: the spelling names the same folder from the workspace
        // root, so the command runs there as written, never re-rooted.
        assert.equal(decision, undefined, JSON.stringify(params));
        continue;
      }
      assert.equal(decision?.block, true, JSON.stringify(params));
      assert.equal(decision.blockReason, `This project is in ${directory}. Use that exact spelling: ${spelling}/${name} is a different path on a case-sensitive workspace. Files already written for this project are saved in ${directory}. Set exec workdir to /workspace/${directory} and use filenames relative to that directory.`);
    }
    // A rooted /playground/... operand is outside the workspace on every
    // filesystem, and the correction says so instead of naming case.
    const rooted = call('exec', {command:`cd /playground/${name} && python3 -m http.server`});
    assert.equal(rooted?.block, true);
    assert.equal(rooted.blockReason, `This project is in ${directory}. /playground/${name} is an absolute path outside the workspace, not this project folder. Files already written for this project are saved in ${directory}. Set exec workdir to /workspace/${directory} and use filenames relative to that directory.`);
    // The spelling the correction names runs unchanged.
    assert.equal(call('exec', {command:'ls', workdir:`/workspace/${directory}`}), undefined);
    assert.equal(call('exec', {command:`ls ${directory}/`}), undefined);
    // Operands that do not misspell this project keep the existing cwd rules.
    assert.equal(call('exec', {command:'ls'}).params.workdir, `/workspace/${directory}`);
    assert.equal(call('exec', {command:`ls playground/${name}-old/`}).params.workdir, `/workspace/${directory}`);
    if (collision) assert.match(call('exec', {command:'ls playground/todo-app/'}).blockReason, /the old todo-app\/ prefix names a different project/);
  }
  {
    // A separate lowercase folder on a case-sensitive workspace is the
    // owner's: the command keeps its literal operand and the workspace cwd.
    const {root, call} = fixture(t);
    if (!caseInsensitive(root)) {
      fs.mkdirSync(path.join(root, 'playground/todo-app'), {recursive:true});
      assert.equal(call('write', {path:'Playground/todo-app/index.html', content:'x'}), undefined);
      assert.equal(call('exec', {command:'ls playground/todo-app/'}), undefined);
      assert.equal(call('exec', {command:'ls playground/todo-app/', workdir:'/workspace'}), undefined);
    }
  }
});

test('a rooted /playground read is not evidence for the unread workspace project with that name', t => {
  // /playground/todo-app/index.html is a host path, readable only with full
  // host access. Reading it says nothing about the owner's workspace file
  // Playground/todo-app/index.html, so a later lowercase write is a new
  // project exactly like the canonical write, never routed onto that file.
  for (const write of ['playground/todo-app/index.html', '/workspace/playground/todo-app/index.html', 'Playground/todo-app/index.html']) {
    const {root, state, call} = fixture(t);
    existingProject(root);
    const decision = call('write', {path:write, content:'MODEL'}, {existingPaths:['/playground/todo-app/index.html']});
    assert.notEqual(decision?.block, true, `${write}: ${decision?.blockReason}`);
    assert.equal(decision.params.path, 'Playground/todo-app-2/index.html', write);
    assert.equal(state.preserved, undefined, write);
    assert.equal(state.spellingAlias, undefined, write);
    assert.equal(state.binding.directory, 'Playground/todo-app-2', write);
    fs.writeFileSync(path.join(root, decision.params.path), decision.params.content);
    assert.equal(fs.readFileSync(path.join(root, 'Playground/todo-app/index.html'), 'utf8'), 'old', write);
  }
  // Workspace spellings of the read still count as reading that project.
  for (const read of ['ROOT/playground/todo-app/index.html', '/workspace/playground/todo-app/index.html', 'playground\\todo-app\\index.html']) {
    const {root, state, call} = fixture(t);
    existingProject(root);
    const decision = call('write', {path:'playground/todo-app/index.html', content:'MODEL'}, {existingPaths:[read.replace('ROOT', root)]});
    assert.equal(decision?.params?.path, 'Playground/todo-app/index.html', read);
    assert.equal(state.preserved, true, read);
    assert.equal(state.spellingAlias, 'Playground/todo-app', read);
  }
  for (const executionHost of ['sandbox', 'gateway']) {
    // The reviewer's sequence through the full guard: read, result, write.
    const root = fs.mkdtempSync(path.join(tmpdir(), 'ods-playground-host-read-'));
    t.after(() => fs.rmSync(root, {recursive:true, force:true}));
    existingProject(root);
    const context = {agentId:'pixel', runId:`playground-host-read-${executionHost}`, sessionId:`playground-host-read-${executionHost}`};
    const guard = createToolLoopGuard();
    guard.observeRun(context, 'pixel', {prompt:'build me a todo app i can use in the browser'}, {workspaceRoot:root, executionHost});
    const readParams = guard.beforeToolCall({toolName:'read', toolCallId:'r1', params:{path:'/playground/todo-app/index.html'}}, context)?.params
      ?? {path:'/playground/todo-app/index.html'};
    assert.equal(readParams.path, '/playground/todo-app/index.html');
    guard.afterToolCall({toolName:'read', toolCallId:'r1', params:readParams, result:{content:[{type:'text', text:'HOST FILE'}]}}, context);
    const writeParams = {path:'playground/todo-app/index.html', content:'<!doctype html><h1>MODEL</h1>'};
    const decision = guard.beforeToolCall({toolName:'write', toolCallId:'w1', params:writeParams}, context);
    assert.notEqual(decision?.block, true, decision?.blockReason);
    const written = decision?.params ?? writeParams;
    assert.equal(written.path, 'Playground/todo-app-2/index.html', executionHost);
    fs.writeFileSync(path.join(root, written.path), written.content);
    assert.equal(fs.readFileSync(path.join(root, 'Playground/todo-app/index.html'), 'utf8'), 'old', executionHost);
  }
});

// A bound project (canonical first write) and a preserved project whose
// spelling the run keeps (misspelled write onto a project read canonically).
function projectModes(t) {
  return ['bound', 'alias'].map(mode => {
    const {root, call} = fixture(t);
    const existingPaths = [];
    if (mode === 'alias') {
      existingProject(root);
      existingPaths.push('Playground/todo-app/index.html');
      assert.equal(call('write', {path:'playground/todo-app/index.html', content:'new'}, {existingPaths}).params.path, 'Playground/todo-app/index.html');
    } else assert.equal(call('write', {path:'Playground/todo-app/index.html', content:'x'}), undefined);
    return {mode, root, call:(tool, params) => call(tool, params, {existingPaths})};
  });
}
const SPELLING_REASON = 'This project is in Playground/todo-app. Use that exact spelling: playground/todo-app is a different path on a case-sensitive workspace. Files already written for this project are saved in Playground/todo-app. Set exec workdir to /workspace/Playground/todo-app and use filenames relative to that directory.';

test('text that only mentions the misspelled project is not a path operand and keeps the ordinary exec rules', t => {
  for (const {mode, call} of projectModes(t)) {
    for (const command of [
      'git add -A && git commit -m "add playground/todo-app"',
      'git commit -m "playground/todo-app"',
      'git commit -am "playground/todo-app"',
      'git log --oneline --grep=playground/todo-app',
      'git grep -n "playground/todo-app"',
      'grep -rn "playground/todo-app" Playground/',
      'rg -n playground/todo-app Playground',
      'echo "Saved to playground/todo-app"',
      "printf '%s\\n' playground/todo-app",
      'python3 -c "print(\'playground/todo-app\')"',
      'ls # listing playground/todo-app',
    ]) {
      // Bound: main's cwd inference (the project folder); preserved: unrouted.
      const decision = call('exec', {command});
      if (mode === 'alias') assert.equal(decision, undefined, `${mode} ${command}`);
      else assert.equal(decision?.params?.workdir, '/workspace/Playground/todo-app', `${mode} ${command}: ${decision?.blockReason}`);
      assert.equal(call('exec', {command, workdir:'/workspace/Playground/todo-app'}), undefined, `${mode} ${command} (workdir)`);
    }
    // These name the old <name>/ prefix, which main already refuses for a
    // non-inspection command with an inferred cwd; that rule is unchanged.
    for (const command of ['echo "Open playground/todo-app/index.html in a browser"', 'cat <<EOF\nOpen playground/todo-app/index.html\nEOF']) {
      const decision = call('exec', {command});
      if (mode === 'alias') { assert.equal(decision, undefined, `${mode} ${command}`); continue; }
      assert.equal(decision?.block, true, command);
      assert.match(decision.blockReason, /An automatic parent directory would make this command ambiguous/, command);
    }
  }
});

test('a misspelled path operand is corrected for every workspace-root cwd spelling and $PWD form', t => {
  const commands = [
    {command:'mkdir -p playground/todo-app/js', workdir:'/workspace/'},
    {command:'mkdir -p playground/todo-app/js', workdir:'./'},
    {command:'mkdir -p playground/todo-app/js', workdir:'ROOT'},
    {command:'mkdir -p playground/todo-app/js', workdir:'ROOT/'},
    {command:'mkdir -p "$(pwd)/playground/todo-app/js"'},
    {command:'mkdir -p "$PWD/playground/todo-app/js"', workdir:'/workspace'},
    {command:'mkdir -p ${PWD}/playground/todo-app/js', workdir:'.'},
    {command:'echo hi>playground/todo-app/note.txt'},
    {command:'touch playground/todo-app/app.js'},
    {command:'cp app.js playground/todo-app/'},
    {command:'mv draft.html playground/todo-app/index.html'},
    {command:'rm -f playground/todo-app/old.js'},
    {command:'cat playground/todo-app/index.html'},
    {command:'python3 -m http.server --directory playground/todo-app 8000'},
    {command:'python3 -m http.server --directory=playground/todo-app'},
    {command:'npm --prefix playground/todo-app run dev'},
    {command:'node playground/todo-app/server.js'},
    {command:'git -C playground/todo-app status'},
    {command:'git add playground/todo-app/index.html'},
    {command:'(cd playground/todo-app && npm run dev)'},
    {command:"bash -lc 'mkdir -p playground/todo-app/js'"},
    {command:"cat > playground/todo-app/notes.md <<'EOF'\nsee playground/todo-app\nEOF"},
  ];
  for (const {mode, root, call} of projectModes(t)) {
    const insensitive = caseInsensitive(root);
    for (const params of commands) {
      const actual = params.workdir ? {...params, workdir:params.workdir.replace('ROOT', root)} : params;
      const decision = call('exec', actual);
      if (insensitive) {
        // APFS/NTFS: the spelling names the project folder from the
        // workspace root, so the command runs there as written.
        assert.equal(decision, undefined, `${mode} ${JSON.stringify(params)}`);
        continue;
      }
      assert.equal(decision?.block, true, `${mode} ${JSON.stringify(params)}`);
      assert.equal(decision.blockReason, SPELLING_REASON, `${mode} ${JSON.stringify(params)}`);
    }
    // A cwd moved elsewhere in the command makes the operand relative to it.
    const moved = call('exec', {command:'cd /tmp && mkdir -p playground/todo-app'});
    assert.notEqual(moved?.blockReason, SPELLING_REASON, mode);
    if (!insensitive) {
      // The refused commands never created a lowercase copy, so later
      // misspelled writes still reach the one project folder.
      assert.equal(fs.existsSync(path.join(root, 'playground')), false, mode);
      assert.equal(call('write', {path:'playground/todo-app/js/app.js', content:'y'}).params.path, 'Playground/todo-app/js/app.js', mode);
    }
  }
});

test('an owner folder listed as playground never gets the case-sensitive correction', t => {
  const {root, state, call} = fixture(t);
  fs.mkdirSync(path.join(root, 'playground'));
  const insensitive = caseInsensitive(root);
  if (insensitive) {
    // APFS/NTFS: that entry is the Playground folder itself, listed with the
    // owner's lowercase spelling.
    assert.equal(call('write', {path:'playground/todo-app/index.html', content:'x'}).params.path, 'Playground/todo-app/index.html');
    assert.deepEqual(fs.readdirSync(root).filter(name => name !== '.ods-projects'), ['playground']);
  } else {
    // Linux: a separate owner folder; the project is bound canonically.
    assert.equal(call('write', {path:'Playground/todo-app/index.html', content:'x'}), undefined);
  }
  assert.equal(state.binding.directory, 'Playground/todo-app');
  for (const command of ['ls playground/todo-app/', 'mkdir -p playground/todo-app/js', 'ls /workspace/playground/todo-app/', ...(insensitive ? ['ls PLAYGROUND/todo-app/'] : [])]) {
    // The operand resolves as written from the workspace root.
    assert.equal(call('exec', {command}), undefined, command);
  }
  const rooted = call('exec', {command:'ls /playground/todo-app'});
  assert.equal(rooted?.block, true);
  assert.equal(rooted.blockReason, 'This project is in Playground/todo-app. /playground/todo-app is an absolute path outside the workspace, not this project folder. Files already written for this project are saved in Playground/todo-app. Set exec workdir to /workspace/Playground/todo-app and use filenames relative to that directory.');
});

test('long slash runs in exec workdirs and cd targets stay linear-time', t => {
  // A trailing-slash regex backtracked quadratically on model-supplied slash
  // runs (100k slashes took seconds in the before-tool-call hook).
  const {call} = fixture(t);
  call('write', {path:'/playground/todo-app/index.html', content:'x'});
  const slashes = '/'.repeat(100000);
  for (const params of [{command:'ls', workdir:`a${slashes}x`}, {command:`cd a${slashes}x && ls`}, {command:'ls', workdir:`\${'\'.repeat(100000)}x`}]) {
    const started = process.hrtime.bigint();
    call('exec', params);
    const elapsedMs = Number(process.hrtime.bigint() - started) / 1e6;
    assert.ok(elapsedMs < 250, `took ${elapsedMs.toFixed(1)} ms`);
  }
});
