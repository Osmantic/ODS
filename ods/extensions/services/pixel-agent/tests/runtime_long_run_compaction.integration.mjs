// Exact pinned runtime, loopback provider, disposable state; no model inference.
// RTX Spark, 2026-10-16: one long Portal run compacted on the loop guard's
// estimate while the provider prompt was 35-46k of 131k tokens, and the later
// overflow compaction sent ~190k tokens of transcript in one summary request,
// which failed the run. ODS_LONG_RUN_COMPACTION_BASELINE=1 loads the previous
// reviewed recipes; the same assertions then fail.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {once} from 'node:events';
import {createHash} from 'node:crypto';
import {registerHooks} from 'node:module';
import {pathToFileURL} from 'node:url';

const pkg = process.env.OPENCLAW_PACKAGE_DIR;
assert.ok(pkg, 'provide the pinned reviewed OpenClaw package');
const baseline = process.env.ODS_LONG_RUN_COMPACTION_BASELINE === '1';
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-long-run-'));
process.env.OPENCLAW_STATE_DIR = path.join(root, 'state');
process.env.OPENCLAW_CONFIG_PATH = path.join(root, 'openclaw.json');
const sha = value => createHash('sha256').update(value).digest('hex');
// The reviewed recipes this change extends; installed runtimes migrate from them.
const PREVIOUS = {
  'compaction-budget': '2e64e5eb0e35f5f363865d4f51bba54a599c9ac745241120b0a826e97613288e',
  'compaction-empty': '12fe33a44b48cfe9c5fe95e3f797371ff1e33d548877a95e5d5d0199088738fd',
  'tool-result-projection': '595818ac5da06acc428175a056052561c1a3adf8ef5e8d5d047670d26e07695a',
};
function apply(source, replacements) {
  for (const [before, after] of replacements) {
    assert.equal(source.split(before).length, 2, 'each reviewed anchor is unique');
    source = source.replace(before, () => after);
  }
  return source;
}
function unapply(source, replacements) {
  for (const [before, after] of [...replacements].reverse()) {
    assert.equal(source.split(after).length, 2, 'each reviewed replacement is unique');
    source = source.replace(after, () => before);
  }
  return source;
}
function reviewed(name, module) {
  const manifest = JSON.parse(fs.readFileSync(new URL(`../host/openclaw-${name}.json`, import.meta.url)));
  const filename = path.join(pkg, 'dist', module);
  let original = fs.readFileSync(filename, 'utf8');
  const digest = sha(original);
  const applied = digest === manifest.patchedSha256 ? manifest.replacements : manifest.previousReplacements?.[digest];
  if (applied) original = unapply(original, applied);
  assert.equal(sha(original), manifest.sourceSha256, 'reject unreviewed native bytes');
  const patched = apply(original, manifest.replacements);
  assert.equal(sha(patched), manifest.patchedSha256);
  const previous = name in PREVIOUS ? manifest.previousReplacements[PREVIOUS[name]] : undefined;
  return {manifest, filename, original, patched, previous: previous && apply(original, previous)};
}
const recipes = Object.fromEntries([
  ['compaction-budget', 'selection-BEwSQKM-.js'],
  ['tool-result-projection', 'tool-result-truncation-CbxVHy2D.js'],
  ['compaction-resume', 'sessions-CZbwb3_c.js'],
  ['compaction-empty', 'proxy-Bsfwfsp-.js'],
  ['compaction-no-work', 'compact-DuWIsaq_.js'],
].map(([name, module]) => [name, reviewed(name, module)]));
const sources = new Map(Object.values(recipes).map(recipe =>
  [pathToFileURL(recipe.filename).href, baseline && recipe.previous ? recipe.previous : recipe.patched]));
const loader = registerHooks({load(url, context, next) {
  return sources.has(url) ? {format: 'module', source: sources.get(url), shortCircuit: true} : next(url, context);
}});
const native = await import(pathToFileURL(path.join(pkg, 'dist/proxy-Bsfwfsp-.js')));
const {t: runEmbeddedAgent} = await import(pathToFileURL(path.join(pkg, 'dist/embedded-agent-CJx-nG3W.js')));
const {compactEmbeddedAgentSessionDirect} = await import(pathToFileURL(path.join(pkg, 'dist/compact-DuWIsaq_.js')));
test.after(async () => {
  // Windows refuses to remove the shared state database while it is open.
  const {n: closeStateDatabases} = await import(pathToFileURL(path.join(pkg, 'dist/openclaw-state-db-BtpXMqJX.js')));
  closeStateDatabases(); loader.deregister(); await fs.promises.rm(root, {recursive: true, force: true});
});

