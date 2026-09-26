import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {routePlaygroundTool} from '../plugin/playground-projects.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS} from '../plugin/run-progress-budget.mjs';

const NOT_WRITTEN = 'Not written. For a new project, use a workspace-relative path such as Playground/snake-game/index.html';
const PORTAL_SUFFIX = "\n\n[ODS Portal delivery requirement: Answer the owner's complete message above. If it asks for exact text, copy that full exact text. Do not answer with a generic acknowledgement. Do not output NO_REPLY.]";

function workspace(t) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(tmpdir(), 'ods-playground-rooted-')));
  t.after(() => fs.rmSync(root, {recursive:true, force:true}));
  return root;
}
function fixture(t, intent = 'make me a snake game') {
  const root = workspace(t);
  const state = {};
  const call = (tool, params, overrides = {}) => routePlaygroundTool({state, tool, params, root, session:'owner-session', intent, ...overrides});
  return {root, state, call};
}
const page = chars => {
  const head = '<!DOCTYPE html><html><head><title>Snake</title></head><body><canvas id="game"></canvas><script>';
  const tail = '</script></body></html>';
  return head + ' '.repeat(chars - head.length - tail.length) + tail;
};

// Replays OpenClaw's hook sequence against a real workspace: an allowed write
// lands on disk, an allowed read returns the file, and a blocked call gets the
// SDK's standard veto receipt. Every receipt reaches after_tool_call.
function harness(t, prompt, executionHost) {
  const root = workspace(t);
  const guard = createToolLoopGuard();
  const context = {agentId:'pixel', runId:`rooted-${executionHost}`, sessionId:`rooted-${executionHost}-session`};
  guard.observeRun(context, 'pixel', {prompt}, {workspaceRoot:root, executionHost});
  let failures = 0;
  function call(toolName, params, toolCallId, perform) {
    const decision = guard.beforeToolCall({toolName, toolCallId, params}, {...context, toolName, toolCallId});
    const executed = decision?.block ? params : {...params, ...decision?.params};
    const result = decision?.block
      ? {isError:true, content:[{type:'text', text:decision.blockReason}], details:{status:'blocked', reason:decision.blockReason}}
      : perform(executed);
    failures = result.isError ? failures + 1 : 0;
    guard.afterToolCall({toolName, toolCallId, params:executed, result, ...(result.isError ? {error:result.content[0].text} : {})}, {...context, toolName, toolCallId});
    return {decision, executed, result};
  }
  const write = (params, id) => call('write', params, id, executed => {
    const target = path.join(root, ...executed.path.split('/'));
    fs.mkdirSync(path.dirname(target), {recursive:true});
    fs.writeFileSync(target, executed.content);
    return {content:[{type:'text', text:`Successfully wrote ${Buffer.byteLength(executed.content)} bytes to ${executed.path}`}]};
  });
  const read = (params, id) => call('read', params, id, executed => {
    const target = path.join(root, ...executed.path.replace(/^\/+/, '').split('/'));
    return fs.existsSync(target) && !path.isAbsolute(executed.path)
      ? {content:[{type:'text', text:fs.readFileSync(target, 'utf8')}]}
      : {isError:true, content:[{type:'text', text:`Path escapes sandbox root: ${executed.path}`}]};
  });
  const preview = (params, id) => call('pixel_ods_workspace_preview', params, id, () => ({content:[{type:'text', text:'published'}]}));
  return {root, write, read, preview, failures:() => failures};
}

