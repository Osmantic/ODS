// The agent's exec tool runs node inside the real pinned OpenClaw Docker sandbox.
// Deterministic provider, no model inference. Requires OPENCLAW_PACKAGE (the
// installed openclaw@2026.6.33 package) and OPENCLAW_TEST_SANDBOX_IMAGE: an image
// built from ods/vendor/pixel/deploy/sandbox with PIXEL_SANDBOX_UID set to this
// user's uid. CI: .github/workflows/pixel-sandbox-image.yml on amd64 and arm64.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn, spawnSync} from 'node:child_process';
import {mkdtempSync, mkdirSync, writeFileSync, symlinkSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';

const pkg = process.env.OPENCLAW_PACKAGE;
const image = process.env.OPENCLAW_TEST_SANDBOX_IMAGE;
const manifest = JSON.parse(readFileSync(new URL('../../../../vendor/pixel/RELEASE-MANIFEST.json', import.meta.url)));
const nodeVersion = manifest.sandboxNodeRuntime.version;

// One exec per provider round, as a model checking its own game code would.
const STEPS = [
  {command: `node -p 'process.version+" "+process.cwd()'`, exitCode: 0, output: `v${nodeVersion} /workspace`},
  {command: 'node --check game.js', exitCode: 0},
  {command: 'node --check broken.js', exitCode: 1, output: 'SyntaxError'},
  {command: 'node --test logic.test.mjs', exitCode: 0, output: 'pass 1'},
  {command: 'node --test failing.test.mjs', exitCode: 1, output: 'fail 1'},
  // No package manager reaches the agent; its exit status is the shell's.
  {command: 'npm --version', output: 'not found'},
];

test('agent exec runs the pinned node offline in the real OpenClaw Docker sandbox', {skip: !pkg || !image, timeout: 120000}, async () => {
  assert.equal(JSON.parse(readFileSync(join(pkg, 'package.json'))).version, '2026.6.33');
  const root = mkdtempSync(join(tmpdir(), 'ods-sandbox-node-'));
  const workspace = join(root, 'workspace');
  mkdirSync(workspace);
  writeFileSync(join(workspace, 'game.js'), 'export function step(state) { return {...state, frame: state.frame + 1}; }\n');
  writeFileSync(join(workspace, 'broken.js'), 'function draw( {\n  return 1;\n}\n');
  writeFileSync(join(workspace, 'logic.test.mjs'), "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
    + "import {step} from './game.js';\ntest('advances one frame', () => assert.equal(step({frame: 1}).frame, 2));\n");
  writeFileSync(join(workspace, 'failing.test.mjs'), "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
    + "test('wrong frame', () => assert.equal(1 + 1, 3));\n");
  writeFileSync(join(workspace, 'package.json'), '{"type":"module"}\n');
  mkdirSync(join(workspace, '.openclaw', 'sandbox-skills'), {recursive: true});
  const prefix = 'ods-sandbox-node-' + root.split('-').at(-1).toLowerCase() + '-';
  let rounds = 0, log = '', child, toolResults = [];
  const upstream = createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    toolResults = JSON.parse(Buffer.concat(chunks).toString()).messages.filter(message => message.role === 'tool');
    const round = rounds++;
    const call = round < STEPS.length;
    const delta = call
      ? {role: 'assistant', tool_calls: [{index: 0, id: 'exec-' + round, type: 'function',
        function: {name: 'exec', arguments: JSON.stringify({command: STEPS[round].command, workdir: '/workspace'})}}]}
      : {role: 'assistant', content: 'Checked the game code.'};
    res.writeHead(200, {'Content-Type': 'text/event-stream'});
    res.write('data: ' + JSON.stringify({id: 'fixture', object: 'chat.completion.chunk', choices: [{index: 0, delta, finish_reason: null}]}) + '\n\n');
    res.end('data: ' + JSON.stringify({id: 'fixture', object: 'chat.completion.chunk', choices: [{index: 0, delta: {}, finish_reason: call ? 'tool_calls' : 'stop'}]}) + '\n\ndata: [DONE]\n\n');
  });
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve));
  const probe = createServer();
  await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
  const port = probe.address().port;
  await new Promise(resolve => probe.close(resolve));
  mkdirSync(join(root, 'node_modules'));
  symlinkSync(pkg, join(root, 'node_modules', 'openclaw'));
  // Records what the SDK reports for each exec; no ODS policy is applied here.
  const plugin = join(root, 'plugin');
  mkdirSync(plugin);
  writeFileSync(join(plugin, 'package.json'), JSON.stringify({name: 'sandbox-node-fixture', version: '1.0.0', type: 'module', openclaw: {extensions: ['./index.mjs']}}));
  writeFileSync(join(plugin, 'openclaw.plugin.json'), JSON.stringify({id: 'sandbox-node-fixture', activation: {onStartup: true}, configSchema: {type: 'object', properties: {}}}));
  writeFileSync(join(plugin, 'index.mjs'), `
    import {appendFileSync} from 'node:fs';
    export default {id:'sandbox-node-fixture',register(api){
      api.on('after_tool_call',(event)=>{appendFileSync(${JSON.stringify(join(root, 'hooks.jsonl'))},JSON.stringify({tool:event.toolName,status:event.result?.details?.status,exitCode:event.result?.details?.exitCode})+'\\n');});
    }};
  `);
  // agents.defaults.sandbox.docker as ods/vendor/pixel/scripts/render-config.mjs
  // writes it and ODS normalizes it (no nproc ulimit, no exec-control bind here).
  const sandbox = {mode: 'all', scope: 'session', workspaceAccess: 'rw', docker: {
    image, containerPrefix: prefix, workdir: '/workspace', network: 'none', user: 'sandbox', readOnlyRoot: true,
    tmpfs: ['/tmp:rw,nosuid,nodev,size=256m', '/var/tmp:rw,nosuid,nodev,size=128m', '/run:rw,nosuid,nodev,size=64m'],
    capDrop: ['ALL'], env: {LANG: 'C.UTF-8'}, pidsLimit: 1024, memory: '8g', memorySwap: '8g',
    ulimits: {nofile: {soft: 4096, hard: 4096}}}};
  const config = {logging: {file: join(root, 'runtime.log')}, update: {checkOnStart: false},
    gateway: {mode: 'local', bind: 'loopback', port, auth: {mode: 'token', token: 'fixture-only-0123456789abcdef'}, http: {endpoints: {chatCompletions: {enabled: true}}}},
    agents: {defaults: {workspace, skipBootstrap: true, sandbox, model: {primary: 'fixture/test'}, contextTokens: 32768, heartbeat: {every: '0m'}}, list: [{id: 'pixel', default: true, workspace}]},
    models: {mode: 'replace', providers: {fixture: {baseUrl: 'http://127.0.0.1:' + upstream.address().port + '/v1', api: 'openai-completions', apiKey: 'fixture-only',
      models: [{id: 'test', name: 'Fixture', contextWindow: 32768, maxTokens: 4096, reasoning: false, input: ['text']}]}}},
    tools: {allow: ['exec'], exec: {host: 'sandbox', security: 'full', ask: 'off'}},
    plugins: {allow: ['sandbox-node-fixture'], load: {paths: [plugin]}, entries: {'sandbox-node-fixture': {enabled: true, hooks: {allowConversationAccess: true}}}}};
  writeFileSync(join(root, 'openclaw.json'), JSON.stringify(config));
  try {
    child = spawn(process.execPath, [join(pkg, 'openclaw.mjs'), 'gateway', 'run'], {cwd: root, detached: true,
      env: {PATH: process.env.PATH, HOME: root, TMPDIR: root, OPENCLAW_STATE_DIR: join(root, 'state'), OPENCLAW_CONFIG_PATH: join(root, 'openclaw.json'), OPENCLAW_SKIP_CHANNELS: '1'},
      stdio: ['ignore', 'pipe', 'pipe']});
    child.stdout.on('data', chunk => log += chunk);
    child.stderr.on('data', chunk => log += chunk);
    let ready = false;
    for (let attempt = 0; attempt < 250 && !ready; attempt++) {
      try { ready = (await fetch('http://127.0.0.1:' + port + '/health', {signal: AbortSignal.timeout(500)})).ok; } catch {}
      if (!ready) { assert.equal(child.exitCode, null, log); await delay(100); }
    }
    assert.ok(ready, log);
    const response = await fetch('http://127.0.0.1:' + port + '/v1/chat/completions', {method: 'POST',
      headers: {'Content-Type': 'application/json', Authorization: 'Bearer fixture-only-0123456789abcdef'},
      body: JSON.stringify({model: 'openclaw:pixel', stream: true, user: 'sandbox-node-fixture',
        messages: [{role: 'user', content: 'Syntax-check and test the game code in the sandbox.'}]}),
      signal: AbortSignal.timeout(90000)});
    const body = await response.text();
    assert.equal(response.status, 200, body + '\n' + log);
    assert.equal(rounds, STEPS.length + 1, log);
    assert.equal(toolResults.length, STEPS.length, JSON.stringify(toolResults));
    const hooks = readFileSync(join(root, 'hooks.jsonl'), 'utf8').trim().split('\n').map(line => JSON.parse(line));
    console.log(JSON.stringify({hooks, results: toolResults.map(result => JSON.stringify(result.content).slice(0, 400))}));
    assert.equal(hooks.length, STEPS.length);
    STEPS.forEach((step, index) => {
      const text = JSON.stringify(toolResults[index].content);
      assert.equal(hooks[index].tool, 'exec', step.command);
      if (step.exitCode !== undefined) {
        assert.deepEqual({status: hooks[index].status, exitCode: hooks[index].exitCode},
          {status: 'completed', exitCode: step.exitCode}, step.command + '\n' + text);
      } else {
        assert.notEqual(hooks[index].exitCode, 0, step.command + '\n' + text);
      }
      if (step.output) assert.ok(text.includes(step.output), step.command + '\n' + text);
    });
  } finally {
    if (child && child.exitCode === null) {
      const closed = once(child, 'close');
      process.kill(-child.pid, 'SIGTERM');
      await Promise.race([closed, delay(3000)]);
      if (child.exitCode === null) { process.kill(-child.pid, 'SIGKILL'); await closed; }
    }
    upstream.closeAllConnections();
    await new Promise(resolve => upstream.close(resolve));
    // Only this fixture's uniquely prefixed sandbox containers.
    const names = spawnSync('docker', ['ps', '-aq', '--filter', 'name=^' + prefix], {encoding: 'utf8'});
    assert.equal(names.status, 0);
    for (const id of names.stdout.trim().split(/\s+/).filter(Boolean)) assert.equal(spawnSync('docker', ['rm', '-f', id]).status, 0);
    rmSync(root, {recursive: true, force: true});
  }
});
