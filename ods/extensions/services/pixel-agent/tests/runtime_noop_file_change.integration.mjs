// Actual pinned SDK gateway, agent loop and file tools; deterministic provider,
// no model inference. OPENCLAW_PACKAGE may be the upstream package or one with
// the other ODS repairs applied; this repair is applied to a private copy.
// ODS_NOOP_FILE_CHANGE_RED=1 runs the pinned loop to show the turn ending on
// an unchanged file without an answer.
import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, symlinkSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname, join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {createIngressServer} from '../host/pixel_ingress.mjs';

const installed = process.env.OPENCLAW_PACKAGE;
const red = process.env.ODS_NOOP_FILE_CHANGE_RED === '1';
const hash = text => createHash('sha256').update(text).digest('hex');
const LOOP = 'proxy-Bsfwfsp-.js';
const manifest = JSON.parse(readFileSync(new URL('../host/openclaw-noop-file-change.json', import.meta.url)));
let pkg, runtimeCopy;
if (installed) {
  runtimeCopy = mkdtempSync(join(tmpdir(), 'ods-noop-runtime-'));
  pkg = join(runtimeCopy, 'package');
  cpSync(installed, pkg, {recursive: true});
  // An npm prefix install hoists the runtime's dependencies beside it.
  if (!existsSync(join(installed, 'node_modules'))) symlinkSync(dirname(installed), join(runtimeCopy, 'node_modules'));
  const loop = join(pkg, 'dist', LOOP);
  let source = readFileSync(loop, 'utf8');
  if (hash(source) === manifest.patchedSha256) for (const [before, value] of [...manifest.replacements].reverse()) source = source.replace(value, before);
  assert.equal(hash(source), manifest.sourceSha256, 'refuse unknown SDK source');
  if (!red) {
    for (const [before, value] of manifest.replacements) { assert.equal(source.split(before).length, 2); source = source.replace(before, () => value); }
    assert.equal(hash(source), manifest.patchedSha256);
  }
  writeFileSync(loop, source);
}
after(() => { if (runtimeCopy) rmSync(runtimeCopy, {recursive: true, force: true}); });
const skip = !pkg || process.platform === 'win32';

test('in the pinned release only unchanged-file results of write, edit and apply_patch request termination', {skip: !pkg}, () => {
  const dist = join(pkg, 'dist');
  // A word boundary leaves out the progress-bar option "indeterminate: true".
  const flag = /\bterminate: true/g;
  const count = text => [...text.matchAll(flag)].length;
  const producers = readdirSync(dist).filter(name => name.endsWith('.js') && count(readFileSync(join(dist, name), 'utf8')) > 0);
  // tool-split: tools delegated to an API client; ssh-config: a child-process option.
  assert.deepEqual(producers.sort(), ['agent-tools-D1DOpg6D.js', 'sessions-CZbwb3_c.js', 'ssh-config-BiPg4-Kv.js', 'tool-split-C61cf48Y.js']);
  const sessions = readFileSync(join(dist, 'sessions-CZbwb3_c.js'), 'utf8');
  const sites = [...sessions.matchAll(flag)].map(match => sessions.slice(match.index - 160, match.index));
  assert.equal(sites.length, 3);
  for (const before of sites) {
    assert.match(before, /textResult\(`No changes made to \$\{path\}\. The (?:file already has identical content|replacement text is identical to the original|replacement produced identical content)\.`, void 0\),\s*$/);
  }
  const tools = readFileSync(join(dist, 'agent-tools-D1DOpg6D.js'), 'utf8');
  assert.equal(count(tools), 1);
  assert.match(tools, /\.\.\.result\.noOp \? \{ terminate: true \} : \{\}/);
  const split = readFileSync(join(dist, 'tool-split-C61cf48Y.js'), 'utf8');
  assert.match(split, /message: "Tool execution delegated to client"\n\t\t\t\t\t\}\),\n\t\t\t\t\tterminate: true/);
});

async function gateway(root, config, run) {
  const probe = createServer(); await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
  const port = probe.address().port; await new Promise(resolve => probe.close(resolve));
  config.gateway = {mode: 'local', bind: 'loopback', port, auth: {mode: 'token', token: 'fixture-only-0123456789abcdef'}, http: {endpoints: {chatCompletions: {enabled: true}}}};
  writeFileSync(join(root, 'openclaw.json'), JSON.stringify(config));
  let log = '', child;
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
    return await run(port, () => log);
  } finally {
    if (child && child.exitCode === null) { const closed = once(child, 'close'); process.kill(-child.pid, 'SIGTERM'); await Promise.race([closed, delay(3000)]); if (child.exitCode === null) { process.kill(-child.pid, 'SIGKILL'); await closed; } }
  }
}