test('replay: the recorded rooted snake-game write is saved in its Playground folder and the recorded preview publishes it', t => {
  // Open-prompt sweep on build bfb62a2b, prompt 04 "make me a snake game",
  // Qwen3.6-35B-A3B INT4 (session file sha256 570e57ca...). Recorded: the
  // write of /snake-game/index.html (7285 chars) got only the generic
  // correction, which did not say the file was not written. The model then
  // published "snake-game" (refused: no index.html), read /snake-game/index.html
  // (outside the sandbox root) and read snake-game/index.html, which was the
  // fourth consecutive failure: the progress fuse stopped the run with nothing
  // saved. Calls 3-4 reacted to the refused preview; they are replayed only to
  // show that they now resolve to the saved file.
  assert.equal(RUN_PROGRESS_LIMITS.consecutiveFailures, 4);
  const html = page(7285);
  assert.equal(html.length, 7285);
  for (const executionHost of ['sandbox', 'gateway']) {
    const h = harness(t, `make me a snake game${PORTAL_SUFFIX}`, executionHost);
    const first = h.write({path:'/snake-game/index.html', content:html}, '2MbGQOrC8jg1xTg4ATjLDaybNgfGqg7V');
    assert.notEqual(first.decision?.block, true, first.decision?.blockReason);
    assert.equal(first.executed.path, 'Playground/snake-game/index.html');
    assert.equal(first.executed.content, html);
    assert.equal(fs.readFileSync(path.join(h.root, 'Playground', 'snake-game', 'index.html'), 'utf8'), html);
    const published = h.preview({relativeDirectory:'snake-game'}, 'mDIooE0IhSOhJdJmEvPbNm23EGFOWceH');
    assert.notEqual(published.decision?.block, true, published.decision?.blockReason);
    assert.deepEqual(published.executed, {relativeDirectory:'Playground/snake-game'});
    for (const [params, id] of [[{path:'/snake-game/index.html'}, 'AxmD1fpbsiZk7e4m5luOlOJerOS92fMG'], [{path:'snake-game/index.html'}, '3YlYmmb74IIETubpx1m9qGu0OZ5oAgRU']]) {
      const readback = h.read(params, id);
      assert.notEqual(readback.decision?.block, true, `${params.path}: ${readback.decision?.blockReason}`);
      assert.equal(readback.executed.path, 'Playground/snake-game/index.html', params.path);
      assert.equal(readback.result.content[0].text, html, params.path);
    }
    assert.equal(h.failures(), 0, 'no failure is charged, so the fuse is never approached');
    assert.deepEqual(fs.readdirSync(h.root).sort(), ['.ods-projects', 'Playground']);
    assert.deepEqual(fs.readdirSync(path.join(h.root, 'Playground')), ['snake-game']);
  }
});

test('replay: the recorded rooted coffee-landing write lands where its recorded Playground retry landed', t => {
  // Same sweep, prompt 03 "make a landing page for my coffee shop called Bean
  // There" (session file sha256 213a4eff...). /bean-there/index.html got the
  // generic correction, a mkdir was refused, and the full page was generated
  // again as Playground/bean-there/index.html, which an earlier run's folder
  // turned into Playground/bean-there-2. The first write now lands there.
  const {root, call} = fixture(t, `make a landing page for my coffee shop called Bean There${PORTAL_SUFFIX}`);
  fs.mkdirSync(path.join(root, 'Playground', 'bean-there'), {recursive:true});
  fs.writeFileSync(path.join(root, 'Playground', 'bean-there', 'index.html'), 'EARLIER');
  assert.equal(call('write', {path:'/bean-there/index.html', content:'x'}).params.path, 'Playground/bean-there-2/index.html');
  assert.equal(call('pixel_ods_workspace_preview', {relativeDirectory:'Playground/bean-there-2'}), undefined);
  assert.equal(fs.readFileSync(path.join(root, 'Playground', 'bean-there', 'index.html'), 'utf8'), 'EARLIER');
});

test('a rooted /<name>/<file> of a new project routes exactly like <name>/<file>', t => {
  for (const [rooted, expected] of [
    ['/snake-game/index.html', 'Playground/snake-game/index.html'],
    ['/weather-tool/main.py', 'Playground/weather-tool/main.py'],
    ['/photo-renamer/src/rename.py', 'Playground/photo-renamer/src/rename.py'],
    ['\\snake-game\\index.html', 'Playground/snake-game/index.html'],
  ]) for (const wrapped of [false, true]) {
    const relative = fixture(t);
    const plain = relative.call('write', {path:rooted.replaceAll('\\', '/').slice(1), content:'x'});
    const {root, state, call} = fixture(t);
    const args = {path:rooted, content:'x'};
    const decision = wrapped ? call('tool_call', {id:'openclaw:core:write', args}) : call('write', args);
    const actual = wrapped ? decision.params.args : decision.params;
    assert.equal(actual.path, expected, rooted);
    assert.equal(actual.path, plain.params.path, `${rooted} matches the relative spelling`);
    assert.equal(args.path, rooted, 'caller params are not mutated');
    assert.deepEqual(state.binding, relative.state.binding);
    assert.ok(fs.lstatSync(path.join(root, ...expected.split('/').slice(0, 2))).isDirectory());
  }
});

