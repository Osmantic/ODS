// Real pinned OpenClaw gateway, the plugin's actual prompt hooks, deterministic
// provider. Two owner messages in one chat must reach the provider as an
// append-only extension: the same system prompt and tools, and the first
// owner message replayed exactly as the model saw it (with its guidance).
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {cpSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {commonPrefixLength, renderQwen35} from './qwen-chat-render.mjs';
import {OWNER_TURNS, systemBlockLength} from './prompt-prefix-replay.mjs';
import {TURN_GUIDANCE_HEADER} from '../plugin/turn-guidance.mjs';

const pkg = process.env.OPENCLAW_PACKAGE;
const TOKEN = 'fixture-only-0123456789abcdef';

test('two owner messages reach a real provider append-only', {skip: !pkg || process.platform === 'win32', timeout: 120000}, async () => {
  const root = mkdtempSync(join(tmpdir(), 'ods-prompt-prefix-'));
  const workspace = join(root, 'workspace'); mkdirSync(workspace);
  const requests = [];
  let log = '', child;
  const upstream = createServer(async (req, res) => {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString());
    const round = requests.push(body) - 1;
    // Turn 1: one write, then an answer. Turn 2: an answer.
    const delta = round === 0
      ? {role: 'assistant', tool_calls: [{index: 0, id: 'write-0', type: 'function', function: {name: 'write',
        arguments: JSON.stringify({path: 'index.html', content: OWNER_TURNS[0].html})}}]}
      : {role: 'assistant', content: OWNER_TURNS[round === 1 ? 0 : 2].answer};
    res.writeHead(200, {'Content-Type': 'text/event-stream'});
    res.write('data: ' + JSON.stringify({id: 'fixture', object: 'chat.completion.chunk', choices: [{index: 0, delta, finish_reason: null}]}) + '\n\n');
    res.end('data: ' + JSON.stringify({id: 'fixture', object: 'chat.completion.chunk', choices: [{index: 0, delta: {}, finish_reason: round === 0 ? 'tool_calls' : 'stop'}]}) + '\n\ndata: [DONE]\n\n');
  });
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve));
  const probe = createServer(); await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
  const port = probe.address().port; await new Promise(resolve => probe.close(resolve));
  mkdirSync(join(root, 'node_modules')); symlinkSync(pkg, join(root, 'node_modules', 'openclaw'));
  // The plugin's own hook registration block (tests/prompt-prefix-replay.mjs
  // extracts it from plugin/index.js) with its real prompt modules.
  cpSync(new URL('../plugin/', import.meta.url), join(root, 'pa', 'plugin'), {recursive: true});
  cpSync(new URL('./', import.meta.url), join(root, 'pa', 'tests'), {recursive: true});
  const plugin = join(root, 'fixture-plugin'); mkdirSync(plugin);
  writeFileSync(join(plugin, 'package.json'), JSON.stringify({name: 'prefix-fixture', version: '1.0.0', type: 'module', openclaw: {extensions: ['./index.mjs']}}));
  writeFileSync(join(plugin, 'openclaw.plugin.json'), JSON.stringify({id: 'prefix-fixture', activation: {onStartup: true}, configSchema: {type: 'object', properties: {}}}));
  writeFileSync(join(plugin, 'index.mjs'), `
    import {pixelPromptHooks} from '../pa/tests/prompt-prefix-replay.mjs';
    export default {id: 'prefix-fixture', register(api) {
      const hooks = pixelPromptHooks();
      for (const name of ['before_prompt_build', 'before_message_write', 'agent_end']) api.on(name, hooks[name]);
    }};
  `);
  const config = {logging: {file: join(root, 'runtime.log')}, update: {checkOnStart: false},
    gateway: {mode: 'local', bind: 'loopback', port, auth: {mode: 'token', token: TOKEN}, http: {endpoints: {chatCompletions: {enabled: true}}}},
    agents: {defaults: {workspace, skipBootstrap: true, sandbox: {mode: 'off'}, model: {primary: 'fixture/test'}, contextTokens: 65536, heartbeat: {every: '0m'}},
      list: [{id: 'pixel', default: true, workspace}]},
    models: {mode: 'replace', providers: {fixture: {baseUrl: `http://127.0.0.1:${upstream.address().port}/v1`, api: 'openai-completions', apiKey: 'fixture-only',
      models: [{id: 'test', name: 'Fixture', contextWindow: 65536, maxTokens: 4096, reasoning: false, input: ['text']}]}}},
    tools: {loopDetection: {enabled: false}},
    plugins: {allow: ['prefix-fixture'], load: {paths: [plugin]}, entries: {'prefix-fixture': {enabled: true, hooks: {allowConversationAccess: true}}}}};
  writeFileSync(join(root, 'openclaw.json'), JSON.stringify(config));
  const ask = async content => {
    const response = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {method: 'POST',
      headers: {'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN}`},
      body: JSON.stringify({model: 'openclaw:pixel', stream: true, user: 'prefix-fixture', messages: [{role: 'user', content}]}),
      signal: AbortSignal.timeout(60000)});
    const text = await response.text();
    assert.equal(response.status, 200, text + '\n' + log);
  };
  try {
    child = spawn(process.execPath, [join(pkg, 'openclaw.mjs'), 'gateway', 'run'], {cwd: root, detached: true,
      env: {PATH: process.env.PATH, HOME: root, TMPDIR: root, OPENCLAW_STATE_DIR: join(root, 'state'), OPENCLAW_CONFIG_PATH: join(root, 'openclaw.json'), OPENCLAW_SKIP_CHANNELS: '1'},
      stdio: ['ignore', 'pipe', 'pipe']});
    child.stdout.on('data', x => log += x); child.stderr.on('data', x => log += x);
    let ready = false;
    for (let n = 0; n < 250 && !ready; n++) {
      try { ready = (await fetch(`http://127.0.0.1:${port}/health`, {signal: AbortSignal.timeout(500)})).ok; } catch {}
      if (!ready) { assert.equal(child.exitCode, null, log); await delay(100); }
    }
    assert.ok(ready, log);
    await ask(OWNER_TURNS[0].prompt);
    await ask(OWNER_TURNS[2].prompt);
    const trace = JSON.stringify(requests.map(body => body.messages.map(message => [message.role,
      typeof message.content === 'string' ? message.content.slice(0, 80) : message.content])), null, 1) + '\n' + log;
    assert.equal(requests.length, 3, trace);
    const [first, afterTool, second] = requests;
    const system = body => body.messages[0];
    assert.equal(system(first).role, 'system');
    assert.deepEqual(system(afterTool), system(first), 'system prompt is stable within the run');
    assert.deepEqual(system(second), system(first), 'system prompt is byte-identical across owner messages');
    assert.deepEqual(second.tools, first.tools, 'tool definitions are byte-identical across owner messages');
    const owner = afterTool.messages.at(1);
    assert.ok(String(owner.content).includes(TURN_GUIDANCE_HEADER), 'the model saw the turn guidance');
    // Everything the first run sent, and the answer it generated, is replayed unchanged.
    const replayed = second.messages.slice(0, afterTool.messages.length);
    assert.deepEqual(replayed, afterTool.messages, trace);
    assert.equal(second.messages[afterTool.messages.length].role, 'assistant');
    assert.ok(String(JSON.stringify(second.messages[afterTool.messages.length].content)).includes(OWNER_TURNS[0].answer));
    // OpenClaw renders the stored envelope timestamp ("[Fri 2026-09-25 11:01 EDT] ")
    // in front of every owner message, current or earlier, from the stored value.
    const envelope = /^\[[^\]\n]{1,64}\] /;
    assert.ok(String(second.messages.at(-1).content).replace(envelope, '').startsWith(OWNER_TURNS[2].prompt));
    assert.ok(String(owner.content).replace(envelope, '').startsWith(OWNER_TURNS[0].prompt));
    // As a Qwen3.5/3.6 llama.cpp server with preserve_thinking renders them:
    // the second owner turn extends the first run's slot.
    const render = body => renderQwen35({messages: body.messages, tools: body.tools ?? [], preserveThinking: true});
    const slot = render(afterTool) + OWNER_TURNS[0].answer;
    const next = render(second);
    assert.ok(commonPrefixLength(slot, next) >= systemBlockLength(next));
    assert.ok(next.startsWith(slot), `diverged at ${commonPrefixLength(slot, next)} of ${slot.length}`);
  } finally {
    if (child && child.exitCode === null) { const closed = once(child, 'close'); process.kill(-child.pid, 'SIGTERM'); await Promise.race([closed, delay(3000)]); if (child.exitCode === null) { process.kill(-child.pid, 'SIGKILL'); await closed; } }
    upstream.closeAllConnections(); await new Promise(resolve => upstream.close(resolve));
    rmSync(root, {recursive: true, force: true});
  }
});