function provider(script) {
  const requests = [];
  const server = createServer(async (req, res) => {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const messages = JSON.parse(Buffer.concat(chunks).toString()).messages;
    requests.push(messages);
    const step = script[requests.length - 1] ?? {text: 'Unexpected extra model call.'};
    const delta = step.calls
      ? {role: 'assistant', tool_calls: step.calls.map(([name, args], index) => ({index, id: `call-${requests.length}-${index}`, type: 'function', function: {name, arguments: JSON.stringify(args)}}))}
      : {role: 'assistant', content: step.text};
    res.writeHead(200, {'Content-Type': 'text/event-stream'});
    res.write('data: ' + JSON.stringify({id: 'fixture', object: 'chat.completion.chunk', choices: [{index: 0, delta, finish_reason: null}]}) + '\n\n');
    res.end('data: ' + JSON.stringify({id: 'fixture', object: 'chat.completion.chunk', choices: [{index: 0, delta: {}, finish_reason: step.calls ? 'tool_calls' : 'stop'}]}) + '\n\ndata: [DONE]\n\n');
  });
  return {server, requests};
}

const model = port => ({mode: 'replace', providers: {fixture: {baseUrl: 'http://127.0.0.1:' + port + '/v1', api: 'openai-completions', apiKey: 'fixture-only',
  models: [{id: 'test', name: 'Fixture', contextWindow: 32768, maxTokens: 4096, reasoning: false, input: ['text']}]}}});
const ANSWER = 'The notes file already had that content.';
const NOTE = 'unchanged\n';
const PATCH = '*** Begin Patch\n*** Update File: notes.txt\n@@\n-unchanged\n+unchanged\n*** End Patch';
const UNCHANGED = {
  write: [['write', {path: 'notes.txt', content: NOTE}]],
  edit: [['edit', {path: 'notes.txt', edits: [{oldText: 'unchanged', newText: 'unchanged'}]}]],
  apply_patch: [['apply_patch', {input: PATCH}]],
  batch: [['write', {path: 'notes.txt', content: NOTE}], ['edit', {path: 'notes.txt', edits: [{oldText: 'unchanged', newText: 'unchanged'}]}], ['apply_patch', {input: PATCH}]],
};

for (const [kind, calls] of Object.entries(UNCHANGED)) test(`the model answers after an unchanged-file ${kind} as its last call`, {skip, timeout: 120000}, async () => {
  const root = mkdtempSync(join(tmpdir(), 'ods-noop-turn-'));
  const workspace = join(root, 'workspace'); mkdirSync(workspace);
  writeFileSync(join(workspace, 'notes.txt'), NOTE);
  const {server, requests} = provider([{calls}, {text: ANSWER}]);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const config = {logging: {file: join(root, 'runtime.log')}, update: {checkOnStart: false},
      agents: {defaults: {workspace, skipBootstrap: true, sandbox: {mode: 'off'}, model: {primary: 'fixture/test'}, contextTokens: 32768, heartbeat: {every: '0m'}},
        list: [{id: 'pixel', default: true, workspace}]},
      models: model(server.address().port),
      tools: {allow: ['write', 'edit', 'apply_patch', 'exec'], exec: {host: 'gateway', security: 'full', ask: 'off'}, loopDetection: {enabled: false}}};
    await gateway(root, config, async (port, log) => {
      const response = await fetch('http://127.0.0.1:' + port + '/v1/chat/completions', {method: 'POST',
        headers: {'Content-Type': 'application/json', Authorization: 'Bearer fixture-only-0123456789abcdef'},
        body: JSON.stringify({model: 'openclaw:pixel', stream: true, user: 'noop-' + kind, messages: [{role: 'user', content: 'Make sure notes.txt says unchanged.'}]}),
        signal: AbortSignal.timeout(90000)});
      const body = await response.text();
      assert.equal(response.status, 200, body + '\n' + log());
      const results = requests[1]?.filter(message => message.role === 'tool').map(message => typeof message.content === 'string' ? message.content : JSON.stringify(message.content));
      console.log(JSON.stringify({red, kind, modelCalls: requests.length, answered: body.includes(ANSWER), results}));
      if (red) {
        assert.equal(requests.length, 1, 'the pinned loop ends the turn after the unchanged file');
        assert.doesNotMatch(body, new RegExp(ANSWER));
      } else {
        assert.equal(requests.length, 2, log());
        assert.equal(results.length, calls.length);
        for (const text of results) assert.match(text, /No changes made to notes\.txt/);
        assert.match(body, new RegExp(ANSWER));
      }
      assert.equal(readFileSync(join(workspace, 'notes.txt'), 'utf8'), NOTE);
    });
  } finally {
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
    rmSync(root, {recursive: true, force: true});
  }
});