test('rooted host paths, system and generic names, Playground spellings and escapes keep the refusal', t => {
  for (const value of [
    '/home/owner/snake-game/index.html', '/etc/passwd', '/tmp/x/y.py', '/usr/local/bin/tool.py', '/var/www/index.html',
    '/opt/snake/index.html', '/srv/site/index.html', '/mnt/data/snake.py', '/media/usb/game.html', '/root/game.py',
    '/Users/owner/snake.py', '/Library/game/index.html', '/Applications/Game/index.html', '/Volumes/disk/game.py', '/System/x/y.py',
    '/workspace/../snake-game/index.html', '/workspaces/snake-game/index.html', '/data/snake/index.html', '/content/snake-game/index.html',
    '/src/index.html', '/app/index.html', '/public/index.html', '/project/main.py', '/demo/index.html', '/ab/index.html', '/CON/index.html',
    '/playgrounds/todo-app/index.html', '/my-playground/snake/index.html', '/PlaygroundX/snake/index.html',
    '/snake-game/../../etc/x', '/../snake-game/index.html', '//snake-game/index.html', '/snake-game/./index.html',
    '/snake-game//index.html', '/snake-game', '/snake-game/',
  ]) {
    const {root, state, call} = fixture(t);
    const decision = call('write', {path:value, content:'x'});
    assert.equal(decision?.block, true, value);
    assert.ok(decision.blockReason.startsWith(NOT_WRITTEN), `${value}: ${decision.blockReason}`);
    assert.equal(state.binding ?? null, null, value);
    assert.equal(state.failed, undefined, value);
    assert.equal(fs.existsSync(path.join(root, 'Playground')), false, value);
  }
  // A component of the configured workspace path names a host folder, not a project.
  const parent = fs.realpathSync(fs.mkdtempSync(path.join(tmpdir(), 'ods-rooted-parent-')));
  t.after(() => fs.rmSync(parent, {recursive:true, force:true}));
  const root = path.join(parent, 'owner-files');
  fs.mkdirSync(root);
  const route = name => {
    const state = {};
    return {state, decision:routePlaygroundTool({state, tool:'write', params:{path:`/${name}/snake/index.html`, content:'x'}, root, session:'owner-session', intent:'make me a snake game'})};
  };
  for (const name of ['owner-files', 'OWNER-FILES', path.basename(parent)]) {
    const {state, decision} = route(name);
    assert.equal(decision?.block, true, name);
    assert.equal(state.binding ?? null, null, name);
    assert.equal(fs.existsSync(path.join(root, 'Playground')), false, name);
  }
  assert.equal(route('snake-files').decision.params.path, 'Playground/snake-files/snake/index.html', 'the same workspace routes other names');
});

test('no entry that exists at the host filesystem root is read as a new workspace project', t => {
  const hostRoot = path.parse(workspace(t)).root;
  const names = fs.readdirSync(hostRoot).filter(name => /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(name));
  assert.ok(names.length > 0, hostRoot);
  for (const name of names) {
    const {root, state, call} = fixture(t);
    const decision = call('write', {path:`/${name}/snake/index.html`, content:'x'});
    assert.equal(decision?.block, true, name);
    assert.equal(state.binding ?? null, null, name);
    assert.equal(fs.existsSync(path.join(root, 'Playground')), false, name);
  }
});

