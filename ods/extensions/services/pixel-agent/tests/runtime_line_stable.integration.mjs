// Real pinned OpenClaw gateway, deterministic provider, disposable state.
// A Portal chat, then a second chat (the Portal side chat, or a new chat),
// then the first chat again. Upstream, each chat's system prompt carries its
// own session key and id in the Runtime line, so the chats share a llama.cpp
// prefix only up to the key. With the plugin's text transform all three
// requests carry the same system prompt: the one upstream renders, less the
// two fields.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {cpSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {stablePixelRuntimeLine} from '../plugin/runtime-line.mjs';

const pkg = process.env.OPENCLAW_PACKAGE;
const TOKEN = 'fixture-only-0123456789abcdef';
// pixel_ingress keys Portal chats as openai-user ods-<sha256 hex>.
const MAIN = 'ods-' + '3f1c'.repeat(16), SIDE = 'ods-' + 'a95e'.repeat(16);
const text = content => typeof content === 'string' ? content
  : Array.isArray(content) ? content.map(part => part?.text ?? '').join('\n') : '';
const runtimeLine = system => system.split('\n').find(line => line.startsWith('Runtime: '));

async function chats({stable, root, port}) {
  const requests = [];
  let log = '', child;
  const upstream = createServer(async (req, res) => {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString());
    requests.push(body);
    res.writeHead(200, {'Content-Type': 'text/event-stream'});
    res.write('data: ' + JSON.stringify({id: 'fixture', object: 'chat.completion.chunk',
      choices: [{index: 0, delta: {role: 'assistant', content: 'SAVED'}, finish_reason: null}]}) + '\n\n');
    res.end('data: ' + JSON.stringify({id: 'fixture', object: 'chat.completion.chunk',
      choices: [{index: 0, delta: {}, finish_reason: 'stop'}]}) + '\n\ndata: [DONE]\n\n');
  });
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve));
  const plugin = join(root, stable ? 'stable-plugin' : 'upstream-plugin'); mkdirSync(plugin);
  writeFileSync(join(plugin, 'package.json'), JSON.stringify({name: 'runtime-line-fixture', version: '1.0.0', type: 'module', openclaw: {extensions: ['./index.mjs']}}));
  writeFileSync(join(plugin, 'openclaw.plugin.json'), JSON.stringify({id: 'runtime-line-fixture', activation: {onStartup: true}, configSchema: {type: 'object', properties: {}}}));
  // The plugin's own registration function with its real module.
  writeFileSync(join(plugin, 'index.mjs'), stable ? `
    import {registerStableRuntimeLine} from '../pa/plugin/runtime-line.mjs';
    export default {id: 'runtime-line-fixture', register(api) { registerStableRuntimeLine(api); }};
  ` : `export default {id: 'runtime-line-fixture', register() {}};`);
  const state = join(root, stable ? 'stable-state' : 'upstream-state');
  const config = {logging: {file: join(root, stable ? 'stable.log' : 'upstream.log')}, update: {checkOnStart: false},
    gateway: {mode: 'local', bind: 'loopback', port, auth: {mode: 'token', token: TOKEN}, http: {endpoints: {chatCompletions: {enabled: true}}}},
    agents: {defaults: {workspace: join(root, 'workspace'), skipBootstrap: true, sandbox: {mode: 'off'}, model: {primary: 'fixture/test'},
      contextTokens: 65536, heartbeat: {every: '0m'}}, list: [{id: 'pixel', default: true, workspace: join(root, 'workspace')}]},
    models: {mode: 'replace', providers: {fixture: {baseUrl: `http://127.0.0.1:${upstream.address().port}/v1`, api: 'openai-completions', apiKey: 'fixture-only',
      models: [{id: 'test', name: 'Fixture', contextWindow: 65536, maxTokens: 4096, reasoning: false, input: ['text']}]}}},
    tools: {loopDetection: {enabled: false}},
    plugins: {allow: ['runtime-line-fixture'], load: {paths: [plugin]}, entries: {'runtime-line-fixture': {enabled: true}}}};
  const configPath = join(root, stable ? 'stable.json' : 'upstream.json');
  writeFileSync(configPath, JSON.stringify(config));
  const ask = async (user, content) => {
    const response = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {method: 'POST',
      headers: {'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN}`},
      body: JSON.stringify({model: 'openclaw:pixel', stream: true, user, messages: [{role: 'user', content}]}),
      signal: AbortSignal.timeout(60000)});
    const body = await response.text();
    assert.equal(response.status, 200, body + '\n' + log);
  };
  try {
    child = spawn(process.execPath, [join(pkg, 'openclaw.mjs'), 'gateway', 'run'], {cwd: root, detached: true,
      env: {PATH: process.env.PATH, HOME: root, TMPDIR: root, OPENCLAW_STATE_DIR: state, OPENCLAW_CONFIG_PATH: configPath, OPENCLAW_SKIP_CHANNELS: '1'},
      stdio: ['ignore', 'pipe', 'pipe']});
    child.stdout.on('data', x => log += x); child.stderr.on('data', x => log += x);
    let ready = false;
    for (let n = 0; n < 250 && !ready; n++) {
      try { ready = (await fetch(`http://127.0.0.1:${port}/health`, {signal: AbortSignal.timeout(500)})).ok; } catch {}
      if (!ready) { assert.equal(child.exitCode, null, log); await delay(100); }
    }
    assert.ok(ready, log);
    await ask(MAIN, 'Create a static event website in a new workspace directory.');
    await ask(SIDE, 'Remember the private test marker FLEET-UI. Reply with exactly SAVED. Do not use tools.');
    await ask(MAIN, 'Without changing any files, tell me the directory of the website we just made.');
    assert.equal(requests.length, 3, log);
    return {requests, log};
  } finally {
    if (child && child.exitCode === null) {
      const closed = once(child, 'close'); process.kill(-child.pid, 'SIGTERM');
      await Promise.race([closed, delay(3000)]);
      if (child.exitCode === null) { process.kill(-child.pid, 'SIGKILL'); await closed; }
    }
    upstream.closeAllConnections(); await new Promise(resolve => upstream.close(resolve));
  }
}