// Spark profile: 131k context, 8k output, ODS reserve/keep, 16k tool results.
const WINDOW = 131072, OUTPUT = 8192, RESERVE = 32768;
const SUMMARY_SYSTEM = 'You are a context summarization assistant.';
const textOf = content => typeof content === 'string' ? content
  : Array.isArray(content) ? content.map(block => typeof block?.text === 'string' ? block.text : '').join('') : '';
const requestChars = body => body.messages.reduce((sum, message) => sum + textOf(message.content).length
  + (message.tool_calls ? JSON.stringify(message.tool_calls).length : 0), 0);
// The openai-completions transport's own input estimate (4 chars/token, 1.25 margin).
const transportTokens = body => Math.ceil(requestChars(body) / 4 * 1.25);
// The fixture server's own prompt count (4 chars/token), reported as usage.
const promptTokens = body => Math.ceil(requestChars(body) / 4);
const isSummary = body => textOf(body.messages[0]?.content).startsWith(SUMMARY_SYSTEM);
const CONTEXT_ERROR = 'the request exceeds the available context size, try increasing it';
const GUARD_OVERFLOW = 'Context overflow: estimated context size exceeds safe threshold during tool loop.';
const transcript = sessionFile => fs.readFileSync(sessionFile, 'utf8').trim().split('\n').map(line => JSON.parse(line));
function provider(t, respond) {
  const bodies = [];
  const server = http.createServer(async (req, res) => {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks)); bodies.push(body);
    const reply = respond(body);
    if (reply.status) {
      res.writeHead(reply.status, {'Content-Type': 'application/json'});
      res.end(JSON.stringify({error: {code: reply.status, message: reply.error, type: 'exceed_context_size_error'}}));
      return;
    }
    res.writeHead(200, {'Content-Type': 'text/event-stream'});
    res.write('data: ' + JSON.stringify({id: 'fixture', object: 'chat.completion.chunk', choices: [{index: 0, delta: {role: 'assistant', ...reply.delta}, finish_reason: null}]}) + '\n\n');
    res.end('data: ' + JSON.stringify({id: 'fixture', object: 'chat.completion.chunk', choices: [{index: 0, delta: {}, finish_reason: reply.delta.tool_calls ? 'tool_calls' : 'stop'}],
      usage: {prompt_tokens: promptTokens(body), completion_tokens: 20, total_tokens: promptTokens(body) + 20}}) + '\n\ndata: [DONE]\n\n');
  });
  server.listen(0, '127.0.0.1');
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  return {bodies, ready: once(server, 'listening').then(() => server.address().port)};
}
function config(area, port) {
  const workspace = path.join(area, 'workspace'), agentDir = path.join(area, 'agent');
  fs.mkdirSync(workspace, {recursive: true}); fs.mkdirSync(agentDir, {recursive: true});
  const value = {agents: {defaults: {workspace, skipBootstrap: true, model: 'fixture/model', contextTokens: WINDOW,
    compaction: {reserveTokens: RESERVE, reserveTokensFloor: 0, keepRecentTokens: 8192}, heartbeat: {every: '0m'}},
  list: [{id: 'pixel', default: true, workspace, agentDir, contextLimits: {toolResultMaxChars: 16000}}]},
  tools: {allow: ['read'], loopDetection: {enabled: false}}, plugins: {enabled: false},
  models: {mode: 'replace', providers: {fixture: {api: 'openai-completions', baseUrl: `http://127.0.0.1:${port}/v1`, apiKey: 'fixture-only',
    models: [{id: 'model', name: 'Fixture', contextWindow: WINDOW, maxTokens: OUTPUT, reasoning: false, input: ['text']}]}}}};
  fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH, JSON.stringify(value));
  return {config: value, workspace, agentDir};
}
// Content-free synthetic source: about 30k characters, like the game's ui.js.
const source = lines => Array.from({length: lines}, (_, i) => `  const cell${i} = grid.cells[${i}] ?? spawn(${i});`).join('\n');