test('a workspace folder, a read rooted path or an owner-named path is never relocated by the rooted reading', t => {
  {
    // An existing workspace folder may be an owner project: nothing is written into it.
    const {root, state, call} = fixture(t);
    fs.mkdirSync(path.join(root, 'snake-game'));
    fs.writeFileSync(path.join(root, 'snake-game', 'index.html'), 'OWNER');
    const decision = call('write', {path:'/snake-game/index.html', content:'x'});
    assert.equal(decision?.block, true);
    assert.ok(decision.blockReason.startsWith(NOT_WRITTEN), decision.blockReason);
    assert.equal(state.binding ?? null, null);
    assert.equal(fs.readFileSync(path.join(root, 'snake-game', 'index.html'), 'utf8'), 'OWNER');
    assert.equal(fs.existsSync(path.join(root, 'Playground')), false);
  }
  {
    // A rooted path the model already read may be a real host file.
    const {root, state, call} = fixture(t);
    const decision = call('write', {path:'/snake-game/index.html', content:'x'}, {existingPaths:['/snake-game/index.html']});
    assert.equal(decision?.block, true);
    assert.equal(state.binding ?? null, null);
    assert.equal(fs.existsSync(path.join(root, 'Playground')), false);
  }
  for (const intent of ['make me a snake game at /snake-game/index.html', 'Summarize my notes.']) {
    const {root, call} = fixture(t, intent);
    assert.equal(call('write', {path:'/snake-game/index.html', content:'x'}), undefined, intent);
    assert.equal(fs.existsSync(path.join(root, 'Playground')), false, intent);
  }
});

test('later calls route a rooted spelling of the bound project only; other rooted paths are unchanged', t => {
  for (const collision of [false, true]) {
    const {root, call} = fixture(t);
    if (collision) fs.mkdirSync(path.join(root, 'Playground', 'snake-game'), {recursive:true});
    const directory = collision ? 'Playground/snake-game-2' : 'Playground/snake-game';
    assert.equal(call('write', {path:'/snake-game/index.html', content:'x'}).params.path, `${directory}/index.html`);
    for (const [tool, params, key, expected] of [
      ['write', {path:'/snake-game/style.css', content:'y'}, 'path', `${directory}/style.css`],
      ['write', {path:'snake-game/game.js', content:'z'}, 'path', `${directory}/game.js`],
      ['edit', {path:'/snake-game/index.html', oldText:'x', newText:'w'}, 'path', `${directory}/index.html`],
      ['read', {path:'/snake-game/index.html'}, 'path', `${directory}/index.html`],
      ['pixel_ods_workspace_preview', {relativeDirectory:'snake-game'}, 'relativeDirectory', directory],
      ['pixel_ods_workspace_preview', {relativeDirectory:'/snake-game'}, 'relativeDirectory', directory],
      ['exec', {command:'ls', workdir:'/snake-game'}, 'workdir', `/workspace/${directory}`],
    ]) {
      const decision = call(tool, params);
      assert.notEqual(decision?.block, true, `${tool} ${params[key]}: ${decision?.blockReason}`);
      assert.equal(decision.params[key], expected, `${tool} ${params[key]}`);
      const wrapped = call('tool_call', {id:tool === 'pixel_ods_workspace_preview' ? tool : `openclaw:core:${tool}`, args:params});
      assert.equal(wrapped.params.args[key], expected, `wrapped ${tool} ${params[key]}`);
    }
    const input = '*** Begin Patch\n*** Update File: /snake-game/game.js\n@@\n-z\n+y\n*** End Patch';
    assert.equal(call('apply_patch', {input}).params.input, input.replace('Update File: /snake-game/', `Update File: ${directory}/`));
    for (const [tool, params] of [
      ['read', {path:'/etc/hosts'}], ['read', {path:'/other-game/index.html'}], ['write', {path:'/other-game/index.html', content:'x'}],
      ['read', {path:`/${collision ? 'snake-game-2' : 'snake-game-x'}/index.html`}], ['pixel_ods_workspace_preview', {relativeDirectory:'/other-game'}],
      ['exec', {command:'ls', workdir:'/tmp'}],
    ]) assert.equal(call(tool, params), undefined, `${tool} ${JSON.stringify(params)}`);
    assert.equal(call('apply_patch', {input:'*** Begin Patch\n*** Add File: /other-game/x.js\n+x\n*** End Patch'}).block, true);
    assert.deepEqual(fs.readdirSync(path.join(root, 'Playground')).sort(), collision ? ['snake-game', 'snake-game-2'] : ['snake-game']);
  }
});
