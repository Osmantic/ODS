// Actual pinned SDK gateway and attempts; deterministic provider, no model inference.
// ODS_PRECHECK_ROUTE_RED=1 keeps the pinned route to show the summary compaction.
import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, symlinkSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {basename, dirname, join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';

const installed = process.env.OPENCLAW_PACKAGE;
const red = process.env.ODS_PRECHECK_ROUTE_RED === '1';
const MODULE = 'attempt.tool-run-context-yigSIkBW.js';
const hash = (text) => createHash('sha256').update(text).digest('hex');
function reviewed(file, manifestName) {
  const manifest = JSON.parse(readFileSync(new URL(`../host/${manifestName}`, import.meta.url)));
  let source = readFileSync(join(installed, 'dist', file), 'utf8');
  const prior = hash(source) === manifest.patchedSha256 ? manifest.replacements : manifest.previousReplacements?.[hash(source)];
  if (prior) for (const [before, value] of [...prior].reverse()) { assert.equal(source.split(value).length, 2); source = source.replace(value, before); }
  assert.equal(hash(source), manifest.sourceSha256, 'refuse unknown SDK source');
  const pinned = source;
  for (const [before, value] of manifest.replacements) { assert.equal(source.split(before).length, 2); source = source.replace(before, () => value); }
  assert.equal(hash(source), manifest.patchedSha256);
  return {pinned, repaired: source};
}
let pkg, runtimeCopy;
if (installed) {
  runtimeCopy = mkdtempSync(join(tmpdir(), 'ods-precheck-route-runtime-'));
  pkg = join(runtimeCopy, 'package');
  cpSync(installed, pkg, {recursive: true});
  // Hoisted dependencies of the installed package stay resolvable from the copy.
  const hoisted = dirname(installed);
  if (basename(hoisted) === 'node_modules' && !existsSync(join(runtimeCopy, 'node_modules'))) symlinkSync(hoisted, join(runtimeCopy, 'node_modules'));
  const {pinned, repaired} = reviewed(MODULE, 'openclaw-precheck-route.json');
  writeFileSync(join(pkg, 'dist', MODULE), red ? pinned : repaired);
}
after(() => { if (runtimeCopy) rmSync(runtimeCopy, {recursive: true, force: true}); });

// Turn one leaves eight 5.8k-char command outputs (46k chars) in the session.
// The budget (contextTokens - reserveTokens) puts the second owner turn about
// 10k estimated tokens over: the runtime's own trim of old tool output to
// toolResultMaxChars removes about 18k, so no summary is needed.
const ROUNDS = 8, CHARS = 5800, CONTEXT = 65536, RESERVE = 38000;
const SUMMARIZER = 'You are a context summarization assistant';
test('an owner-turn overflow that trimming old tool output fixes is recovered without a summary', {skip: !pkg || process.platform === 'win32', timeout: 180000}, async () => {
  const root = mkdtempSync(join(tmpdir(), 'ods-precheck-route-'));
  const workspace = join(root, 'workspace'); mkdirSync(workspace);
  let rounds = 0, log = '', child;
  const requests = [];
  const upstream = createServer(async (req, res) => {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString());
    const system = body.messages.find((message) => message.role === 'system')?.content;
    const summarizer = JSON.stringify(system ?? '').includes(SUMMARIZER);
    const owner = body.messages.filter((message) => message.role === 'user').map((message) => JSON.stringify(message.content)).at(-1) ?? '';
    requests.push({summarizer, owner, tools: body.messages.filter((message) => message.role === 'tool').map((message) => message.content)});
    let delta;
    if (summarizer) delta = {role: 'assistant', content: '## Goal\nRun the diagnostics.\n\n## Progress\n### Done\n- [x] Diagnostics ran.'};
    else if (owner.includes('Run the eight diagnostics') && rounds < ROUNDS) {
      const round = rounds++;
      const command = `python3 -c "import sys; sys.stdout.write('R${round}:' + 'x' * ${CHARS})"`;
      delta = {role: 'assistant', tool_calls: [{index: 0, id: `exec-${round}`, type: 'function', function: {name: 'exec', arguments: JSON.stringify({command, workdir: workspace})}}]};
    } else delta = {role: 'assistant', content: 'Done.'};
    const finish = delta.tool_calls ? 'tool_calls' : 'stop';
    res.writeHead(200, {'Content-Type': 'text/event-stream'});
    res.write('data: ' + JSON.stringify({id: 'fixture', object: 'chat.completion.chunk', choices: [{index: 0, delta, finish_reason: null}]}) + '\n\n');
    res.end('data: ' + JSON.stringify({id: 'fixture', object: 'chat.completion.chunk', choices: [{index: 0, delta: {}, finish_reason: finish}]}) + '\n\ndata: [DONE]\n\n');
  });
  await new Promise((resolve) => upstream.listen(0, '127.0.0.1', resolve));
  const probe = createServer(); await new Promise((resolve) => probe.listen(0, '127.0.0.1', resolve));
  const port = probe.address().port; await new Promise((resolve) => probe.close(resolve));
  const config = {logging: {file: join(root, 'runtime.log')}, update: {checkOnStart: false},
    gateway: {mode: 'local', bind: 'loopback', port, auth: {mode: 'token', token: 'fixture-only-0123456789abcdef'}, http: {endpoints: {chatCompletions: {enabled: true}}}},
    agents: {defaults: {workspace, skipBootstrap: true, sandbox: {mode: 'off'}, model: {primary: 'fixture/test'}, contextTokens: CONTEXT, heartbeat: {every: '0m'},
      compaction: {reserveTokens: RESERVE, reserveTokensFloor: 0, keepRecentTokens: 4096}},
      list: [{id: 'pixel', default: true, workspace, contextLimits: {toolResultMaxChars: 16000}}]},
    models: {mode: 'replace', providers: {fixture: {baseUrl: 'http://127.0.0.1:' + upstream.address().port + '/v1', api: 'openai-completions', apiKey: 'fixture-only',
      models: [{id: 'test', name: 'Fixture', contextWindow: CONTEXT, maxTokens: 8192, reasoning: false, input: ['text']}]}}},
    tools: {allow: ['exec'], exec: {host: 'gateway', security: 'full', ask: 'off'}, loopDetection: {enabled: false}}};
  writeFileSync(join(root, 'openclaw.json'), JSON.stringify(config));
  const chat = async (content) => {
    const response = await fetch('http://127.0.0.1:' + port + '/v1/chat/completions', {method: 'POST',
      headers: {'Content-Type': 'application/json', Authorization: 'Bearer fixture-only-0123456789abcdef'},
      body: JSON.stringify({model: 'openclaw:pixel', stream: true, user: 'precheck-route-fixture', messages: [{role: 'user', content}]}),
      signal: AbortSignal.timeout(120000)});
    const text = await response.text();
    assert.equal(response.status, 200, text + '\n' + log);
    return text;
  };
  try {
    child = spawn(process.execPath, [join(pkg, 'openclaw.mjs'), 'gateway', 'run'], {cwd: root, detached: true,
      env: {PATH: process.env.PATH, HOME: root, TMPDIR: root, OPENCLAW_STATE_DIR: join(root, 'state'), OPENCLAW_CONFIG_PATH: join(root, 'openclaw.json'), OPENCLAW_SKIP_CHANNELS: '1'},
      stdio: ['ignore', 'pipe', 'pipe']});
    child.stdout.on('data', (x) => log += x); child.stderr.on('data', (x) => log += x);
    let ready = false;
    for (let n = 0; n < 250 && !ready; n++) {
      try { ready = (await fetch('http://127.0.0.1:' + port + '/health', {signal: AbortSignal.timeout(500)})).ok; } catch {}
      if (!ready) { assert.equal(child.exitCode, null, log); await delay(100); }
    }
    assert.ok(ready, log);
    await chat('Run the eight diagnostics.');
    assert.equal(rounds, ROUNDS, log);
    const firstTurn = requests.length;
    await chat('Now tell me which diagnostics ran.');
    const secondTurn = requests.slice(firstTurn);
    const runtimeLog = readFileSync(join(root, 'runtime.log'), 'utf8');
    const precheck = runtimeLog.match(/\[context-overflow-precheck\][^\n]*route=(\w+)[^\n]*estimatedPromptTokens=(\d+)[^\n]*overflowTokens=(\d+)[^\n]*toolResultReducibleChars=(\d+)/);
    const sessions = join(root, 'state/agents/pixel/sessions');
    const transcript = readdirSync(sessions).filter((name) => name.endsWith('.jsonl') && !name.includes('trajectory'))
      .map((name) => readFileSync(join(sessions, name), 'utf8')).join('\n');
    const compactions = transcript.split('\n').filter((line) => line.includes('"type":"compaction"')).length;
    const summaries = secondTurn.filter((request) => request.summarizer).length;
    const answer = secondTurn.filter((request) => !request.summarizer).at(-1);
    console.log(JSON.stringify({red, precheck: precheck?.slice(1), summaries, compactions, sentToolChars: answer?.tools.map((text) => text.length)}));
    assert.ok(precheck, 'the second owner turn overflowed the pre-prompt budget\n' + runtimeLog.slice(-4000));
    assert.ok(answer, 'the owner turn reached the provider');
    if (red) {
      assert.equal(precheck[1], 'compact_then_truncate');
      assert.ok(summaries >= 1, 'the pinned route summarizes the history');
      assert.ok(compactions >= 1);
    } else {
      assert.equal(precheck[1], 'truncate_tool_results_only');
      assert.equal(summaries, 0, 'no summarization request');
      assert.equal(compactions, 0, 'no compaction entry');
      const trimmed = answer.tools.filter((text) => /more characters truncated|truncated/.test(text) && text.length < 200);
      assert.ok(trimmed.length >= 1, 'older command outputs were trimmed');
      assert.ok(answer.tools.some((text) => text.includes(`R${ROUNDS - 1}:` + 'x'.repeat(100))), 'the newest output is still there');
    }
  } finally {
    if (child && child.exitCode === null) { const closed = once(child, 'close'); process.kill(-child.pid, 'SIGTERM'); await Promise.race([closed, delay(3000)]); if (child.exitCode === null) { process.kill(-child.pid, 'SIGKILL'); await closed; } }
    upstream.closeAllConnections(); await new Promise((resolve) => upstream.close(resolve));
    rmSync(root, {recursive: true, force: true});
  }
});
