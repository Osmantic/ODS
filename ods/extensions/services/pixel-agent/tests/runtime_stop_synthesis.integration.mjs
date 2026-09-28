// Real pinned harness + real ingress, deterministic model, disposable state.
// Two targeted page reads succeed, then a fetch fails until the run-progress
// budget stops the response; the answer turn calls a tool again (tower1 round
// 069), so no answer text exists. Delivery then makes one synthesis request
// through OpenClaw's plugin LLM runtime: it must reach the same provider with
// no tools, the fixed instruction and the wrapped page excerpts, and its reply
// is delivered as the failed-status partial answer with the host notes.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {mkdtempSync, mkdirSync, writeFileSync, cpSync, symlinkSync, readFileSync, rmSync, existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {createIngressServer} from '../host/pixel_ingress.mjs';
import {RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';
import {PROGRESS_FINALIZATION_INSTRUCTION, PROGRESS_FINALIZATION_NOTE, PROGRESS_READ_PAGES_HEADING} from '../plugin/progress-finalization.mjs';
import {STOP_SYNTHESIS_INSTRUCTION, STOP_SYNTHESIS_NOTE} from '../plugin/stop-synthesis.mjs';

const pkg = process.env.OPENCLAW_PACKAGE;
const PAGES = [
  {url: 'https://example.org/rtx-5070', text: 'GeForce RTX 5070 specifications\nMemory: 12 GB GDDR7\nTotal Graphics Power: 250 W'},
  {url: 'https://example.org/rtx-5070-retail', text: 'PNY GeForce RTX 5070 OC 12GB\nPrice: $549.99\nShips to the United States'},
];
const SYNTH = '```json\n{"name":"RTX 5070","vramGB":12,"boardPowerW":250,"price":549.99,"inStock":null}\n```\n\n' +
  'The specification page lists 12 GB of GDDR7 and 250 W total graphics power (https://example.org/rtx-5070). ' +
  'The retail page shows $549.99 but no stock state, so stock is not found (https://example.org/rtx-5070-retail).';
const ANSWER_ROUND = 7;

test('real harness stop synthesis: one tool-free request through the plugin LLM runtime', {skip: !pkg, timeout: 90000}, async () => {
  const root = mkdtempSync(join(tmpdir(), 'ods-stop-synthesis-'));
  let rounds = 0, log = '', child, ingress, health = 0;
  const synthesis = [];
  const upstream = createServer(async (req, res) => {
    if (req.method === 'GET') { health++; res.writeHead(req.url === '/health' ? 200 : 404); res.end(); return; }
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const request = JSON.parse(Buffer.concat(chunks).toString());
    const sse = delta => {
      res.writeHead(200, {'Content-Type': 'text/event-stream'});
      res.write('data: ' + JSON.stringify({id: 'fixture', object: 'chat.completion.chunk', choices: [{index: 0, delta, finish_reason: null}]}) + '\n\n');
      res.end('data: ' + JSON.stringify({id: 'fixture', object: 'chat.completion.chunk',
        choices: [{index: 0, delta: {}, finish_reason: delta.tool_calls ? 'tool_calls' : 'stop'}]}) + '\n\ndata: [DONE]\n\n');
    };
    if (!request.tools?.length) {
      synthesis.push(request);
      sse({role: 'assistant', content: SYNTH});
      return;
    }
    const round = rounds++;
    const call = (name, args) => ({role: 'assistant', tool_calls: [{index: 0, id: `call-${round}`, type: 'function',
      function: {name, arguments: JSON.stringify(args)}}]});
    sse(round < PAGES.length ? call('pixel_ods_web_extract', {url: PAGES[round].url, query: 'RTX 5070 VRAM board power price'})
      : call('fixture_fetch', {url: `https://example.org/missing-${round}`}));
  });
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve));
  const probe = createServer(); await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
  const port = probe.address().port; await new Promise(resolve => probe.close(resolve));
  mkdirSync(join(root, 'node_modules')); symlinkSync(pkg, join(root, 'node_modules', 'openclaw'));
  const plugin = join(root, 'plugin'); mkdirSync(plugin);
  cpSync(new URL('../plugin/', import.meta.url), join(plugin, 'ods'), {recursive: true});
  writeFileSync(join(plugin, 'package.json'), JSON.stringify({name: 'synthesis-fixture', version: '1.0.0', type: 'module', openclaw: {extensions: ['./index.mjs']}}));
  writeFileSync(join(plugin, 'openclaw.plugin.json'), JSON.stringify({id: 'synthesis-fixture', contracts: {tools: ['fixture_fetch', 'pixel_ods_web_extract']},
    activation: {onStartup: true}, configSchema: {type: 'object', properties: {}}}));
  writeFileSync(join(plugin, 'index.mjs'), `
    import {createToolLoopGuard, createRunAbortAdapter} from './ods/tool-loop-guard.mjs';
    import {createStopSynthesisClient} from './ods/stop-synthesis.mjs';
    import {abortAgentHarnessRun, resolveActiveEmbeddedRunSessionId} from 'openclaw/plugin-sdk/agent-harness-runtime';
    import {appendFileSync} from 'node:fs';
    const record = x => appendFileSync(${JSON.stringify(join(root, 'events.jsonl'))}, JSON.stringify(x) + '\\n');
    const pages = ${JSON.stringify(PAGES)};
    const adapter = createRunAbortAdapter({resolveSessionId: resolveActiveEmbeddedRunSessionId, abort: abortAgentHarnessRun});
    // OpenClaw registers the plugin more than once; like index.js's guard
    // registry, one guard serves every registration (first options win).
    let guard;
    export default {id: 'synthesis-fixture', register(api) {
      guard ??= createToolLoopGuard({abortRun: (id, key, observe) => { const ok = adapter(id, key, observe); record({abort: ok}); return ok; },
        execControl: {signal: () => true}, stopSynthesis: createStopSynthesisClient({runtime: api.runtime, agentId: 'pixel'}),
        info: message => record({info: message}), warn: message => record({warn: message})});
      api.registerHttpRoute({path: '/pixel-ods/verification', auth: 'gateway', match: 'exact', handler: async (req, res) => {
        let body = ''; for await (const part of req) body += part;
        const runId = JSON.parse(body).runId;
        await guard.settleDelivery(runId);
        record({synthesis: guard.stopSynthesisForRun(runId) ?? null});
        res.writeHead(200, {'Content-Type': 'application/json'});
        res.end(JSON.stringify(guard.deliveryVerificationForRun(runId))); return true;
      }});
      api.on('before_prompt_build', (e, c) => guard.observeRun(c, 'pixel', e));
      api.on('model_call_started', (e, c) => guard.observeModelCall(e, c));
      api.on('model_call_ended', (e, c) => guard.observeModelEnd(e, c));
      api.on('before_tool_call', (e, c) => { const d = guard.beforeToolCall(e, c); record({before: e.toolName, block: d?.blockReason?.slice(0, 40) ?? null}); return d; });
      api.on('after_tool_call', (e, c) => guard.afterToolCall(e, c));
      api.on('tool_result_persist', (e, c) => guard.toolResultPersist(e, c));
      api.on('before_message_write', (e, c) => guard.observeAssistantMessage(e, c));
      api.on('before_agent_finalize', (e, c) => guard.beforeAgentFinalize(e, c));
      api.registerTool({name: 'pixel_ods_web_extract', description: 'Read targeted evidence from one public page.',
        parameters: {type: 'object', properties: {url: {type: 'string'}, query: {type: 'string'}}, required: ['url']},
        async execute(id, args) {
          const page = pages.find(entry => entry.url === args.url);
          record({execute: args.url});
          return {content: [{type: 'text', text: 'Targeted evidence from ' + page.url + '\\n<<<EXTERNAL_UNTRUSTED_CONTENT id="f">>>\\n' +
            page.text + '\\n<<<END_EXTERNAL_UNTRUSTED_CONTENT id="f">>>'}],
            details: {boundary: 'public-web-read-only', matched: true, source_url: page.url}};
        }});
      api.registerTool({name: 'fixture_fetch', description: 'Fetch one fixture page.',
        parameters: {type: 'object', properties: {url: {type: 'string'}}, required: ['url']},
        async execute(id, args) { record({execute: args.url}); throw new Error('Web fetch failed (403)'); }});
    }};
  `);
  const config = {logging: {file: join(root, 'runtime.log')}, update: {checkOnStart: false},
    gateway: {mode: 'local', bind: 'loopback', port, auth: {mode: 'token', token: 'fixture-only'}, http: {endpoints: {chatCompletions: {enabled: true}}}},
    agents: {defaults: {workspace: join(root, 'workspace'), skipBootstrap: true, contextTokens: 32768, heartbeat: {every: '0m'}},
      list: [{id: 'pixel', default: true, model: 'fixture/test'}]},
    models: {mode: 'replace', providers: {fixture: {baseUrl: `http://127.0.0.1:${upstream.address().port}/v1`, api: 'openai-completions', apiKey: 'fixture-only',
      models: [{id: 'test', name: 'Fixture', contextWindow: 32768, maxTokens: 4096, reasoning: false, input: ['text']}]}}},
    tools: {allow: ['fixture_fetch', 'pixel_ods_web_extract']}, plugins: {allow: ['synthesis-fixture'], load: {paths: [plugin]},
      entries: {'synthesis-fixture': {enabled: true, hooks: {allowConversationAccess: true}}}}};
  writeFileSync(join(root, 'openclaw.json'), JSON.stringify(config));
  try {
    child = spawn(process.execPath, [join(pkg, 'openclaw.mjs'), 'gateway', 'run'], {cwd: root, detached: true,
      env: {PATH: process.env.PATH, HOME: root, TMPDIR: root, OPENCLAW_STATE_DIR: join(root, 'state'), OPENCLAW_CONFIG_PATH: join(root, 'openclaw.json'), OPENCLAW_SKIP_CHANNELS: '1'},
      stdio: ['ignore', 'pipe', 'pipe']});
    child.stdout.on('data', x => log += x); child.stderr.on('data', x => log += x);
    let ready = false;
    for (let n = 0; n < 250; n++) {
      try { ready = (await fetch(`http://127.0.0.1:${port}/health`, {signal: AbortSignal.timeout(500)})).ok; } catch {}
      if (ready) break; assert.equal(child.exitCode, null, log); await delay(100);
    }
    assert.ok(ready, log);
    ingress = createIngressServer({token: 'fixture-only', gatewayPort: port});
    await new Promise(resolve => ingress.listen(0, '127.0.0.1', resolve));
    const response = await fetch(`http://127.0.0.1:${ingress.address().port}/v1/chat/completions`, {method: 'POST',
      headers: {Authorization: 'Bearer fixture-only', 'Content-Type': 'application/json'},
      body: JSON.stringify({model: 'openclaw:pixel', stream: true, user: 'synthesis-fixture', messages: [{role: 'user',
        content: 'Compare the RTX 5070 VRAM and board power with a US retail price. Actually search the live web and open sources. Return one fenced JSON object then a concise explanation.'}]}),
      signal: AbortSignal.timeout(60000)});
    const body = await response.text();
    assert.equal(response.status, 200, body + '\n' + log);
    const events = existsSync(join(root, 'events.jsonl')) ? readFileSync(join(root, 'events.jsonl'), 'utf8').trim().split('\n').map(JSON.parse) : [];
    const frames = body.split(/\r?\n/).filter(line => line.startsWith('data: {')).map(line => JSON.parse(line.slice(6)));
    const delivered = frames.map(frame => frame.choices?.[0]?.delta?.content ?? '').join('');
    const trace = JSON.stringify({rounds, events, delivered, synthesis: synthesis.length}) + '\n' + log.slice(-3000);
    assert.equal(rounds, ANSWER_ROUND + 1, 'no agent model call beyond the answer turn\n' + trace);
    assert.ok(events.some(x => x.before === 'fixture_fetch' && x.block === PROGRESS_FINALIZATION_INSTRUCTION.slice(0, 40)), trace);
    assert.ok(events.some(x => x.before === 'fixture_fetch' && x.block === RUN_PROGRESS_STOP_REASON.slice(0, 40)), 'the answer turn called a tool\n' + trace);
    assert.deepEqual(events.filter(x => 'abort' in x).map(x => x.abort), [true], trace);

    assert.equal(synthesis.length, 1, 'exactly one synthesis request\n' + trace);
    const [request] = synthesis;
    assert.equal(request.tools, undefined, 'no tools in the synthesis request');
    assert.equal(request.tool_choice, undefined);
    assert.equal(request.model, 'test', 'the same configured provider model');
    assert.equal(request.max_completion_tokens ?? request.max_tokens, 1200, JSON.stringify(Object.keys(request)));
    assert.equal(request.temperature, 0.2);
    assert.equal(request.messages[0].role, 'system');
    assert.equal(request.messages[0].content, STOP_SYNTHESIS_INSTRUCTION);
    assert.equal(request.messages.length, 2);
    const user = request.messages[1].content;
    for (const page of PAGES) assert.ok(user.includes(`URL: ${page.url}\n`), user);
    assert.ok(user.includes('Memory: 12 GB GDDR7') && user.includes('Price: $549.99'), user);
    assert.ok(!user.includes('EXTERNAL_UNTRUSTED_CONTENT'), 'page text is rewrapped as data');
    assert.ok(health >= 1, 'the loopback route health was probed first');
    assert.equal(events.find(x => 'synthesis' in x)?.synthesis?.status, 'answered', trace);

    assert.equal(frames.at(-1).pixel_outcome.status, 'failed', trace);
    assert.ok(delivered.startsWith(SYNTH), trace);
    assert.ok(delivered.includes(`${PROGRESS_FINALIZATION_NOTE}\n\n${STOP_SYNTHESIS_NOTE}`), trace);
    assert.ok(delivered.endsWith(`${PROGRESS_READ_PAGES_HEADING}\n\n- <${PAGES[0].url}>\n- <${PAGES[1].url}>`), trace);
    assert.ok(!delivered.includes(RUN_PROGRESS_STOP_REASON), trace);
  } finally {
    if (ingress) { ingress.closeAllConnections(); await new Promise(resolve => ingress.close(resolve)); }
    if (child && child.exitCode === null) {
      const closed = once(child, 'close'); process.kill(-child.pid, 'SIGTERM');
      await Promise.race([closed, delay(3000)]); if (child.exitCode === null) { process.kill(-child.pid, 'SIGKILL'); await closed; }
    }
    upstream.closeAllConnections(); await new Promise(resolve => upstream.close(resolve)); rmSync(root, {recursive: true, force: true});
  }
});
