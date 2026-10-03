// Actual pinned SDK gateway and attempt; deterministic provider, no model inference.
// ODS_TOOL_RESULT_PROJECTION_RED=1 runs the pinned projection to show the starvation.
import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';

const installed = process.env.OPENCLAW_PACKAGE;
const red = process.env.ODS_TOOL_RESULT_PROJECTION_RED === '1';
const hash = text => createHash('sha256').update(text).digest('hex');
function reviewed(file, manifestName) {
  const manifest = JSON.parse(readFileSync(new URL(`../host/${manifestName}`, import.meta.url)));
  let source = readFileSync(join(installed, 'dist', file), 'utf8');
  const prior = hash(source) === manifest.patchedSha256 ? manifest.replacements : manifest.previousReplacements?.[hash(source)];
  if (prior) for (const [before, value] of [...prior].reverse()) { assert.equal(source.split(value).length, 2); source = source.replace(value, before); }
  assert.equal(hash(source), manifest.sourceSha256, 'refuse unknown SDK source');
  for (const [before, value] of manifest.replacements) { assert.equal(source.split(before).length, 2); source = source.replace(before, () => value); }
  assert.equal(hash(source), manifest.patchedSha256);
  return source;
}
let pkg, runtimeCopy;
if (installed) {
  runtimeCopy = mkdtempSync(join(tmpdir(), 'ods-tool-result-runtime-'));
  pkg = join(runtimeCopy, 'package');
  cpSync(installed, pkg, {recursive: true});
  writeFileSync(join(pkg, 'dist/selection-BEwSQKM-.js'), reviewed('selection-BEwSQKM-.js', 'openclaw-compaction-budget.json'));
  const truncation = join(pkg, 'dist/tool-result-truncation-CbxVHy2D.js');
  const manifest = JSON.parse(readFileSync(new URL('../host/openclaw-tool-result-projection.json', import.meta.url)));
  if (red) assert.equal(hash(readFileSync(truncation)), manifest.sourceSha256, 'red run needs the pinned projection');
  else writeFileSync(truncation, reviewed('tool-result-truncation-CbxVHy2D.js', 'openclaw-tool-result-projection.json'));
}
after(() => { if (runtimeCopy) rmSync(runtimeCopy, {recursive: true, force: true}); });

