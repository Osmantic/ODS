// Characterize the pinned runtime before enabling managed provider activation.
// All inference is a loopback fixture; no host tools or production state.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {mkdtempSync, mkdirSync, readFileSync, writeFileSync, symlinkSync, copyFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';

const pkg = process.env.OPENCLAW_PACKAGE;
const requiredContract = process.env.OPENCLAW_REQUIRED_PLUGIN_TESTS === '1';
test('pinned retained-session overrides require independent run admission', {skip: !pkg, timeout: 180000}, async () => {
  assert.equal(JSON.parse(readFileSync(join(pkg, 'package.json'))).version, '2026.6.33');
  const root = mkdtempSync(join(tmpdir(), 'ods-activation-overrides-'));
  const file = join(root, 'openclaw.json');
  const requests = [], observations = [];
  const upstream = createServer(async (req, res) => {
    let bytes = '';
    for await (const chunk of req) bytes += chunk;
    const body = JSON.parse(bytes);
    requests.push({model: body.model, auth: req.headers.authorization});
    res.writeHead(200, {'Content-Type': 'text/event-stream'});
    res.end('data: ' + JSON.stringify({id: 'fixture', object: 'chat.completion.chunk',
      model: 'fixture', choices: [{index: 0, delta: {role: 'assistant', content: 'legacy-observed'}, finish_reason: 'stop'}],
      usage: {prompt_tokens: 20, completion_tokens: 4, total_tokens: 24}}) + '\n\ndata: [DONE]\n\n');
  });
  let active;
  const stop = async () => {
    if (!active) return;
    const own = active; active = null;
    if (own.child.exitCode === null && own.child.signalCode === null) {
      try { process.kill(-own.child.pid, 'SIGTERM'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
      await Promise.race([own.closed, delay(3000)]);
      if (own.child.exitCode === null && own.child.signalCode === null) process.kill(-own.child.pid, 'SIGKILL');
    }
    await own.closed;
    writeFileSync(join(root, own.name + '.log'), own.log, {mode: 0o600});
  };
  try {
    upstream.listen(0, '127.0.0.1'); await once(upstream, 'listening');
    const portProbe = createServer(); portProbe.listen(0, '127.0.0.1'); await once(portProbe, 'listening');
    const port = portProbe.address().port; await new Promise(r => portProbe.close(r));
    const provider = (baseUrl, id) => ({baseUrl, api: 'openai-completions', apiKey: 'fixture-auth',
      models: [{id, name: id, contextWindow: 32768, maxTokens: 1024, reasoning: false, input: ['text']}]});
    const config = {update: {checkOnStart: false}, logging: {file: join(root, 'runtime.log')},
      gateway: {mode: 'local', port, bind: 'loopback', auth: {mode: 'token', token: 'fixture-gateway-key'},
        http: {endpoints: {chatCompletions: {enabled: true}}}},
      agents: {defaults: {workspace: join(root, 'workspace'), skipBootstrap: true, heartbeat: {every: '0m'},
        model: {primary: 'legacy/fixture', fallbacks: []}}, list: [{id: 'pixel', default: true}]},
      models: {providers: {legacy: provider(`http://127.0.0.1:${upstream.address().port}/v1`, 'fixture')}},
      tools: {deny: ['*']}, plugins: {allow: [], entries: {}}};
    const start = async name => {
      writeFileSync(file, JSON.stringify(config), {mode: 0o600});
      const child = spawn(process.execPath, [join(pkg, 'openclaw.mjs'), 'gateway', 'run'], {
        cwd: root, detached: true, stdio: ['ignore', 'pipe', 'pipe'], env: {
          PATH: process.env.PATH, HOME: root, TMPDIR: root, OPENCLAW_SKIP_CHANNELS: '1',
          ...(requiredContract && name !== 'baseline' ? {OPENCLAW_REQUIRED_PLUGINS: JSON.stringify({
            version: 1, plugins: [{id: 'ods-activation-fixture', hooks: ['before_agent_run']}]
          })} : {}),
          OPENCLAW_STATE_DIR: join(root, 'state'), OPENCLAW_CONFIG_PATH: file,
          XDG_CONFIG_HOME: join(root, 'xdg'), XDG_CACHE_HOME: join(root, 'cache')}});
      const own = {child, name, closed: once(child, 'close'), log: '', listeningObserved: false}; active = own;
      child.stdout.on('data', b => {own.log += b;}); child.stderr.on('data', b => {own.log += b;});
      for (let n = 0; n < 200 && child.exitCode === null; n++) {
        try {
          const health = await fetch(`http://127.0.0.1:${port}/health`, {signal: AbortSignal.timeout(500)});
          own.listeningObserved = true;
          if (health.ok) return true;
        }
        catch { /* Observe the same process; never restart on polling timeout. */ }
        await delay(100);
      }
      return false;
    };
    const chat = async () => {
      const response = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {
        method: 'POST', headers: {'Content-Type': 'application/json', Authorization: 'Bearer fixture-gateway-key'},
        body: JSON.stringify({model: 'openclaw:pixel', user: 'retained-override', stream: false,
          messages: [{role: 'user', content: 'Return the fixed fixture answer.'}]}), signal: AbortSignal.timeout(20000)});
      return {status: response.status, text: await response.text()};
    };
    assert.ok(await start('baseline'), active?.log.slice(-2000));
    assert.match((await chat()).text, /legacy-observed/); assert.equal(requests.length, 1);
    await stop();
    const sessionsFile = join(root, 'state/agents/pixel/sessions/sessions.json');
    const seed = JSON.parse(readFileSync(sessionsFile));
    const entries = Object.values(seed).filter(entry => entry.sessionId);
    assert.equal(entries.length, 1);
    const sessionId = entries[0].sessionId;
    const seedOverride = () => {
      const sessions = JSON.parse(readFileSync(sessionsFile));
      for (const entry of Object.values(sessions)) if (entry.sessionId === sessionId) {
        entry.providerOverride = 'legacy'; entry.modelOverride = 'fixture';
      }
      writeFileSync(sessionsFile, JSON.stringify(sessions), {mode: 0o600});
    };
    config.agents.defaults.model = {primary: 'ods-policy/managed', fallbacks: []};
    config.agents.list[0].model = {primary: 'ods-policy/managed', fallbacks: []};
    config.models.providers['ods-policy'] = provider('http://127.0.0.1:1/v1', 'managed');
    const plugin = join(root, 'plugin'); mkdirSync(plugin, {mode: 0o700});
    mkdirSync(join(root, 'node_modules'), {mode: 0o700});
    symlinkSync(resolve(pkg), join(root, 'node_modules/openclaw'));
    copyFileSync(new URL('../plugin/provider-routing.mjs', import.meta.url), join(plugin, 'provider-routing.mjs'));
    copyFileSync(new URL('../plugin/handoff-approval.mjs', import.meta.url), join(plugin, 'handoff-approval.mjs'));
    writeFileSync(join(plugin, 'package.json'), JSON.stringify({name: 'ods-activation-fixture', version: '1.0.0',
      type: 'module', openclaw: {extensions: ['./index.mjs']}}), {mode: 0o600});
    writeFileSync(join(plugin, 'openclaw.plugin.json'), JSON.stringify({id: 'ods-activation-fixture',
      providers: ['ods-policy'], activation: {onStartup: true},
      configSchema: {type: 'object', properties: {}, additionalProperties: false}}), {mode: 0o600});
    for (const variant of ['absent', 'throwing', 'refused', 'guarded', 'guarded-after-selection',
      'missing-load-path', 'register-throws', ...(requiredContract ? ['guarded-optional-error', 'guarded-reload'] : [])]) {
      seedOverride();
      if (variant !== 'absent') {
        config.plugins = {allow: ['ods-activation-fixture'], load: {paths: [plugin]},
          entries: {'ods-activation-fixture': {enabled: true, hooks: {allowConversationAccess: true}}}};
        const selectedThenFailed = variant === 'guarded-after-selection';
        const acquire = selectedThenFailed ? `async()=>(${JSON.stringify({baseUrl: config.models.providers.legacy.baseUrl,
          token: 'fixture-auth', contextTokens: 32768, maxOutputTokens: 1024, reasoning: false, supportsVision: false})})`
          : "async()=>{throw new Error('refused')}";
        const selection = variant === 'refused' ? 'bridge.beforeModelResolve' : selectedThenFailed
          ? "async(event,ctx)=>{await bridge.beforeModelResolve(event,ctx);throw new Error('deliberate-selection-failure')}"
          : "()=>{throw new Error('deliberate-selection-failure')}";
        writeFileSync(join(plugin, 'index.mjs'), `import {createProviderRoutingBridge} from './provider-routing.mjs';
          const bridge=createProviderRoutingBridge({enabled:true,acquireLease:${acquire},releaseLease:async()=>{}});
          export default {id:'ods-activation-fixture',register(api){
            ${variant === 'register-throws' ? "throw new Error('deliberate-registration-failure');" : ''}
            api.registerProvider(bridge.provider);
            api.on('before_model_resolve', ${selection});
            ${variant.startsWith('guarded') ? "api.on('before_agent_run'," +
              (variant === 'guarded-reload' ? '()=>undefined' : 'bridge.beforeAgentRun') + ');' : ''}
          }};`, {mode: 0o600});
        if (variant === 'missing-load-path') config.plugins.load.paths = [join(root, 'missing-plugin')];
        if (variant === 'guarded-optional-error') {
          const optional = join(root, 'optional'); mkdirSync(optional, {mode: 0o700});
          writeFileSync(join(optional, 'package.json'), JSON.stringify({name: 'optional-fixture',
            version: '1.0.0', type: 'module', openclaw: {extensions: ['./index.mjs']}}), {mode: 0o600});
          writeFileSync(join(optional, 'openclaw.plugin.json'), JSON.stringify({id: 'optional-fixture',
            activation: {onStartup: true}, configSchema: {type: 'object', properties: {}}}), {mode: 0o600});
          writeFileSync(join(optional, 'index.mjs'), "export default {id:'optional-fixture',register(){throw new Error('deliberate-optional-failure')}};", {mode: 0o600});
          config.plugins.load.paths.push(optional);
          config.plugins.allow.push('optional-fixture');
          config.plugins.entries['optional-fixture'] = {enabled: true};
        }
      }
      const before = requests.length;
      const ready = await start(variant);
      const result = ready ? await chat() : {status: null, text: ''};
      const listeningObserved = active.listeningObserved;
      const startupLog = active.log;
      if (variant === 'guarded-reload') {
        // Start with an intentionally permitting fixture hook, so an old pinned
        // registry cannot make the rejection assertion pass merely by vetoing.
        assert.match(result.text, /legacy-observed/);
        const beforeReload = requests.length;
        config.plugins.entries['ods-activation-fixture'].enabled = false;
        writeFileSync(file, JSON.stringify(config), {mode: 0o600});
        for (let n = 0; n < 100 && !active.log.includes('Required plugin configuration unavailable'); n++) await delay(100);
        assert.match(active.log, /Required plugin configuration unavailable/, 'observe rejected reload before probing');
        const blocked = await chat();
        assert.equal(requests.length, beforeReload, 'rejected reload must not use retained permissive hooks');
        observations.push({variant: 'reload-disabled', status: blocked.status, legacyCalls: 0});
        await stop();
        config.plugins.entries['ods-activation-fixture'].enabled = true;
        assert.ok(await start('repaired-restart'), active.log.slice(-2000));
        assert.match((await chat()).text, /legacy-observed/);
        assert.equal(requests.length, beforeReload + 1, 'verified restart restores admission');
      }
      await stop();
      const calls = requests.length - before;
      observations.push({variant, ready, listeningObserved, status: result.status, legacyCalls: calls,
        legacyAnswer: result.text.includes('legacy-observed')});
      // This is characterization, not acceptance of unsafe activation. A future
      // implementation must refuse activation or guard these unsafe branches.
      assert.equal(ready, requiredContract ? variant.startsWith('guarded') : variant !== 'missing-load-path',
        variant + ' startup must be observed separately from routing refusal');
      assert.equal(calls, variant === 'guarded-reload' ? 2 :
        !requiredContract && ['absent', 'throwing', 'register-throws'].includes(variant) ? 1 : 0,
        JSON.stringify(observations));
      if (requiredContract && !ready) assert.equal(listeningObserved, false, variant + ' bound before required hooks');
      if (variant === 'guarded-optional-error') assert.match(startupLog, /deliberate-optional-failure/);
      assert.ok(Object.values(JSON.parse(readFileSync(sessionsFile))).some(entry => entry.sessionId === sessionId));
    }
    assert.ok(requests.every(item => item.auth === 'Bearer fixture-auth' && item.model === 'fixture'));
  } finally {
    await stop(); upstream.closeAllConnections(); await new Promise(r => upstream.close(r));
    writeFileSync(join(root, 'result.json'), JSON.stringify({requiredContract, observations, requests}), {mode: 0o600});
    console.log('Retained activation characterization:', root);
  }
});
