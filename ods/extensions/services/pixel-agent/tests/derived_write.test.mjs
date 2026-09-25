import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createToolLoopGuard, derivedWriteMatch, DERIVED_COPY_WRITE_REASON, DERIVED_MAP_WRITE_REASON,
  FREE_CORRECTIONS_PER_KIND, REPEATED_WRITE_REQUIRES_PATCH_REASON} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS, RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';
import {AGENT_SKILLS, DERIVED_FILE_CONTRACT} from '../plugin/agent-skills.mjs';
import {promptContractForAgent} from '../plugin/prompt-contract.mjs';

// Fleet evidence. tower1 (Qwen3.5-27B), main 380565e2, coding journey round
// 058: the model re-typed sources.json and every .py.txt copy with write, and
// the harness failed it ("Raw published source differs from sources.json
// totals.py"): every sources.json value had lost its file's final newline.
// The same host passed round 057 by running python3 json.dump and cp instead.
const TOWER1 = JSON.parse(fs.readFileSync(new URL('./derived-write-tower1-round058.json', import.meta.url), 'utf8'));
const PROJECT = 'fleet-qualification-9089d99ee0ec-coding';
const PROMPT = TOWER1.prompt;
const missingFile = {isError:true, content:[{type:'text', text:'ENOENT'}], details:{status:'error'}};

// Synthetic sources shaped like the laptop round-057 project (report.py,
// totals.py, test_totals.py), each above the detection minimum.
const source = (name, lines) => `"""${name}"""\n` +
  Array.from({length: lines}, (_, i) => `def ${name}_${i}(value):\n    return value + ${i}  # "quoted" \\ \t\n`).join('\n');
const SOURCES = {
  'report.py': source('report', 8),
  'totals.py': source('totals', 30),
  'test_totals.py': source('test_totals', 60),
};

const workspaces = [];
after(() => { for (const root of workspaces) fs.rmSync(root, {recursive: true, force: true}); });
function workspace() {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'pixel-derived-')));
  workspaces.push(root);
  return root;
}

function place(root, file, content) {
  const target = path.join(root, ...file.split('/'));
  fs.mkdirSync(path.dirname(target), {recursive: true});
  fs.writeFileSync(target, content);
}

// Replays OpenClaw's hook sequence for one model round and one tool call,
// against a real workspace directory: an allowed write lands on disk, a read
// returns the file, a blocked call gets the SDK's standard veto result.
function harness({wrapped = false, root = workspace(), prompt = PROMPT, runId = 'run', guard = createToolLoopGuard()} = {}) {
  const context = {agentId:'pixel', runId, sessionId:'owner-session', sessionKey:'owner-key'};
  guard.observeRun(context, 'pixel', {prompt}, {workspaceRoot: root, executionHost: 'sandbox'});
  let sequence = 0;
  function call(name, args, perform, id = `${name}-${++sequence}`) {
    guard.observeModelCall({}, context);
    const toolName = wrapped ? 'tool_call' : name;
    const params = wrapped ? {id:`openclaw:core:${name}`, args} : args;
    const ctx = {...context, toolName, toolCallId:id};
    const decision = guard.beforeToolCall({toolName, params, toolCallId:id}, ctx);
    let result, executed = params;
    if (decision?.block) {
      result = {content:[{type:'text', text:decision.blockReason}],
        details:{status:'blocked', deniedReason:'plugin-before-tool-call', reason:decision.blockReason}};
    } else {
      executed = wrapped ? decision?.params ?? params : {...params, ...decision?.params};
      const inner = perform(wrapped ? executed.args : executed);
      const envelope = {tool:{id:`openclaw:core:${name}`, name, source:'openclaw', sourceName:'core'}, result:inner};
      result = wrapped ? {content:[{type:'text', text:JSON.stringify(envelope)}], details:envelope} : inner;
    }
    const isError = decision?.block === true || result?.isError === true;
    guard.afterToolCall({toolName, params:executed, toolCallId:id, result,
      ...(isError ? {error:result.content[0].text} : {})}, ctx);
    const message = {role:'toolResult', toolName, toolCallId:id, isError, ...structuredClone(result)};
    const persisted = guard.toolResultPersist({toolName, toolCallId:id, message}, ctx)?.message ?? message;
    return {decision, text: persisted.content.filter(block => block.type === 'text').map(block => block.text).join('\n')};
  }
  const write = (file, content, id) => call('write', {path:file, content}, args => {
    place(root, args.path, args.content);
    return {content:[{type:'text', text:`Successfully wrote ${Buffer.byteLength(args.content)} bytes to ${args.path}`}], details:{}};
  }, id);
  const read = file => call('read', {path:file}, args =>
    ({content:[{type:'text', text:fs.readFileSync(path.join(root, ...args.path.split('/')), 'utf8')}], details:{}}));
  const failRead = file => call('read', {path:file}, () => missingFile);
  const exhausted = () => guard.beforeToolCall({toolName:'read', params:{path:'README.md'}, toolCallId:'probe'},
    {...context, toolName:'read', toolCallId:'probe'})?.blockReason === RUN_PROGRESS_STOP_REASON;
  const written = file => fs.existsSync(path.join(root, ...file.split('/')));
  return {guard, root, call, write, read, failRead, exhausted, written};
}