test('the loop guard measures the projected prompt and leaves a small real prompt uncompacted', {timeout: 90000}, async t => {
  const ROUNDS = 12;
  let rounds = 0;
  const upstream = provider(t, body => {
    if (isSummary(body)) return {delta: {content: 'Summary of the file reads.'}};
    if (rounds >= ROUNDS) return {delta: {content: 'Done.'}};
    return {delta: {tool_calls: [{index: 0, id: `read-${rounds}`, type: 'function', function: {name: 'read', arguments: JSON.stringify({path: 'ui.js', offset: 1 + rounds++ % 2})}}]}};
  });
  const area = fs.mkdtempSync(path.join(root, 'guard-')), {config: value, workspace, agentDir} = config(area, await upstream.ready);
  fs.writeFileSync(path.join(workspace, 'ui.js'), source(640));
  const sessionId = '2aaaaaaa-2222-4333-8444-555555555555', sessionFile = path.join(area, sessionId + '.jsonl');
  fs.writeFileSync(sessionFile, JSON.stringify({type: 'session', version: 3, id: sessionId, timestamp: new Date().toISOString(), cwd: workspace}) + '\n');
  const result = await runEmbeddedAgent({agentId: 'pixel', sessionId, sessionFile, sessionKey: 'agent:pixel:long-run-fixture', workspaceDir: workspace, agentDir,
    config: value, provider: 'fixture', model: 'model', prompt: 'Read ui.js twelve times, then say Done.', runId: 'long-run-guard', trigger: 'user', timeoutMs: 60000});
  const summaries = upstream.bodies.filter(isSummary);
  const turns = upstream.bodies.filter(body => !summaries.includes(body));
  const trace = JSON.stringify({error: result.meta?.error, rounds, requests: upstream.bodies.length, summaries: summaries.length,
    estimates: turns.map(transportTokens)});
  t.diagnostic(trace);
  assert.equal(summaries.length, 0, 'FAIL: the loop guard compacted a prompt far below the real limit\n' + trace);
  // Each raw result is ~30k characters; twelve of them are what the old guard
  // counted (at 2 characters per token) against the 131k window.
  assert.equal(turns.at(-1).messages.filter(message => message.role === 'tool').length, ROUNDS, trace);
  assert.ok(fs.readFileSync(path.join(workspace, 'ui.js'), 'utf8').length * ROUNDS * 2 > WINDOW * 4 * 0.9);
  assert.equal(result.meta?.error, undefined, trace);
  assert.equal(result.payloads?.map(payload => payload.text ?? '').join(''), 'Done.', trace);
  // A response keeps up to twice the 64k base aggregate of tool output in view
  // (openclaw-tool-result-projection.json): at most 128k characters plus the
  // newest result and the turn's own text, about 47k transport tokens.
  assert.ok(turns.every(body => transportTokens(body) < 48000), 'the provider prompts stayed small\n' + trace);
  // The base aggregate alone stays near 25k (64k plus the newest result).
  assert.ok(turns.some(body => transportTokens(body) > 30000), 'the wider view was in use\n' + trace);
});