// Tower2 round 092 (coding-v1), with the real Pixel guard and ODS ingress:
// publish, a CLI check, then a write of identical bytes as the last call. The
// host byte comparison answers slowly, after the attempt has ended when the
// pinned loop ends the turn on that write.
test('R092 coding-v1: the preview stays verified and the model answers after an identical write', {skip, timeout: 120000}, async () => {
  const root = mkdtempSync(join(tmpdir(), 'ods-noop-pixel-'));
  const workspace = join(root, 'workspace'); mkdirSync(workspace);
  const events = join(root, 'events.jsonl');
  const html = '<!doctype html><title>Expense report</title><p>Totals</p>';
  const {server, requests} = provider([
    {calls: [['write', {path: 'signal-garden/index.html', content: html}]]},
    {calls: [['pixel_ods_workspace_preview', {relativeDirectory: 'Playground/signal-garden'}]]},
    {calls: [['exec', {command: "node -e \"console.log(JSON.stringify({food:'16.00'}))\"", workdir: workspace}]]},
    {calls: [['write', {path: 'signal-garden/index.html', content: html}]]},
    {text: 'The report page is published and its check passed.'},
  ]);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  mkdirSync(join(root, 'node_modules')); symlinkSync(pkg, join(root, 'node_modules', 'openclaw'));
  const plugin = join(root, 'plugin'); mkdirSync(plugin);
  writeFileSync(join(plugin, 'package.json'), JSON.stringify({name: 'noop-fixture', version: '1.0.0', type: 'module', openclaw: {extensions: ['./index.mjs']}}));
  writeFileSync(join(plugin, 'openclaw.plugin.json'), JSON.stringify({id: 'noop-fixture', activation: {onStartup: true}, contracts: {tools: ['pixel_ods_workspace_preview']}, configSchema: {type: 'object', properties: {}}}));
  const page = join(workspace, 'Playground/signal-garden/index.html');
  writeFileSync(join(plugin, 'index.mjs'), `
    import {createToolLoopGuard} from ${JSON.stringify(new URL('../plugin/tool-loop-guard.mjs', import.meta.url).href)};
    import {createWorkspacePreviewTool} from ${JSON.stringify(new URL('../plugin/workspace-preview.mjs', import.meta.url).href)};
    import {readFileSync, appendFileSync} from 'node:fs';
    import {createHash} from 'node:crypto';
    import {setTimeout as delay} from 'node:timers/promises';
    const note = event => appendFileSync(${JSON.stringify(events)}, JSON.stringify({event, at: Date.now()}) + '\\n');
    const digest = () => createHash('sha256').update(readFileSync(${JSON.stringify(page)})).digest('hex');
    const publisher = createWorkspacePreviewTool({request: async request => {
      const bytes = readFileSync(${JSON.stringify(page)}), hash = digest(), siteId = 'site-' + hash.slice(0, 24);
      note('publish');
      return {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'succeeded', relativeDirectory: request.relativeDirectory,
        port: 9437, siteId, url: 'http://' + siteId + '.localhost:9437/' + siteId + '/', sha256: hash, entrySha256: hash,
        entryFile: 'index.html', files: 1, bytes: bytes.length, httpStatus: 200, readbackVerified: true, executable: false, overwritten: false,
        boundary: 'Create-only static-site snapshot from the configured Pixel workspace to a dedicated loopback preview origin; no arbitrary host path, network destination, server process, overwrite, or execution authority.'};
    }});
    const guard = createToolLoopGuard({verifyWorkspacePreview: async receipt => {
      note('compare-start'); await delay(400);
      const same = digest() === receipt.sha256; note(same ? 'compare-match' : 'compare-mismatch'); return same;
    }});
    export default {id: 'noop-fixture', register(api) {
      api.registerTool(publisher);
      api.on('before_prompt_build', (event, ctx) => guard.observeRun(ctx, 'pixel', event, {workspaceRoot: ${JSON.stringify(workspace)}}));
      api.on('before_tool_call', (event, ctx) => guard.beforeToolCall(event, ctx));
      api.on('after_tool_call', (event, ctx) => guard.afterToolCall(event, ctx));
      api.on('tool_result_persist', (event, ctx) => { if (ctx.toolName === 'write') note('saved-write'); return guard.toolResultPersist(event, ctx); });
      api.on('agent_end', (event, ctx) => { note('agent_end'); guard.endPreviewRevalidation(event, ctx); guard.observeAgentEnd(event, ctx); });
      api.on('before_agent_finalize', async (event, ctx) => {
        note('finalize');
        await guard.revalidateWorkspacePreview(event, ctx);
        await guard.recoverWorkspacePreview(event, ctx);
        return guard.beforeAgentFinalize(event, ctx);
      });
      api.on('reply_payload_sending', event => guard.replyPayloadSending(event));
      api.registerHttpRoute({path: '/pixel-ods/verification', auth: 'gateway', match: 'exact', handler: async (req, res) => {
        let raw = ''; for await (const chunk of req) raw += chunk;
        const {runId} = JSON.parse(raw);
        await guard.settleDelivery(runId);
        note('verification');
        res.writeHead(200, {'Content-Type': 'application/json'});
        res.end(JSON.stringify(guard.deliveryVerificationForRun(runId))); return true;
      }});
    }};
  `);
  let ingress;
  try {
    const config = {logging: {file: join(root, 'runtime.log')}, update: {checkOnStart: false},
      agents: {defaults: {workspace, skipBootstrap: true, model: {primary: 'fixture/test'}, contextTokens: 32768, heartbeat: {every: '0m'}}, list: [{id: 'pixel', default: true, workspace}]},
      models: model(server.address().port),
      tools: {allow: ['write', 'exec', 'pixel_ods_workspace_preview'], exec: {host: 'gateway', security: 'full', ask: 'off'}, loopDetection: {enabled: false}},
      plugins: {allow: ['noop-fixture'], load: {paths: [plugin]}, entries: {'noop-fixture': {enabled: true, hooks: {allowConversationAccess: true}}}}};
    await gateway(root, config, async (port, log) => {
      ingress = createIngressServer({token: 'fixture-only-0123456789abcdef', gatewayPort: port});
      await new Promise(resolve => ingress.listen(0, '127.0.0.1', resolve));
      const response = await fetch(`http://127.0.0.1:${ingress.address().port}/v1/chat/completions`, {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({model: 'openclaw:pixel', stream: true, user: 'noop-fixture', messages: [{role: 'user', content: 'Build and publish a website in existing signal-garden.'}]}),
        signal: AbortSignal.timeout(60000)});
      const body = await response.text();
      assert.equal(response.status, 200, body + '\n' + log());
      const frames = body.split(/\r?\n/).filter(line => line.startsWith('data: {')).map(line => JSON.parse(line.slice(6)));
      const trace = existsSync(events) ? readFileSync(events, 'utf8').trim().split('\n').map(line => JSON.parse(line).event) : [];
      console.log(JSON.stringify({red, modelCalls: requests.length, outcome: frames.at(-1)?.pixel_outcome, trace}));
      assert.equal(readFileSync(page, 'utf8'), html);
      assert.equal(trace.filter(event => event === 'publish').length, 1, 'no republication');
      assert.equal(frames.at(-1)?.pixel_outcome?.status, 'passed', body + '\n' + log());
      // The saved CLI check and the saved identical write each requested one
      // comparison; the instant fixture model wrote the write while the first
      // was still waiting, so it answered for an older generation.
      assert.deepEqual(trace.filter(event => event.startsWith('compare-')), ['compare-start', 'compare-match', 'compare-start', 'compare-match']);
      if (red) {
        assert.equal(requests.length, 4, 'the pinned loop ends the turn on the identical write');
        assert.doesNotMatch(body, /report page is published/);
        assert.ok(!trace.includes('finalize'), 'OpenClaw skips before_agent_finalize without answer text');
        // The attempt ended while the comparison started by the saved write was
        // still waiting for the host; delivery waited for its answer.
        assert.ok(trace.lastIndexOf('saved-write') < trace.lastIndexOf('compare-start'));
        assert.ok(trace.indexOf('agent_end') < trace.lastIndexOf('compare-match'), trace.join(' '));
        assert.ok(trace.lastIndexOf('compare-match') < trace.lastIndexOf('verification'));
      } else {
        assert.equal(requests.length, 5, log());
        assert.match(requests[4].filter(message => message.role === 'tool').at(-1).content, /No changes made to .*identical content/);
        assert.match(body, /report page is published and its check passed/);
        // Finalization waited for those comparisons instead of starting another.
        assert.ok(trace.includes('finalize'));
        assert.ok(trace.lastIndexOf('compare-match') < trace.indexOf('agent_end'), trace.join(' '));
      }
    });
  } finally {
    if (ingress) { ingress.closeAllConnections(); await new Promise(resolve => ingress.close(resolve)); }
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
    rmSync(root, {recursive: true, force: true});
  }
});