// ODS's 16k per-result cap makes the live aggregate 64k chars. Eight 12k
// outputs exceed it at the sixth result.
const ROUNDS = 8, CHARS = 12000;
test('every tool result reaches the next real provider request', {skip: !pkg || process.platform === 'win32', timeout: 120000}, async () => {
  assert.equal(JSON.parse(readFileSync(join(pkg, 'package.json'))).version, '2026.6.33');
  const root = mkdtempSync(join(tmpdir(), 'ods-tool-result-delivery-'));
  const workspace = join(root, 'workspace'); mkdirSync(workspace);
  let rounds = 0, log = '', child;
  const requests = [];
  const upstream = createServer(async (req, res) => {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    requests.push(JSON.parse(Buffer.concat(chunks).toString()).messages.filter(message => message.role === 'tool').map(message => message.content));
    const round = rounds++;
    const command = `python3 -c "import sys; sys.stdout.write('R${round}:' + 'x' * ${CHARS})"`;
    const delta = round < ROUNDS
      ? {role: 'assistant', tool_calls: [{index: 0, id: `exec-${round}`, type: 'function', function: {name: 'exec', arguments: JSON.stringify({command, workdir: workspace})}}]}
      : {role: 'assistant', content: 'Done.'};
    res.writeHead(200, {'Content-Type': 'text/event-stream'});
    res.write('data: ' + JSON.stringify({id: 'fixture', object: 'chat.completion.chunk', choices: [{index: 0, delta, finish_reason: null}]}) + '\n\n');
    res.end('data: ' + JSON.stringify({id: 'fixture', object: 'chat.completion.chunk', choices: [{index: 0, delta: {}, finish_reason: round < ROUNDS ? 'tool_calls' : 'stop'}]}) + '\n\ndata: [DONE]\n\n');
  });
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve));
  const probe = createServer(); await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
  const port = probe.address().port; await new Promise(resolve => probe.close(resolve));
  const config = {logging: {file: join(root, 'runtime.log')}, update: {checkOnStart: false},
    gateway: {mode: 'local', bind: 'loopback', port, auth: {mode: 'token', token: 'fixture-only-0123456789abcdef'}, http: {endpoints: {chatCompletions: {enabled: true}}}},
    agents: {defaults: {workspace, skipBootstrap: true, sandbox: {mode: 'off'}, model: {primary: 'fixture/test'}, contextTokens: 131072, heartbeat: {every: '0m'}},
      list: [{id: 'pixel', default: true, workspace, contextLimits: {toolResultMaxChars: 16000}}]},
    models: {mode: 'replace', providers: {fixture: {baseUrl: 'http://127.0.0.1:' + upstream.address().port + '/v1', api: 'openai-completions', apiKey: 'fixture-only',
      models: [{id: 'test', name: 'Fixture', contextWindow: 131072, maxTokens: 8192, reasoning: false, input: ['text']}]}}},
    tools: {allow: ['exec'], exec: {host: 'gateway', security: 'full', ask: 'off'}, loopDetection: {enabled: false}}};
  writeFileSync(join(root, 'openclaw.json'), JSON.stringify(config));
  try {
    child = spawn(process.execPath, [join(pkg, 'openclaw.mjs'), 'gateway', 'run'], {cwd: root, detached: true,
      env: {PATH: process.env.PATH, HOME: root, TMPDIR: root, OPENCLAW_STATE_DIR: join(root, 'state'), OPENCLAW_CONFIG_PATH: join(root, 'openclaw.json'), OPENCLAW_SKIP_CHANNELS: '1'},
      stdio: ['ignore', 'pipe', 'pipe']});
    child.stdout.on('data', x => log += x); child.stderr.on('data', x => log += x);
    let ready = false;
    for (let n = 0; n < 250 && !ready; n++) {
      try { ready = (await fetch('http://127.0.0.1:' + port + '/health', {signal: AbortSignal.timeout(500)})).ok; } catch {}
      if (!ready) { assert.equal(child.exitCode, null, log); await delay(100); }
    }
    assert.ok(ready, log);
    const response = await fetch('http://127.0.0.1:' + port + '/v1/chat/completions', {method: 'POST',
      headers: {'Content-Type': 'application/json', Authorization: 'Bearer fixture-only-0123456789abcdef'},
      body: JSON.stringify({model: 'openclaw:pixel', stream: true, user: 'tool-result-fixture', messages: [{role: 'user', content: 'Run the eight diagnostics.'}]}),
      signal: AbortSignal.timeout(90000)});
    const body = await response.text();
    assert.equal(response.status, 200, body + '\n' + log);
    assert.equal(rounds, ROUNDS + 1, log);
    const delivered = requests.slice(1).map(tools => tools.at(-1));
    console.log(JSON.stringify({red, delivered: delivered.map(text => `${text.slice(0, 12)}... ${text.length}`)}));
    const whole = delivered.map((text, round) => text.includes(`R${round}:` + 'x'.repeat(CHARS)));
    if (red) {
      assert.ok(whole.slice(0, 5).every(Boolean), 'within the aggregate budget the pinned projection delivers results');
      assert.ok(whole.includes(false), 'the pinned projection starves later results');
    } else {
      assert.deepEqual(whole, Array(ROUNDS).fill(true), 'each result reaches the provider whole on its next request');
      const last = requests.at(-1);
      assert.ok(last.every(text => text.length > 0 && text !== '(see attached image)'), 'no result is sent empty');
      assert.ok(last.some(text => /more characters truncated; rerun with narrower args if needed\]/.test(text)), 'older results carry a notice');
    }
  } finally {
    if (child && child.exitCode === null) { const closed = once(child, 'close'); process.kill(-child.pid, 'SIGTERM'); await Promise.race([closed, delay(3000)]); if (child.exitCode === null) { process.kill(-child.pid, 'SIGKILL'); await closed; } }
    upstream.closeAllConnections(); await new Promise(resolve => upstream.close(resolve));
    rmSync(root, {recursive: true, force: true});
  }
});