function writeSources(h, directory = 'proj') {
  for (const [name, content] of Object.entries(SOURCES)) {
    assert.notEqual(h.write(`${directory}/${name}`, content).decision?.block, true, name);
  }
}

const copyReason = (...files) => `${DERIVED_COPY_WRITE_REASON} Existing file: ${files.map(f => JSON.stringify(f)).join(', ')}.`;
const mapReason = (...files) => `${DERIVED_MAP_WRITE_REASON} Repeated files: ${files.map(f => JSON.stringify(f)).join(', ')}.`;

for (const wrapped of [false, true]) {
  test(`tower1 round 058 replay: the first hand-typed sources.json and .py.txt copy are refused (wrapped=${wrapped})`, () => {
    const h = harness({wrapped});
    const decisions = TOWER1.calls.map(({id, tool, args}) =>
      [args.path.slice(PROJECT.length + 1), tool === 'write' ? h.write(args.path, args.content, id) : h.read(args.path)]);
    const outcome = Object.fromEntries(decisions.map(([file, {decision}]) => [file, decision?.blockReason ?? 'allowed']));
    const {text} = decisions.find(([file]) => file === 'public/sources.json')[1];
    assert.deepEqual(outcome, {
      'totals.py': 'allowed', 'report.py': 'allowed', 'test_totals.py': 'allowed',
      // Every value lost its file's final newline: still re-typed, still refused.
      'public/sources.json': mapReason(`${PROJECT}/totals.py`, `${PROJECT}/report.py`, `${PROJECT}/test_totals.py`),
      'public/totals.py.txt': copyReason(`${PROJECT}/totals.py`),
      // The per-run allowance is spent: later copies proceed rather than
      // spending the failure budget; authored files were never candidates.
      'public/report.py.txt': 'allowed', 'public/test_totals.py.txt': 'allowed',
      'public/test-results.txt': 'allowed', 'public/index.html': 'allowed',
    });
    assert.ok(text.startsWith(mapReason(`${PROJECT}/totals.py`, `${PROJECT}/report.py`, `${PROJECT}/test_totals.py`)), text);
    assert.equal(h.written(`${PROJECT}/public/sources.json`), false);
    assert.equal(h.written(`${PROJECT}/public/totals.py.txt`), false);
    assert.equal(h.exhausted(), false);
  });

  test(`a JSON map with some exact values is refused even when another value was mistyped (wrapped=${wrapped})`, () => {
    // Laptop round 057: two values were exact, the hand-escaped third was not.
    const h = harness({wrapped});
    writeSources(h);
    const map = {...SOURCES, 'test_totals.py': SOURCES['test_totals.py'].replace('return value + 7', 'return value')};
    const {decision, text} = h.write('proj/public/sources.json', JSON.stringify(map, null, 2));
    assert.deepEqual(decision, {block: true, blockReason: mapReason('proj/report.py', 'proj/totals.py')});
    assert.ok(text.startsWith(decision.blockReason));
    assert.equal(h.written('proj/public/sources.json'), false);
  });

  test(`an exact copy is refused with the cp hint (wrapped=${wrapped})`, () => {
    const h = harness({wrapped});
    writeSources(h);
    const {decision, text} = h.write('proj/public/totals.py.txt', SOURCES['totals.py']);
    assert.deepEqual(decision, {block: true, blockReason: copyReason('proj/totals.py')});
    assert.ok(text.startsWith(decision.blockReason));
    assert.match(decision.blockReason, /cp SOURCE DESTINATION/);
    assert.equal(h.written('proj/public/totals.py.txt'), false);
  });
}