// Laptop, 2026-10-28 and 2026-10-30: a coding response reads part of a file,
// edits it and reads it again, and reads other files along the way. With the
// real read and edit tools, the reduction takes the read the edit replaced and
// the copy read again before fresh reads (openclaw-tool-result-projection.json).
test('a read, edit and check run reduces replaced and repeated reads first, without compacting', {timeout: 90000}, async t => {
  // Whole-file reads of 160 lines of 60 characters: 9,599 characters each.
  const lines = name => Array.from({length: 160}, (_, i) => `  const ${name}${i} = grid.cells[${i}] ?? spawn(${i});`.padEnd(59, ' '));
  const others = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k'];
  const ui = lines('cell');
  const script = [...others.slice(0, 8).map(name => ['read', {path: `${name}.js`}]),
    ['read', {path: 'ui.js', offset: 1, limit: 160}],
    ['edit', {path: 'ui.js', edits: [{oldText: ui[5], newText: ui[5].replace('spawn', 'reuse')}]}],
    ['read', {path: 'ui.js', offset: 1, limit: 160}],
    ...['i', 'j', 'k'].map(name => ['read', {path: `${name}.js`}]),
    ['read', {path: 'i.js'}]];
  let step = 0;
  const upstream = provider(t, body => {
    if (isSummary(body)) return {delta: {content: 'Summary of the reads and the edit.'}};
    if (step >= script.length) return {delta: {content: 'Done.'}};
    const [name, args] = script[step];
    return {delta: {tool_calls: [{index: 0, id: `call-${step++}`, type: 'function', function: {name, arguments: JSON.stringify(args)}}]}};
  });
  const area = fs.mkdtempSync(path.join(root, 'reread-')), {config: value, workspace, agentDir} = config(area, await upstream.ready);
  value.tools.allow = ['read', 'edit'];
  for (const name of others) fs.writeFileSync(path.join(workspace, `${name}.js`), lines(name).join('\n'));
  fs.writeFileSync(path.join(workspace, 'ui.js'), [...ui, ...lines('tail')].join('\n'));
  const sessionId = '5aaaaaaa-2222-4333-8444-555555555555', sessionFile = path.join(area, sessionId + '.jsonl');
  fs.writeFileSync(sessionFile, JSON.stringify({type: 'session', version: 3, id: sessionId, timestamp: new Date().toISOString(), cwd: workspace}) + '\n');
  const result = await runEmbeddedAgent({agentId: 'pixel', sessionId, sessionFile, sessionKey: 'agent:pixel:reread-fixture', workspaceDir: workspace, agentDir,
    config: value, provider: 'fixture', model: 'model', prompt: 'Read the files, fix ui.js and check it, then say Done.', runId: 'long-run-reread', trigger: 'user', timeoutMs: 60000});
  const summaries = upstream.bodies.filter(isSummary);
  const tools = upstream.bodies.at(-1).messages.filter(message => message.role === 'tool').map(message => textOf(message.content));
  const reduced = tools.map(text => /^\[\.\.\. \d+ more characters truncated; rerun with narrower args if needed\]$/.test(text));
  const trace = JSON.stringify({error: result.meta?.error, step, summaries: summaries.length, sizes: tools.map(text => text.length)});
  t.diagnostic(trace);
  assert.equal(summaries.length, 0, trace);
  assert.equal(result.payloads?.map(payload => payload.text ?? '').join(''), 'Done.', trace);
  assert.match(tools[9], /^Successfully replaced 1 block\(s\) in ui\.js\.$/, trace);
  assert.match(tools[10], /\n\n\[160 more lines in file\. Use offset=161 to continue\.\]$/, trace);
  // 134,539 characters pass the 128k view once: down to 64k. The read the edit
  // replaced and the first copy of i.js go first, then the oldest fresh reads.
  assert.deepEqual(reduced, [true, true, true, true, true, true, false, false, true, false, false, true, false, false, false],
    'FAIL: fresh reads were reduced while replaced or repeated reads stayed\n' + trace);
});

// Positive control. The projection caps tool results only; tool-call arguments
// (write and edit payloads) reach the provider whole. A run of large writes
// must still trip the guard (window x 3.6 characters) before the provider
// rejects the prompt, and the run must continue from the compacted session.
// This fixture ignores max_tokens, which the transport already cuts on the
// last turns before the guard fires; a real model then stops on length and
// the threshold compaction resumes it (openclaw-compaction-resume.json).
test('the loop guard still compacts once the projected prompt nears the window, and the run continues', {timeout: 90000}, async t => {
  let reads = 0, writes = 0, compacted = false;
  const upstream = provider(t, body => {
    if (isSummary(body)) { compacted = true; return {delta: {content: 'Summary of the reads and writes.'}}; }
    // llama.cpp rejects a prompt that fills its context.
    if (promptTokens(body) >= WINDOW) return {status: 400, error: CONTEXT_ERROR};
    if (compacted || writes >= 40) return {delta: {content: 'Done.'}};
    // Four reads at the 16k cap, then ~19k-character writes.
    const [name, args] = reads < 4 ? ['read', {path: 'ui.js', offset: 1 + reads++}] : ['write', {path: `part-${writes++}.js`, content: source(400)}];
    return {delta: {tool_calls: [{index: 0, id: `${name}-${reads + writes}`, type: 'function', function: {name, arguments: JSON.stringify(args)}}]}};
  });
  const area = fs.mkdtempSync(path.join(root, 'guard-near-')), {config: value, workspace, agentDir} = config(area, await upstream.ready);
  value.tools.allow = ['read', 'write'];
  fs.writeFileSync(path.join(workspace, 'ui.js'), source(640));
  const sessionId = '4aaaaaaa-2222-4333-8444-555555555555', sessionFile = path.join(area, sessionId + '.jsonl');
  fs.writeFileSync(sessionFile, JSON.stringify({type: 'session', version: 3, id: sessionId, timestamp: new Date().toISOString(), cwd: workspace}) + '\n');
  const events = [];
  const result = await runEmbeddedAgent({agentId: 'pixel', sessionId, sessionFile, sessionKey: 'agent:pixel:guard-near-fixture', workspaceDir: workspace, agentDir,
    config: value, provider: 'fixture', model: 'model', prompt: 'Read ui.js, then write the parts, then say Done.', runId: 'long-run-guard-near', trigger: 'user', timeoutMs: 60000,
    onAgentEvent: event => { if (event.stream === 'compaction') events.push(event.data); }});
  const first = upstream.bodies.findIndex(isSummary);
  const before = upstream.bodies.slice(0, first), after = upstream.bodies.slice(first + 1).filter(body => !isSummary(body));
  const trace = JSON.stringify({error: result.meta?.error, reads, writes, events, prompts: upstream.bodies.map(body => isSummary(body) ? 'summary' : promptTokens(body))});
  t.diagnostic(trace);
  assert.ok(transcript(sessionFile).some(row => row.message?.errorMessage === GUARD_OVERFLOW), 'FAIL: the loop guard never stopped the run\n' + trace);
  assert.ok(upstream.bodies.every(body => promptTokens(body) < WINDOW), 'FAIL: the provider rejected a prompt the guard let through\n' + trace);
  assert.ok(first > 0 && promptTokens(before.at(-1)) > 0.85 * WINDOW, 'FAIL: the guard compacted before the real prompt neared the window\n' + trace);
  assert.deepEqual(events, [{phase: 'start'}, {phase: 'end', willRetry: true, completed: true}], 'one compaction, then a retry\n' + trace);
  for (const body of upstream.bodies.filter(isSummary)) assert.ok(transportTokens(body) + RESERVE <= WINDOW, trace);
  assert.equal(result.meta?.error, undefined, trace);
  assert.equal(result.payloads?.map(payload => payload.text ?? '').join(''), 'Done.', trace);
  assert.ok(after.length > 0 && promptTokens(after.at(-1)) < WINDOW / 4, 'the run continued from the compacted session\n' + trace);
});