test('every Pixel chat reaches a real provider with the same system prompt', {skip: !pkg || process.platform === 'win32', timeout: 180000}, async () => {
  const root = mkdtempSync(join(tmpdir(), 'ods-runtime-line-'));
  try {
    mkdirSync(join(root, 'workspace'));
    mkdirSync(join(root, 'node_modules')); symlinkSync(pkg, join(root, 'node_modules', 'openclaw'));
    cpSync(new URL('../plugin/', import.meta.url), join(root, 'pa', 'plugin'), {recursive: true});
    const probe = createServer(); await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
    const port = probe.address().port; await new Promise(resolve => probe.close(resolve));
    const system = body => {
      assert.equal(body.messages[0].role, 'system');
      return text(body.messages[0].content);
    };

    // Upstream: the chats differ inside the session key of the Runtime line.
    const before = await chats({stable: false, root, port});
    const [mainA, side, mainB] = before.requests.map(system);
    const upstreamLine = runtimeLine(mainA);
    assert.match(upstreamLine, new RegExp(`^Runtime: agent=pixel \\| session=agent:pixel:openai-user:${MAIN} \\| sessionId=[0-9a-f-]{36} \\| host=`), upstreamLine);
    assert.equal(mainB, mainA, 'the resident chat itself is stable');
    assert.notEqual(side, mainA);
    let split = 0; while (split < mainA.length && mainA[split] === side[split]) split++;
    assert.ok(mainA.slice(0, split).endsWith('Runtime: agent=pixel | session=agent:pixel:openai-user:ods-'),
      `upstream chats diverge at the session key, not at ${JSON.stringify(mainA.slice(split - 40, split + 10))}`);
    assert.equal(stablePixelRuntimeLine(mainA), stablePixelRuntimeLine(side), 'the key and id are the only per-chat text');
    assert.deepEqual(before.requests[1].tools, before.requests[0].tools);

    // With the transform: one system prompt for all three requests, equal to
    // upstream's less the two fields, and every message delivered unchanged.
    const after = await chats({stable: true, root, port});
    const prompts = after.requests.map(system);
    assert.equal(prompts[1], prompts[0], 'the new chat gets the resident chat\'s system prompt');
    assert.equal(prompts[2], prompts[0], 'returning to the first chat keeps it');
    assert.doesNotMatch(prompts[0], /session=|sessionId=/);
    assert.match(runtimeLine(prompts[0]), /^Runtime: agent=pixel \| host=[^|]+ \| /);
    assert.equal(prompts[0], stablePixelRuntimeLine(mainA), 'nothing else in the system prompt changes');
    assert.deepEqual(after.requests[1].tools, after.requests[0].tools);
    const owner = (body, n) => body.messages.filter(message => message.role === 'user').map(message => text(message.content))[n];
    for (const n of [0, 1, 2]) assert.deepEqual(after.requests[n].messages.slice(1).map(message => message.role),
      before.requests[n].messages.slice(1).map(message => message.role));
    assert.match(owner(after.requests[1], 0), /Remember the private test marker FLEET-UI/);
    assert.match(owner(after.requests[2], 1), /Without changing any files/);
  } finally {
    rmSync(root, {recursive: true, force: true});
  }
});