test('the refusals name the commands and are fixed text apart from the matched paths', () => {
  assert.match(DERIVED_COPY_WRITE_REASON, /one short exec command.*cp SOURCE DESTINATION.*byte-exact and much faster/);
  assert.match(DERIVED_MAP_WRITE_REASON, /python3 -c ".*json\.dump\(.*open\(n, 'rb'\)\.read\(\)\.decode\('utf-8'\).*ensure_ascii=False.*".*byte-exact and much faster/);
  // The suggested command is valid Python that reproduces exact file text.
  assert.doesNotMatch(DERIVED_COPY_WRITE_REASON + DERIVED_MAP_WRITE_REASON, /proj|\d{3}/);
});

test('files this run only read are sources too, in maps nested in objects or arrays', () => {
  const root = workspace();
  for (const [name, content] of Object.entries(SOURCES)) place(root, `proj/${name}`, content);
  const h = harness({root});
  h.read('proj/report.py');
  h.read('proj/totals.py');
  // test_totals.py exists with the same bytes but was not observed in this run.
  assert.notEqual(h.write('proj/out/tests.txt', SOURCES['test_totals.py']).decision?.block, true);
  assert.deepEqual(h.write('proj/out/report.txt', SOURCES['report.py']).decision,
    {block: true, blockReason: copyReason('proj/report.py')});
  const nested = {files: [{name: 'totals.py', text: SOURCES['totals.py']}]};
  assert.deepEqual(h.write('proj/out/bundle.json', JSON.stringify(nested)).decision,
    {block: true, blockReason: mapReason('proj/totals.py')});
});

test('only the file\'s single final newline is tolerated as a difference', () => {
  const root = workspace();
  const body = 'c'.repeat(300);
  place(root, 'proj/two-newlines.txt', `${body}\n\n`);
  place(root, 'proj/no-newline.txt', `${'d'.repeat(299)}e`);
  const candidates = ['proj/two-newlines.txt', 'proj/no-newline.txt'];
  // One dropped final newline is the re-typing defect from tower1 round 058.
  assert.deepEqual(derivedWriteMatch(root, 'proj/x.txt', `${body}\n`, candidates), {kind: 'copy', files: ['proj/two-newlines.txt']});
  assert.deepEqual(derivedWriteMatch(root, 'proj/x.json', JSON.stringify({v: `${body}\n`}), candidates),
    {kind: 'map', files: ['proj/two-newlines.txt']});
  // Any other difference is a different file.
  for (const content of [body, `${body}\n\n\n`, `${body}\r\n`, 'd'.repeat(299), `${'d'.repeat(299)}e\n`, `${'d'.repeat(299)}E`]) {
    assert.equal(derivedWriteMatch(root, 'proj/x.txt', content, candidates), undefined, JSON.stringify(content.slice(-4)));
  }
  assert.deepEqual(derivedWriteMatch(root, 'proj/x.txt', `${'d'.repeat(299)}e`, candidates), {kind: 'copy', files: ['proj/no-newline.txt']});
});

test('legitimately different content is written', () => {
  const h = harness();
  writeSources(h);
  const report = SOURCES['report.py'];
  const allowed = {
    'proj/public/index.html': '<!DOCTYPE html>\n<html><body><h1>Expense report</h1>' + 'x'.repeat(400) + '</body></html>\n',
    'proj/one-byte.txt': report.replace('report_3', 'report_4'),
    'proj/extra-newline.txt': `${report}\n`,
    'proj/last-character-dropped.txt': `${report.slice(0, -2)}\n`,
    'proj/leading-space.txt': ` ${report}`,
    'proj/crlf.txt': report.replace(/\n/g, '\r\n'),
    // Filenames are not contents; values that differ are authored data.
    'proj/public/names.json': JSON.stringify({files: Object.keys(SOURCES), note: 'x'.repeat(300)}),
    'proj/public/other.json': JSON.stringify(Object.fromEntries(Object.entries(SOURCES).map(([k, v]) => [k, v.toUpperCase()]))),
    'proj/public/escaped.json': JSON.stringify({text: JSON.stringify(report)}),
  };
  for (const [file, content] of Object.entries(allowed)) {
    assert.notEqual(h.write(file, content).decision?.block, true, file);
    assert.equal(h.written(file), true, file);
  }
  // Rewriting the same path keeps the ordinary repeated-write answer.
  assert.deepEqual(h.write('proj/report.py', report).decision, {block: true, blockReason: REPEATED_WRITE_REQUIRES_PATCH_REASON});
  assert.equal(h.exhausted(), false);
});