// The Spark session: an owner request, a first overflow compaction, then 300
// entries of long tool work (reads, edits with code payloads, reasoning).
function sparkSession(workspace) {
  const now = Date.now(), at = offset => new Date(now - 3600000 + offset).toISOString();
  const rows = [{type: 'session', version: 3, id: '3aaaaaaa-2222-4333-8444-555555555555', timestamp: at(0), cwd: workspace}];
  let parent = null, step = 0;
  const add = message => { const id = `entry-${step}`; rows.push({type: 'message', id, parentId: parent, timestamp: at(++step * 1000), message: {timestamp: now - 3600000 + step * 1000, ...message}}); parent = id; };
  add({role: 'user', content: 'Make the browser game waves arrive faster and keep the score panel readable.'});
  const work = i => {
    const edit = i % 3 === 0;
    add({role: 'assistant', stopReason: 'toolUse', api: 'openai-completions', provider: 'fixture', model: 'model', usage: {input: 40000, output: 200, totalTokens: 40200},
      content: [{type: 'thinking', thinking: `Step ${i}: check the wave timer and the panel layout before the next change. `.repeat(12)},
        {type: 'text', text: `Step ${i} of the wave work.`},
        {type: 'toolCall', id: `call-${i}`, name: edit ? 'edit' : 'read', arguments: edit ? {path: 'game/ui.js', oldText: source(30), newText: source(36)} : {path: 'game/ui.js'}}]});
    add({role: 'toolResult', toolCallId: `call-${i}`, toolName: edit ? 'edit' : 'read', isError: false,
      content: [{type: 'text', text: edit ? 'Successfully replaced text in game/ui.js.' : source(90)}]});
  };
  for (let i = 0; i < 10; i++) work(i);
  const firstKept = rows.at(-4).id;
  rows.push({type: 'compaction', id: 'first-overflow', parentId: parent, timestamp: at(++step * 1000), firstKeptEntryId: firstKept, tokensBefore: 68211,
    summary: '## Goal\nSpeed up the waves and keep the score panel readable.\n\n## Progress\n' + '- [x] Earlier wave timer work.\n'.repeat(120)});
  parent = 'first-overflow';
  for (let i = 10; i < 160; i++) work(i);
  assert.equal(rows.filter((row, index) => index > rows.findIndex(r => r.id === 'first-overflow')).length, 300);
  return rows;
}

