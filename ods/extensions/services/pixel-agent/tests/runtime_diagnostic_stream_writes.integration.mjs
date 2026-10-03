// Actual pinned SDK gateway and attempt; deterministic provider, no model inference.
// The runtime copy is composed by the native macOS bundle code itself: shared
// repairs, then the buffered stream-progress relocation that runs the model-call
// diagnostic observer inside OpenClaw's tool-call wrappers. The "upstream"
// layout applies the shared repairs only, as Linux/WSL installs do, and keeps
// the observer outermost.
// ODS_DIAGNOSTIC_STREAM_WRITES_RED=1 restores the pinned observer module after
// composition to show the corrupted write.
import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn, spawnSync} from 'node:child_process';
import {cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';

const installed = process.env.OPENCLAW_PACKAGE;
const red = process.env.ODS_DIAGNOSTIC_STREAM_WRITES_RED === '1';
const OBSERVER = 'dist/attempt.model-diagnostic-events-DqqiPQPY.js';
const bundleScript = fileURLToPath(new URL('../../../../installers/macos/lib/pixel-runtime-bundle.py', import.meta.url));
const copies = [];
after(() => { for (const copy of copies) rmSync(copy, {recursive: true, force: true}); });

function compose(layout) {
  const copy = mkdtempSync(join(tmpdir(), `ods-diagnostic-stream-${layout}-`));
  copies.push(copy);
  const pkg = join(copy, 'package');
  cpSync(installed, pkg, {recursive: true});
  const composed = spawnSync('python3', ['-c', `
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('bundle', sys.argv[1])
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)
runtime = Path(sys.argv[2])
repairs = bundle._apply_shared_repairs(runtime, runtime.parent)
if sys.argv[3] == 'macos':
    bundle._patch_stream_progress(runtime, budget_receipt=repairs[-1])
`, bundleScript, pkg, layout], {encoding: 'utf8'});
  assert.equal(composed.status, 0, composed.stderr);
  if (red) writeFileSync(join(pkg, OBSERVER), readFileSync(join(installed, OBSERVER)));
  return pkg;
}

// A write whose Python source has a real line break right after "as f:". The
// pinned transport's streaming JSON repair treats "f:" as a Windows drive
// prefix and keeps each later \n escape as a literal backslash-n.
const CONTENT = 'import json\nx = {"ok": 1}\nwith open("out.json", "w") as f:\n    json.dump(x, f)\nprint("saved")\n';
const RAW = JSON.stringify({path: 'save.py', content: CONTENT});
const CORRUPTED = CONTENT.replace('as f:\n    json.dump(x, f)\n', 'as f:\\n    json.dump(x, f)\\n');
const DELTAS = [RAW.slice(0, 24), RAW.slice(24, 70), RAW.slice(70)];

async function writeThroughGateway(pkg) {
  const root = mkdtempSync(join(tmpdir(), 'ods-diagnostic-stream-run-'));
  const workspace = join(root, 'workspace'); mkdirSync(workspace);
  let rounds = 0, log = '', child;
  const requests = [];
  const upstream = createServer(async (req, res) => {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    requests.push(JSON.parse(Buffer.concat(chunks).toString()).messages);
    const round = rounds++;
    const frame = (delta, finish = null) => 'data: ' + JSON.stringify({id: 'fixture', object: 'chat.completion.chunk', choices: [{index: 0, delta, finish_reason: finish}]}) + '\n\n';
    res.writeHead(200, {'Content-Type': 'text/event-stream'});
    if (round === 0) {
      res.write(frame({role: 'assistant', tool_calls: [{index: 0, id: 'write-0', type: 'function', function: {name: 'write', arguments: DELTAS[0]}}]}));
      for (const part of DELTAS.slice(1)) res.write(frame({tool_calls: [{index: 0, function: {arguments: part}}]}));
      res.end(frame({}, 'tool_calls') + 'data: [DONE]\n\n');
    } else {
      res.end(frame({role: 'assistant', content: 'Saved.'}) + frame({}, 'stop') + 'data: [DONE]\n\n');
    }
  });
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve));
  const probe = createServer(); await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
  const port = probe.address().port; await new Promise(resolve => probe.close(resolve));
  const config = {logging: {file: join(root, 'runtime.log')}, update: {checkOnStart: false},
    gateway: {mode: 'local', bind: 'loopback', port, auth: {mode: 'token', token: 'fixture-only-0123456789abcdef'}, http: {endpoints: {chatCompletions: {enabled: true}}}},
    agents: {defaults: {workspace, skipBootstrap: true, sandbox: {mode: 'off'}, model: {primary: 'fixture/test'}, contextTokens: 131072, heartbeat: {every: '0m'}},
      list: [{id: 'pixel', default: true, workspace}]},
    models: {mode: 'replace', providers: {fixture: {baseUrl: 'http://127.0.0.1:' + upstream.address().port + '/v1', api: 'openai-completions', apiKey: 'fixture-only',
      models: [{id: 'test', name: 'Fixture', contextWindow: 131072, maxTokens: 8192, reasoning: false, input: ['text']}]}}},
    tools: {allow: ['write'], loopDetection: {enabled: false}}};
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
      body: JSON.stringify({model: 'openclaw:pixel', stream: true, user: 'diagnostic-stream-fixture', messages: [{role: 'user', content: 'Save the script.'}]}),
      signal: AbortSignal.timeout(90000)});
    const body = await response.text();
    assert.equal(response.status, 200, body + '\n' + log);
    assert.equal(rounds, 2, log);
    const file = join(workspace, 'save.py');
    assert.ok(existsSync(file), log);
    const replayed = requests[1].find(message => message.role === 'assistant' && message.tool_calls?.length)?.tool_calls[0].function.arguments;
    return {written: readFileSync(file, 'utf8'), replayed: JSON.parse(replayed)};
  } finally {
    if (child && child.exitCode === null) { const closed = once(child, 'close'); process.kill(-child.pid, 'SIGTERM'); await Promise.race([closed, delay(3000)]); if (child.exitCode === null) { process.kill(-child.pid, 'SIGKILL'); await closed; } }
    upstream.closeAllConnections(); await new Promise(resolve => upstream.close(resolve));
    rmSync(root, {recursive: true, force: true});
  }
}

const skip = !installed || process.platform === 'win32';
test('macOS composition writes the model\'s exact tool-call arguments', {skip, timeout: 180000}, async () => {
  assert.equal(JSON.parse(readFileSync(join(installed, 'package.json'))).version, '2026.6.33');
  const {written, replayed} = await writeThroughGateway(compose('macos'));
  console.log(JSON.stringify({layout: 'macos', red, written}));
  if (red) {
    assert.equal(written, CORRUPTED, 'the pinned observer drops the strict re-parse, so the repaired JSON is written');
    return;
  }
  assert.equal(written, CONTENT, 'the file holds real line breaks, byte for byte');
  assert.deepEqual(replayed, JSON.parse(RAW), 'the next request replays the arguments the model generated');
});

test('upstream composition is unchanged by the observer repair', {skip, timeout: 180000}, async () => {
  const {written, replayed} = await writeThroughGateway(compose('upstream'));
  assert.equal(written, CONTENT);
  assert.deepEqual(replayed, JSON.parse(RAW));
});