test('small files are below the detection minimum', () => {
  const h = harness();
  const small = 'a'.repeat(254) + '\n';
  const minimum = 'b'.repeat(255) + '\n';
  assert.equal(Buffer.byteLength(small), 255);
  assert.equal(Buffer.byteLength(minimum), 256);
  h.write('proj/small.txt', small);
  h.write('proj/minimum.txt', minimum);
  h.write('proj/__init__.py', '');
  assert.notEqual(h.write('proj/copy/small.txt', small).decision?.block, true);
  assert.notEqual(h.write('proj/copy/__init__.py', '').decision?.block, true);
  assert.deepEqual(h.write('proj/copy/minimum.txt', minimum).decision,
    {block: true, blockReason: copyReason('proj/minimum.txt')});
});

test('large files are bounded: sources over 256 KiB are never read, writes over 1 MiB never parsed', () => {
  const root = workspace();
  const limit = 256 * 1024;
  const atLimit = 'a'.repeat(limit - 1) + '\n';
  const overLimit = 'b'.repeat(limit) + '\n';
  place(root, 'proj/at-limit.txt', atLimit);
  place(root, 'proj/over-limit.txt', overLimit);
  const candidates = ['proj/at-limit.txt', 'proj/over-limit.txt'];
  assert.deepEqual(derivedWriteMatch(root, 'proj/copy.txt', atLimit, candidates), {kind: 'copy', files: ['proj/at-limit.txt']});
  assert.equal(derivedWriteMatch(root, 'proj/copy.txt', overLimit, candidates), undefined);
  assert.equal(derivedWriteMatch(root, 'proj/copy.json', JSON.stringify({big: overLimit}), candidates), undefined);
  // A map just under the write bound is checked; one over it is skipped whole.
  const padding = 'p'.repeat(1024 * 1024 - limit - 64);
  assert.equal(derivedWriteMatch(root, 'proj/map.json', JSON.stringify({big: atLimit, padding}), candidates).kind, 'map');
  assert.equal(derivedWriteMatch(root, 'proj/map.json', JSON.stringify({big: atLimit, padding: `${padding}${'p'.repeat(128)}`}), candidates), undefined);
  // The same bound holds through the guard.
  const h = harness({root});
  for (const file of candidates) h.read(file);
  assert.notEqual(h.write('proj/copy/over-limit.txt', overLimit).decision?.block, true);
  assert.deepEqual(h.write('proj/copy/at-limit.txt', atLimit).decision, {block: true, blockReason: copyReason('proj/at-limit.txt')});
});

test('only regular files inside the workspace are compared: links are never followed', t => {
  const root = workspace();
  const outside = workspace();
  const secret = SOURCES['totals.py'];
  place(outside, 'secret.py', secret);
  place(root, 'proj/real.py', secret);
  try {
    fs.symlinkSync(path.join(outside, 'secret.py'), path.join(root, 'proj', 'link.py'), 'file');
    fs.symlinkSync(outside, path.join(root, 'proj', 'linkdir'), 'dir');
  } catch (error) {
    if (['EPERM', 'EACCES', 'ENOSYS'].includes(error.code)) return t.skip(`symlinks unavailable: ${error.code}`);
    throw error;
  }
  fs.linkSync(path.join(outside, 'secret.py'), path.join(root, 'proj', 'hardlink.py'));
  const linked = ['proj/link.py', 'proj/linkdir/secret.py', 'proj/hardlink.py'];
  assert.equal(derivedWriteMatch(root, 'proj/copy.txt', secret, linked), undefined);
  assert.equal(derivedWriteMatch(root, 'proj/copy.json', JSON.stringify({secret}), linked), undefined);
  // Paths outside the workspace are never candidates either.
  assert.equal(derivedWriteMatch(root, 'proj/copy.txt', secret,
    [path.join(outside, 'secret.py'), '../' + path.basename(outside) + '/secret.py', 'proj/../proj/real.py']), undefined);
  // Control: the same bytes in a regular workspace file do match.
  assert.deepEqual(derivedWriteMatch(root, 'proj/copy.txt', secret, [...linked, 'proj/real.py']), {kind: 'copy', files: ['proj/real.py']});
  // Through the guard, a run that "read" the linked paths gets no refusal.
  const h = harness({root});
  for (const file of linked) h.call('read', {path: file}, () => ({content: [{type: 'text', text: secret}], details: {}}));
  assert.notEqual(h.write('proj/public/copy.txt', secret).decision?.block, true);
});