for (const trigger of ['overflow', 'manual']) test(`${trigger} compaction of a 300-entry tail sends one bounded summary request and completes`, {timeout: 90000}, async t => {
  const upstream = provider(t, body => {
    // llama.cpp rejects a prompt that does not fit its context.
    if (transportTokens(body) >= WINDOW) return {status: 400, error: 'the request exceeds the available context size, try increasing it'};
    return {delta: {content: '## Goal\nSpeed up the waves.\n\n## Progress\n- [x] Wave timer and panel edits in game/ui.js.'}};
  });
  const area = fs.mkdtempSync(path.join(root, `summary-${trigger}-`)), {config: value, workspace, agentDir} = config(area, await upstream.ready);
  const rows = sparkSession(workspace);
  const sessionId = rows[0].id, sessionFile = path.join(area, sessionId + '.jsonl');
  fs.writeFileSync(sessionFile, rows.map(row => JSON.stringify(row)).join('\n') + '\n');
  const tail = rows.slice(rows.findIndex(row => row.id === 'first-overflow') + 1).map(row => JSON.stringify(row)).join('\n').length;
  assert.ok(tail / 4 > 1.4 * WINDOW, `the tail alone is far larger than the window (${tail} JSON characters)`);
  const outcome = await compactEmbeddedAgentSessionDirect({agentId: 'pixel', sessionId, sessionFile, sessionKey: 'agent:pixel:spark-fixture', workspaceDir: workspace, agentDir,
    config: value, provider: 'fixture', model: 'model', trigger});
  const trace = JSON.stringify({outcome: {ok: outcome.ok, compacted: outcome.compacted, reason: outcome.reason},
    requests: upstream.bodies.map(body => ({estimate: transportTokens(body), max: body.max_tokens ?? body.max_completion_tokens}))});
  t.diagnostic(trace);
  for (const body of upstream.bodies) {
    assert.ok(transportTokens(body) + OUTPUT < WINDOW, 'FAIL: the summary request does not fit the model with its output\n' + trace);
    assert.ok(transportTokens(body) + RESERVE <= WINDOW, 'the input is held to an ordinary turn at the compaction threshold\n' + trace);
    assert.equal(body.max_tokens ?? body.max_completion_tokens, OUTPUT, 'the summary keeps its full output budget\n' + trace);
  }
  assert.ok(upstream.bodies.length >= 1 && upstream.bodies.length <= 2, 'one compaction, at most two summary requests\n' + trace);
  assert.equal(outcome.ok, true, trace); assert.equal(outcome.compacted, true, trace);
  const after = fs.readFileSync(sessionFile, 'utf8').trim().split('\n').map(line => JSON.parse(line));
  assert.deepEqual(after.filter(row => row.type === 'message'), rows.filter(row => row.type === 'message'), 'the transcript is unchanged');
  assert.equal(after.filter(row => row.type === 'compaction').length, 2, 'a new checkpoint was saved');
  const sent = upstream.bodies.map(body => textOf(body.messages.at(-1).content)).join('\n');
  const steps = [...sent.matchAll(/Step (\d+): check the wave timer/g)].map(match => Number(match[1]));
  assert.ok(steps.length > 0 && Math.max(...steps) >= 150 && Math.min(...steps) > 20,
    `the newest work is summarized whole and the oldest reduced first: ${steps}`);
  assert.match(sent, /Step 11 of the wave work\./, 'replies of reduced steps stay');
  assert.match(sent, /edit\(path="game\/ui\.js", oldText="\[\d+ characters omitted to fit the summary request\]"/, 'a reduced edit keeps its tool and path');
});

// The Spark's failing path: an overflow inside the run starts the session's own
// overflow auto-compaction, which summarizes the whole stored tail since the
// last checkpoint and then retries the turn. On the Spark it ended
// "auto-compaction incomplete ... willRetry=false". Here two provider
// overflows in one owner turn each compact, and the run continues.
test('overflows inside one run over a 300-entry tail auto-compact within the window and the run answers', {timeout: 90000}, async t => {
  let turn = 0;
  const upstream = provider(t, body => {
    // llama.cpp rejects a prompt that does not fit its context.
    if (transportTokens(body) >= WINDOW) return {status: 400, error: CONTEXT_ERROR};
    if (isSummary(body)) return {delta: {content: '## Goal\nSpeed up the waves.\n\n## Progress\n- [x] Wave timer and panel edits in game/ui.js.'}};
    // Overflow, read the file, overflow again, then answer.
    const step = turn++;
    if (step === 0 || step === 2) return {status: 400, error: CONTEXT_ERROR};
    if (step === 1) return {delta: {tool_calls: [{index: 0, id: 'read-after-compaction', type: 'function', function: {name: 'read', arguments: JSON.stringify({path: 'game/ui.js'})}}]}};
    return {delta: {content: 'Done.'}};
  });
  const area = fs.mkdtempSync(path.join(root, 'spark-run-')), {config: value, workspace, agentDir} = config(area, await upstream.ready);
  fs.mkdirSync(path.join(workspace, 'game')); fs.writeFileSync(path.join(workspace, 'game/ui.js'), source(640));
  const rows = sparkSession(workspace);
  const sessionId = rows[0].id, sessionFile = path.join(area, sessionId + '.jsonl');
  fs.writeFileSync(sessionFile, rows.map(row => JSON.stringify(row)).join('\n') + '\n');
  const events = [];
  const result = await runEmbeddedAgent({agentId: 'pixel', sessionId, sessionFile, sessionKey: 'agent:pixel:spark-run-fixture', workspaceDir: workspace, agentDir,
    config: value, provider: 'fixture', model: 'model', prompt: 'Keep going with the wave work.', runId: 'long-run-spark', trigger: 'user', timeoutMs: 60000,
    onAgentEvent: event => { if (event.stream === 'compaction') events.push(event.data); }});
  const summaries = upstream.bodies.filter(isSummary);
  const trace = JSON.stringify({error: result.meta?.error, turn, events, requests: upstream.bodies.map(body =>
    ({summary: isSummary(body), estimate: transportTokens(body), max: body.max_tokens ?? body.max_completion_tokens}))});
  t.diagnostic(trace);
  for (const body of summaries) {
    assert.ok(transportTokens(body) + OUTPUT < WINDOW, 'FAIL: the summary request does not fit the model with its output\n' + trace);
    assert.ok(transportTokens(body) + RESERVE <= WINDOW, trace);
    assert.equal(body.max_tokens ?? body.max_completion_tokens, OUTPUT, 'the summary keeps its full output budget\n' + trace);
  }
  const compaction = [{phase: 'start'}, {phase: 'end', willRetry: true, completed: true}];
  assert.deepEqual(events, [...compaction, ...compaction], 'FAIL: each overflow compacts and retries\n' + trace);
  assert.equal(turn, 4, 'the run continued after each compaction\n' + trace);
  assert.equal(result.meta?.error, undefined, trace);
  assert.equal(result.payloads?.map(payload => payload.text ?? '').join(''), 'Done.', trace);
  assert.equal(transcript(sessionFile).filter(row => row.type === 'compaction').length, 3, 'two new checkpoints were saved');
});

const message = (role, content, extra = {}) => ({role, content, timestamp: 1, ...extra});
async function summaryRequest(messages, {previousSummary, contextWindow = WINDOW, maxTokens = OUTPUT, reserve = RESERVE} = {}) {
  const prompts = [];
  const result = await native.k(messages, {id: 'model', api: 'openai-completions', provider: 'fixture', contextWindow, maxTokens}, reserve,
    undefined, undefined, undefined, undefined, previousSummary, undefined,
    async (_model, context) => ({result: async () => { prompts.push({system: context.systemPrompt, text: context.messages[0].content[0].text}); return {stopReason: 'stop', content: [{type: 'text', text: 'Summary.'}]}; }}));
  return {result, prompts};
}
async function summarize(messages, options) {
  const {result, prompts} = await summaryRequest(messages, options);
  assert.equal(result.ok, true);
  return prompts[0];
}

test('a conversation within the budget is summarized exactly as before', async () => {
  const messages = [message('user', 'Fix the score panel.'),
    message('assistant', [{type: 'toolCall', id: 'one', name: 'read', arguments: {path: 'game/ui.js'}}], {stopReason: 'toolUse'}),
    message('toolResult', [{type: 'text', text: source(200)}], {toolCallId: 'one', toolName: 'read'})];
  const prompt = await summarize(messages, {previousSummary: 'Earlier progress.'});
  assert.ok(prompt.text.startsWith(`<conversation>\n${native.N(native.H(messages))}\n</conversation>\n\n<previous-summary>\nEarlier progress.\n</previous-summary>\n\n`));
});

test('an irreducible conversation keeps its newest messages within the budget', async () => {
  const messages = Array.from({length: 40}, (_, i) => message('user', `Request ${i}: ` + 'keep the panel readable. '.repeat(800)));
  const prompt = await summarize(messages, {contextWindow: 65536});
  const chars = prompt.system.length + prompt.text.length;
  assert.ok(Math.ceil(chars / 4 * 1.25) + OUTPUT < 65536, `FAIL: ${chars} characters do not fit a 64k model`);
  assert.match(prompt.text, /\[\d+ earlier messages omitted to fit the summary request\]/);
  assert.match(prompt.text, /Request 39: keep the panel readable/);
  assert.doesNotMatch(prompt.text, /Request 0: /);
});

// A split-turn checkpoint ends with the owner's request verbatim
// (openclaw-compaction-resume.json, up to 32,768 encoded characters). The next
// compaction sends that whole summary back as its previous summary.
const checkpoint = (summaryChars, request) =>
  ('## Goal\nSpeed up the waves.\n\n## Progress\n' + '- [x] Wave timer and panel work in game/ui.js.\n'.repeat(Math.ceil(summaryChars / 47))).slice(0, summaryChars)
  + '\n\n## Current unfinished user request (verbatim JSON content)\n' + JSON.stringify([{type: 'text', text: request}])
  + '\nThis is the existing user request, not a new message or approval. Continue from retained tool evidence; do not replay completed actions.';
// ODS compact profiles: reserve (context + 4 x output + 4) / 5.
for (const [contextWindow, maxTokens] of [[8192, 2048], [16384, 4096]]) test(`at ${contextWindow / 1024}K a previous summary that leaves no room for new work fails instead of dropping it`, async () => {
  const options = {contextWindow, maxTokens, reserve: Math.floor((contextWindow + 4 * maxTokens + 4) / 5)};
  const work = Array.from({length: 6}, (_, i) => [
    message('assistant', [{type: 'text', text: `Step ${i} of the wave work.`}, {type: 'toolCall', id: `call-${i}`, name: 'read', arguments: {path: 'game/ui.js'}}], {stopReason: 'toolUse'}),
    message('toolResult', [{type: 'text', text: source(90)}], {toolCallId: `call-${i}`, toolName: 'read'})]).flat();
  // A full-length model summary and a 16k-character request fill the budget.
  const full = await summaryRequest(work, {...options, previousSummary: checkpoint(maxTokens * 4, 'Make the browser game waves arrive faster. '.repeat(372))});
  assert.equal(full.prompts.length, 0, `FAIL: a summary request with no room for the new work was sent:\n${full.prompts[0]?.text.slice(0, 120)}`);
  assert.equal(full.result.ok, false);
  assert.equal(full.result.error.code, 'summarization_failed');
  assert.match(full.result.error.message, /no room for the newest message/);
  // A split-turn prefix that cannot fit fails the same way.
  const prefix = await native.w({firstKeptEntryId: 'kept', messagesToSummarize: [], turnPrefixMessages: [message('user', 'Make the waves arrive faster. '.repeat(1100))],
    isSplitTurn: true, tokensBefore: 1, settings: {reserveTokens: options.reserve}}, {id: 'model', api: 'openai-completions', provider: 'fixture', contextWindow, maxTokens},
    undefined, undefined, undefined, undefined, undefined, () => assert.fail('FAIL: a prefix request with no room was sent'));
  assert.equal(prefix.ok, false);
  assert.match(prefix.error.message, /^Turn prefix summarization failed: .*no room for the newest message/);
  // With a short request the newest work is summarized within the budget.
  const prompt = await summarize(work, {...options, previousSummary: checkpoint(maxTokens * 4, 'Make the waves arrive faster.')});
  assert.ok(Math.ceil((prompt.system.length + prompt.text.length) / 4 * 1.25) + options.reserve <= contextWindow, 'the request fits the model with its reserve');
  assert.match(prompt.text, /Step 5 of the wave work\./);
});

test('recipes migrate installed runtimes from the previous reviewed bytes', () => {
  for (const name of Object.keys(PREVIOUS)) {
    const {manifest, original, previous} = recipes[name];
    assert.ok(previous, `${name} accepts the previous reviewed hash`);
    assert.equal(sha(previous), PREVIOUS[name]);
    // openclaw_tool_recovery.py: reconstruct the original, then apply the new recipe.
    assert.equal(sha(apply(unapply(previous, manifest.previousReplacements[PREVIOUS[name]]), manifest.replacements)), manifest.patchedSha256);
    for (const [digest, replacements] of Object.entries(manifest.previousReplacements)) {
      assert.equal(sha(apply(original, replacements)), digest, `${name} predecessor ${digest} is exact`);
    }
  }
});