test('a refusal is a free correction: it neither charges nor resets the failure budget', () => {
  const h = harness();
  writeSources(h);
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures - 1; i++) h.failRead(`proj/missing-${i}.py`);
  assert.equal(h.write('proj/public/totals.py.txt', SOURCES['totals.py']).decision?.blockReason, copyReason('proj/totals.py'));
  assert.equal(h.exhausted(), false, 'the refusal was charged');
  // Not progress either: the next real failure still trips the fuse.
  h.failRead('proj/missing-last.py');
  assert.equal(h.exhausted(), true);
});

test('refusals are bounded per destination and per run, and never charged beyond the bound', () => {
  const h = harness();
  writeSources(h);
  // One refusal per destination: a model that re-types anyway is not refused again.
  assert.equal(h.write('proj/public/report.py.txt', SOURCES['report.py']).decision?.blockReason, copyReason('proj/report.py'));
  assert.notEqual(h.write('proj/public/report.py.txt', SOURCES['report.py']).decision?.block, true);
  assert.equal(h.written('proj/public/report.py.txt'), true);
  // FREE_CORRECTIONS_PER_KIND refusals per run, then derived writes proceed.
  const destinations = ['proj/a/totals.py.txt', 'proj/b/totals.py.txt', 'proj/c/totals.py.txt', 'proj/d/totals.py.txt'];
  const refused = destinations.map(file => h.write(file, SOURCES['totals.py']).decision?.block === true);
  assert.deepEqual(refused, destinations.map((_, i) => i < FREE_CORRECTIONS_PER_KIND - 1));
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
    assert.notEqual(h.write(`proj/e${i}/sources.json`, JSON.stringify(SOURCES)).decision?.block, true);
  }
  assert.equal(h.exhausted(), false);
  // A fresh run gets a fresh allowance.
  const next = harness({root: h.root, runId: 'run-2', guard: h.guard});
  next.read('proj/totals.py');
  assert.equal(next.write('proj/f/totals.py.txt', SOURCES['totals.py']).decision?.blockReason, copyReason('proj/totals.py'));
});

test('nested Tool Search executions and unidentified calls are never refused here', () => {
  const h = harness();
  writeSources(h);
  const context = {agentId:'pixel', runId:'run', sessionId:'owner-session', sessionKey:'owner-key', toolName:'write'};
  for (const toolCallId of ['tool_search_code:parent:write:1', undefined]) {
    const decision = h.guard.beforeToolCall({toolName:'write', toolCallId,
      params:{path:'proj/public/totals.py.txt', content:SOURCES['totals.py']}}, {...context, toolCallId});
    assert.notEqual(decision?.block, true, String(toolCallId));
  }
  // The allowance is untouched.
  assert.equal(h.write('proj/public/totals.py.txt', SOURCES['totals.py']).decision?.blockReason, copyReason('proj/totals.py'));
});

test('the principle is one constant sentence in the workspace guide', () => {
  assert.match(DERIVED_FILE_CONTRACT, /copy or aggregate existing files \(exact or \.txt copies, JSON maps of file contents, concatenations\)/);
  assert.match(DERIVED_FILE_CONTRACT, /one short command that reads the real files, e\.g\. cp, or python3 -c with json\.dump; never retype their content/);
  assert.equal(AGENT_SKILLS.workspace.split(DERIVED_FILE_CONTRACT).length, 2);
  const contracts = [PROMPT, 'In a new workspace project calc, write a Python CLI plus unittest and create public/ with raw .py.txt copies.']
    .map(prompt => promptContractForAgent({agentId:'pixel', contextTokenBudget:65536}, 'pixel', {prompt}).appendSystemContext);
  for (const contract of contracts) assert.equal(contract.split(DERIVED_FILE_CONTRACT).length, 2);
  // Not interpolated with anything request-specific.
  assert.doesNotMatch(DERIVED_FILE_CONTRACT, /\$\{|fleet|proj/);
});
