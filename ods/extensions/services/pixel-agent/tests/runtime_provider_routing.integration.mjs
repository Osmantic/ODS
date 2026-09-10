// Explicit opt-in: starts only a disposable loopback OpenClaw 2026.6.33 gateway.
// Uses synthetic inference, no production config, paid credentials or host tools.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { spawn, spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, readdirSync, symlinkSync, copyFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID, randomBytes } from 'node:crypto';
import { computeSessionUser } from '../host/pixel_ingress.mjs';
import { setTimeout as delay } from 'node:timers/promises';

const pkg = process.env.OPENCLAW_PACKAGE;
const workerPython = process.env.ODS_PROVIDER_WORKER_PYTHON;
const managedBootstrap = process.env.ODS_MANAGED_BOOTSTRAP === '1';
const modes = managedBootstrap ? [[true, true]] : [[false, false], ...(workerPython ? [[true, false], [true, true]] : [])];
for (const [workerMode, handoffMode] of modes) {
test('pinned gateway session/routing contract; private worker=' + workerMode + '; handoff=' + handoffMode,
  {skip: !pkg, timeout: 300000}, async () => {
    assert.equal(JSON.parse(readFileSync(join(pkg, 'package.json'))).version, '2026.6.33');
    const root = mkdtempSync(join(tmpdir(), 'ods-routing-gateway-'));
    const fixture = join(root, 'lease.json');
    const ownerMode = handoffMode && process.env.ODS_HANDOFF_OWNER_API === '1';
    const ownerScope = ownerMode ? process.env.ODS_OWNER_SCOPE : undefined;
    assert.ok(ownerScope === undefined || ['task','conversation','default'].includes(ownerScope));
    if (managedBootstrap) {
      assert.ok(workerPython && ownerMode && ownerScope);
      assert.equal(process.env.ODS_PREPARE_LEASE_RUNTIME, '1', 'bootstrap requires custody-checked prepared launcher');
    }
    const browserMode = ownerMode && process.env.ODS_HANDOFF_BROWSER === '1';
    let ownerChild, ownerClosed, ownerUrl, ownerLog = '';
    const requests = [];
    let parallelActive = 0, maxParallelActive = 0, releaseParallel;
    const parallelGate = new Promise(r => {releaseParallel = r;});
    const upstream = createServer(async (req, res) => {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const body = JSON.parse(Buffer.concat(chunks));
      requests.push({body, path: req.url, auth: req.headers.authorization});
      const stronger = handoffMode && req.headers.authorization === 'Bearer stronger-upstream-key';
      const validAuth = workerMode ? req.headers.authorization === 'Bearer fixture-upstream-key' || stronger
        : req.headers.authorization?.startsWith('Bearer fixture-lease-chatcmpl_');
      if (req.url !== '/v1/chat/completions' || !validAuth || body.model !== (stronger ? 'stronger-model' : workerMode ? 'fixture-model' : 'ods/pixel')) {
        res.writeHead(401); res.end(JSON.stringify({error: {message: 'fixture wire contract mismatch'}})); return;
      }
      if (requests.length >= 4 && !handoffMode) {
        parallelActive++; maxParallelActive = Math.max(maxParallelActive, parallelActive);
        if (parallelActive === 2) releaseParallel();
        await Promise.race([parallelGate, delay(5000)]);
        parallelActive--;
      }
      const handoffTool = handoffMode && requests.length === 3;
      const first = requests.length === 1 || handoffTool;
      const delta = first ? {role: 'assistant', tool_calls: [{index: 0, id: handoffTool ? 'handoff-call-2' : 'witness-call-1',
        type: 'function', function: {name: handoffTool ? 'fixture_handoff_witness' : 'fixture_witness', arguments: '{}'}}]}
        : {role: 'assistant', content: requests.length === 2 ? 'first-turn-complete' : 'second-turn-complete'};
      res.writeHead(200, {'Content-Type': 'text/event-stream'});
      res.write('data: ' + JSON.stringify({id: 'fixture-response', object: 'chat.completion.chunk',
        model: 'ods/pixel', choices: [{index: 0, delta, finish_reason: null}]}) + '\n\n');
      res.end('data: ' + JSON.stringify({id: 'fixture-response', object: 'chat.completion.chunk',
        model: 'ods/pixel', choices: [{index: 0, delta: {}, finish_reason: first ? 'tool_calls' : 'stop'}],
        usage: {prompt_tokens: 100, completion_tokens: 10, total_tokens: 110}}) + '\n\ndata: [DONE]\n\n');
    });
    try {
    upstream.listen(0, '127.0.0.1'); await once(upstream, 'listening');
    const baseUrl = `http://127.0.0.1:${upstream.address().port}/v1`;
    const providerDirectory = join(root, 'pixel-providers');
    let workerCommand;
    if (workerMode) {
      const setup = spawnSync(workerPython, ['-I', '-B', fileURLToPath(new URL('./fixtures/provider-lease-config.py', import.meta.url)),
        providerDirectory, baseUrl, ...(handoffMode ? ['--handoff'] : [])], {encoding: 'utf8', timeout: 180000,
        env: {PATH: process.env.PATH, ODS_PREPARE_LEASE_RUNTIME: process.env.ODS_PREPARE_LEASE_RUNTIME || '0'}});
      assert.equal(setup.status, 0, setup.stderr);
      const workerPath = process.env.ODS_PREPARE_LEASE_RUNTIME === '1' ? '../../../../bin/ods-pixel-route-lease'
        : '../../../../bin/pixel_provider/route_worker.py';
      workerCommand = [workerPython, '-I', '-B', fileURLToPath(new URL(workerPath, import.meta.url))];
    }
    if (ownerMode) {
      ownerChild = spawn(workerPython, ['-I','-B',fileURLToPath(new URL('./fixtures/handoff-owner-api.py',import.meta.url)),
        '--data',root,'--ready',join(root,'owner-api.json'),...(browserMode ? ['--dashboard',process.env.ODS_HANDOFF_DASHBOARD] : [])],
      {stdio: ['ignore','pipe','pipe'], detached: true});
      ownerClosed = once(ownerChild,'close');
      ownerChild.stdout.on('data',chunk => {ownerLog += chunk;}); ownerChild.stderr.on('data',chunk => {ownerLog += chunk;});
      for (let i=0;i<150;i++) {
        try {
          ownerUrl = JSON.parse(readFileSync(join(root,'owner-api.json'),'utf8')).url;
          if ((await fetch(ownerUrl+'/health')).ok) break;
        } catch { /* bounded private fixture startup */ }
        assert.equal(ownerChild.exitCode,null,ownerLog); await delay(100);
      }
      assert.ok(ownerUrl,ownerLog);
      assert.equal((await fetch(ownerUrl+'/api/pixel/handoff/list',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).status,401);
      console.log('Owner API:',ownerUrl,'Evidence:',root);
    }
    const saveLease = (revision, refuse = false) => writeFileSync(fixture,
      JSON.stringify({baseUrl, revision, refuse, handoff: handoffMode && [2, 3].includes(revision)}), {mode: 0o600});
    saveLease(1);
    const portProbe = createServer(); portProbe.listen(0, '127.0.0.1'); await once(portProbe, 'listening');
    const port = portProbe.address().port; await new Promise(r => portProbe.close(r));
    mkdirSync(join(root, 'node_modules')); symlinkSync(resolve(pkg), join(root, 'node_modules/openclaw'));
    const plugin = join(root, 'plugin'); mkdirSync(plugin);
    const original = readFileSync(new URL('./fixtures/provider-routing-gateway.mjs', import.meta.url), 'utf8');
    writeFileSync(join(plugin, 'index.mjs'), original.replaceAll('../../plugin/', './'));
    copyFileSync(fileURLToPath(new URL('../plugin/provider-routing.mjs', import.meta.url)), join(plugin, 'provider-routing.mjs'));
    copyFileSync(fileURLToPath(new URL('../plugin/handoff-approval.mjs', import.meta.url)), join(plugin, 'handoff-approval.mjs'));
    copyFileSync(fileURLToPath(new URL('../plugin/provider-lease-worker.mjs', import.meta.url)), join(plugin, 'provider-lease-worker.mjs'));
    copyFileSync(fileURLToPath(new URL('../plugin/handoff-owner-worker.mjs', import.meta.url)), join(plugin, 'handoff-owner-worker.mjs'));
    copyFileSync(fileURLToPath(new URL('../plugin/provider-bootstrap.mjs', import.meta.url)), join(plugin, 'provider-bootstrap.mjs'));
    copyFileSync(fileURLToPath(new URL('../plugin/access-runtime.mjs', import.meta.url)), join(plugin, 'access-runtime.mjs'));
    copyFileSync(fileURLToPath(new URL('../plugin/settings-runtime-readback.mjs', import.meta.url)), join(plugin, 'settings-runtime-readback.mjs'));
    copyFileSync(fileURLToPath(new URL('./fixtures/managed-admission.mjs', import.meta.url)), join(plugin, 'managed-admission.mjs'));
    writeFileSync(join(plugin, 'package.json'), JSON.stringify({name: 'ods-routing-fixture', version: '1.0.0',
      type: 'module', openclaw: {extensions: ['./index.mjs']}}));
    writeFileSync(join(plugin, 'openclaw.plugin.json'), JSON.stringify({id: 'ods-routing-fixture',
      providers: ['ods-policy'], contracts: {tools: ['fixture_witness','fixture_handoff_witness']}, activation: {onStartup: true},
      configSchema: {type: 'object', properties: {}, additionalProperties: false}}));
    const config = {
      logging: {file: join(root, 'runtime.log')},
      update: {checkOnStart: false},
      gateway: {mode: 'local', port, bind: 'loopback', auth: {mode: 'token', token: 'fixture-gateway-key'},
        http: {endpoints: {chatCompletions: {enabled: true}}}},
      agents: {defaults: {workspace: join(root, 'workspace'), skipBootstrap: true,
        model: {primary: 'ods-policy/managed', fallbacks: []}, contextTokens: 32768, maxConcurrent: 2,
        heartbeat: {every: '0m'}}, list: [{id: 'pixel', default: true}]},
      models: {mode: 'replace', providers: {'ods-policy': {baseUrl: 'http://127.0.0.1:1/v1',
        api: 'openai-completions', apiKey: 'unused-placeholder', models: [{id: 'managed', name: 'ODS managed',
          contextWindow: 32768, maxTokens: 4096, reasoning: false, input: ['text']}]}}},
      tools: {allow: ['fixture_witness','fixture_handoff_witness']},
      plugins: {allow: ['ods-routing-fixture'], load: {paths: [plugin]},
        entries: {'ods-routing-fixture': {enabled: true, hooks: {allowConversationAccess: true}}}},
    };
    let deployment;
    if (managedBootstrap) {
      deployment = {binding: {schemaVersion: 1, activationId: randomUUID(), revision: 1, allowCloud: false},
        sourceRoot: fileURLToPath(new URL('../../../..', import.meta.url)).replace(/\/$/, ''),
        hostPython: '/usr/bin/python3', providerDirectory, ownerScopes: true,
        leaseTimeoutSeconds: 180, approvalTimeoutSeconds: 60};
      // Use the actual Python projection, not a second independently maintained
      // imitation. This fixture explicitly grants only its disposable hooks.
      config.plugins.allow = ['pixel-ods'];
      config.plugins.entries = {'pixel-ods': {enabled: true, hooks: {allowConversationAccess: true}}};
      delete config.models.providers['ods-policy'];
      const projection = spawnSync(workerPython, ['-I', '-B', '-c',
        'import json,sys;sys.path.insert(0,sys.argv[1]);from pixel_provider.activation_config import plan_activation;v=json.load(sys.stdin);b=v["binding"];print(json.dumps(plan_activation(v["config"],revision=b["revision"],allow_cloud=b["allowCloud"],activation_id=b["activationId"])["document"]))',
        join(deployment.sourceRoot, 'bin')], {input: JSON.stringify({config, binding: deployment.binding}), encoding: 'utf8'});
      assert.equal(projection.status, 0, projection.stderr);
      Object.assign(config, JSON.parse(projection.stdout));
      writeFileSync(join(plugin, 'openclaw.plugin.json'), JSON.stringify({id: 'pixel-ods', providers: ['ods-policy'],
        contracts: {tools: ['fixture_witness', 'fixture_handoff_witness']}, activation: {onStartup: true},
        configSchema: {type: 'object', properties: {managedProvider: {type: 'object'}}, additionalProperties: false}}));
    }
    writeFileSync(join(root, 'openclaw.json'), JSON.stringify(config), {mode: 0o600});
    const env = {PATH: process.env.PATH, HOME: root, TMPDIR: root,
      OPENCLAW_STATE_DIR: join(root, 'state'), OPENCLAW_CONFIG_PATH: join(root, 'openclaw.json'),
      XDG_CONFIG_HOME: join(root, 'xdg'), XDG_CACHE_HOME: join(root, 'cache'),
      ODS_ROUTING_FIXTURE: fixture, OPENCLAW_SKIP_CHANNELS: '1'};
    if (workerMode) Object.assign(env, {ODS_LEASE_DIRECTORY: providerDirectory,
      ODS_LEASE_WORKER_COMMAND: JSON.stringify(workerCommand)});
    if (ownerMode) Object.assign(env, {ODS_HANDOFF_OWNER_COMMAND: JSON.stringify([workerPython,'-I','-B',
      fileURLToPath(new URL('../../../../bin/pixel_provider/handoff_worker.py',import.meta.url))]),
      ODS_HANDOFF_BROWSER: browserMode ? '1' : '0'});
    if (ownerScope) env.ODS_OWNER_SCOPE = ownerScope;
    if (managedBootstrap) Object.assign(env, {ODS_MANAGED_BOOTSTRAP: '1', ODS_MANAGED_DEPLOYMENT: JSON.stringify(deployment),
      ODS_ACCESS_FIXTURE_DIRECTORY: join(root, 'access-runtime'),
      OPENCLAW_REQUIRED_PLUGINS: JSON.stringify({version: 1, plugins: [{id: 'pixel-ods',
        hooks: ['before_model_resolve', 'before_agent_run', 'agent_end']}]})});
    const child = spawn(process.execPath, [join(pkg, 'openclaw.mjs'), 'gateway', 'run'],
      {env, cwd: root, stdio: ['ignore', 'pipe', 'pipe'], detached: true});
    let log = ''; child.stdout.on('data', b => { log += b; }); child.stderr.on('data', b => { log += b; });
    const exit = once(child, 'exit');
    const closed = once(child, 'close');
    let stopping;
    const stopGateway = () => stopping ??= (async () => {
      if (child.exitCode === null && child.signalCode === null) {
        try { process.kill(-child.pid, 'SIGTERM'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
        await Promise.race([exit, delay(3000)]);
        if (child.exitCode === null && child.signalCode === null) process.kill(-child.pid, 'SIGKILL');
      }
      await closed;
    })();
    try {
      let ready = false;
      for (let i = 0; i < 200 && child.exitCode === null; i++) {
        try { ready = (await fetch(`http://127.0.0.1:${port}/health`, {signal: AbortSignal.timeout(500)})).ok; }
        catch { /* bounded startup */ }
        if (ready) break;
        await delay(100);
      }
      assert.ok(ready, log.slice(-8000));
      const chat = async (text, user = 'same-native-session') => {
        const res = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {
          method: 'POST', headers: {'Content-Type': 'application/json', Authorization: 'Bearer fixture-gateway-key'},
          body: JSON.stringify({model: 'openclaw:pixel', user: ownerScope ? computeSessionUser({user}) : user, stream: false,
            messages: [{role: 'user', content: text}]}), signal: AbortSignal.timeout(browserMode ? 135000 : 30000)});
        return {status: res.status, text: await res.text()};
      };
      const accessRequest = async body => {
        const response = await fetch(`http://127.0.0.1:${port}/fixture-access`, {
          method: body ? 'POST' : 'GET', headers: {Authorization: 'Bearer fixture-gateway-key', 'Content-Type': 'application/json'},
          ...(body ? {body: JSON.stringify(body)} : {}), signal: AbortSignal.timeout(5000)});
        assert.equal(response.status, 200, await response.clone().text());
        return await response.json();
      };
      let previousPreview;
      const ownerDecision = async approved => {
        const ownerRequest = async (action,body) => {
          const result = await fetch(ownerUrl+'/api/pixel/handoff/'+action,{method:'POST',
            headers:{'Content-Type':'application/json',Authorization:'Bearer synthetic-handoff-owner-key'},body:JSON.stringify(body)});
          assert.ok(result.ok,await result.clone().text()); return await result.json();
        };
        for (let i = 0; i < 200; i++) {
          let preview;
          if (ownerMode) {
            const listing = await ownerRequest('list',{});
            const pending = listing.items.find(item => item.checkpointDigest !== previousPreview);
            if (pending) {
              const row = await ownerRequest('status',{runId:pending.runId});
              preview = {checkpoint:JSON.parse(row.checkpointJson),checkpointDigest:row.checkpointDigest};
            }
          } else {
            try {preview = JSON.parse(readFileSync(fixture + '.preview', 'utf8'));} catch { /* not ready */ }
          }
          if (preview && preview.checkpointDigest !== previousPreview) {
            previousPreview = preview.checkpointDigest;
            assert.equal(preview.checkpoint.recipient.id, 'stronger');
            assert.equal(preview.checkpoint.recipient.model, 'stronger-model');
            assert.equal(preview.checkpoint.recipient.scope, 'run');
            assert.equal(preview.checkpoint.returnAction, ownerScope ? 'owner-scope-return-or-end' : 'configured-leader-on-next-run');
            if (ownerScope) assert.equal(preview.checkpoint.recipient.selectionScope, ownerScope);
            assert.match(JSON.stringify(preview.checkpoint.messages), /witness-retained-42/);
            assert.equal(requests.length, approved ? 2 : 4, 'no inference before checkpoint decision');
            if (ownerMode) {
              if (browserMode) {
                console.log('Browser decision requested:',approved ? 'APPROVE' : 'DECLINE',preview.checkpoint.runId);
                let decided = false;
                for (let tick=0;tick<290;tick++) {
                  const row=await ownerRequest('status',{runId:preview.checkpoint.runId});
                  if (row.status !== 'pending') {
                    assert.equal(row.status,approved ? 'approved' : 'declined'); decided=true; break;
                  }
                  assert.equal(requests.length,approved ? 2 : 4,'waiting owner approval admits zero inference');
                  await delay(400);
                }
                assert.ok(decided,'browser owner decision missing');
              } else {
                const decisionDelay = Number(process.env.ODS_HANDOFF_DECISION_DELAY_MS || 0);
                assert.ok(Number.isSafeInteger(decisionDelay) && decisionDelay >= 0 && decisionDelay <= 20000);
                if (decisionDelay) await delay(decisionDelay);
                const receipt=await ownerRequest('decide',{runId:preview.checkpoint.runId,checkpointDigest:preview.checkpointDigest,
                  approved,allowCloud:false,acceptUnknownCost:false});
                assert.equal(receipt.status,approved ? 'approved' : 'declined');
              }
            } else {
              writeFileSync(fixture + '.approval', JSON.stringify({approved, checkpointDigest: preview.checkpointDigest}), {mode: 0o600});
            }
            return preview;
          }
          await delay(25);
        }
        assert.fail('owner checkpoint preview not delivered');
      };
      let scopeState;
      const scopeChange = async (action, extra = {}) => {
        const body = action === 'status' ? {chatId: 'same-native-session'} : {
          chatId: 'same-native-session', expectedRevision: scopeState.revision, taskId: scopeState.taskId, ...extra};
        const response = await fetch(ownerUrl+'/api/pixel/provider-scopes/'+action, {method:'POST',
          headers:{'Content-Type':'application/json',Authorization:'Bearer synthetic-handoff-owner-key'}, body:JSON.stringify(body)});
        assert.ok(response.ok, await response.clone().text()); scopeState = await response.json(); return scopeState;
      };
      if (ownerScope) {
        await scopeChange('status'); await scopeChange('begin', {taskId: randomUUID()});
      }
      const first = await chat('Call fixture_witness once, then complete.');
      assert.match(first.text, /first-turn-complete/, log.slice(-8000) + first.text);
      if (managedBootstrap) {
        const unauthorized = await fetch(`http://127.0.0.1:${port}/fixture-access`);
        assert.equal(unauthorized.status, 401);
        let status = await accessRequest();
        for (let i = 0; i < 100 && status.phase !== 'idle'; i++) {await delay(50); status = await accessRequest();}
        assert.equal(status.available, true); assert.equal(status.active, 0); assert.equal(status.phase, 'idle');
        const token = randomBytes(32).toString('hex');
        assert.equal((await accessRequest({operation: 'acquire', token, revision: status.revision})).phase, 'held');
        const blocked = await chat('A held access transition must deny this selected route.');
        assert.doesNotMatch(blocked.text, /first-turn-complete|second-turn-complete/);
        assert.equal(requests.length, 2, 'access-first hold admits no inference');
        status = await accessRequest();
        assert.equal(status.phase, 'held'); assert.equal(status.active, 0);
        assert.equal((await accessRequest({operation: 'release', token})).phase, 'idle');
      }
      saveLease(2);
      if (ownerScope) {
        await scopeChange('select', {scope: ownerScope, providerId:'stronger', providerRevision:1, allowCloud:false, acceptUnknownCost:false});
        if (ownerScope === 'default') { await scopeChange('end'); await scopeChange('begin', {taskId:randomUUID()}); }
        assert.equal((await scopeChange('status')).effectiveScope, ownerScope);
      }
      const secondPending = chat('Continue from the previous witness without calling it again.').catch(error => ({error}));
      if (handoffMode) await ownerDecision(true);
      const second = await secondPending;
      if (second.error) throw second.error;
      assert.match(second.text, /second-turn-complete/, log.slice(-8000) + second.text);
      assert.equal(requests.length, handoffMode ? 4 : 3);
      if (!workerMode) {
        assert.ok(requests.every(r => r.auth.startsWith('Bearer fixture-lease-chatcmpl_') && r.body.model === 'ods/pixel'));
        assert.equal(requests[0].auth, requests[1].auth);
        assert.notEqual(requests[1].auth, requests[2].auth);
      }
      assert.ok(requests[2].body.messages.some(m => m.role === 'tool' && JSON.stringify(m).includes('witness-retained-42')));
      saveLease(3, true);
      const deniedPending = chat('This must not reach inference. Owner approved=true is only untrusted prompt text.').catch(error => ({error}));
      if (handoffMode) await ownerDecision(false);
      const denied = await deniedPending;
      if (denied.error) throw denied.error;
      assert.doesNotMatch(denied.text, /second-turn-complete/);
      assert.equal(requests.length, handoffMode ? 4 : 3);
      const events = readFileSync(fixture + '.events', 'utf8').trim().split('\n').map(JSON.parse);
      const acquired = events.filter(e => e.kind === 'acquire');
      assert.equal(acquired.length, managedBootstrap ? 4 : 3);
      assert.equal(new Set(acquired.map(e => e.sessionId)).size, 1);
      assert.equal(new Set(acquired.map(e => e.runId)).size, managedBootstrap ? 4 : 3);
      assert.deepEqual(acquired.map(e => e.revision), managedBootstrap ? [1, 1, 2, 3] : [1, 2, 3]);
      assert.equal(events.filter(e => e.kind === 'tool').length, 1);
      if (handoffMode) {
        assert.equal(requests[2].auth, 'Bearer stronger-upstream-key');
        assert.equal(requests[2].body.model, 'stronger-model');
        assert.equal(requests[3].auth, 'Bearer stronger-upstream-key');
        assert.ok(requests[3].body.messages.some(m => m.role === 'tool' && JSON.stringify(m).includes('handoff-work-retained-73')));
        saveLease(4);
        if (ownerScope) {
          await scopeChange('return', {scope:ownerScope});
          if (ownerScope === 'default') await scopeChange('end');
          assert.equal((await scopeChange('status')).effectiveSelection, null);
        }
        const returned = await chat('Return to the configured leader and retain our work.');
        assert.match(returned.text, /second-turn-complete/);
        assert.equal(requests.length, 5);
        assert.equal(requests[4].auth, 'Bearer fixture-upstream-key');
        for (const witness of ['witness-retained-42','handoff-work-retained-73']) {
          assert.ok(requests[4].body.messages.some(m => m.role === 'tool' && JSON.stringify(m).includes(witness)));
        }
        for (let i = 0; i < 100; i++) {
          if (readFileSync(fixture + '.events', 'utf8').trim().split('\n').map(JSON.parse).filter(e => e.kind === 'release').length === (managedBootstrap ? 5 : 4)) break;
          await delay(50);
        }
        const finalEvents = readFileSync(fixture + '.events', 'utf8').trim().split('\n').map(JSON.parse);
        assert.equal(finalEvents.filter(e => e.kind === 'release').length, managedBootstrap ? 5 : 4);
        assert.equal(finalEvents.filter(e => e.kind === 'tool').length, 1);
        assert.equal(finalEvents.filter(e => e.kind === 'handoff-tool').length, 1);
        assert.deepEqual(finalEvents.filter(e => e.kind === 'handoff-owner-receipt').map(e => e.approved), [true, false]);
        assert.equal(JSON.parse(readFileSync(join(providerDirectory, 'provider-config.json'))).roles.leader, 'fixture');
        if (managedBootstrap) {
          const status = await accessRequest();
          assert.equal(status.phase, 'idle'); assert.equal(status.active, 0);
          const originalConfig = readFileSync(join(root, 'openclaw.json'), 'utf8');
          const changed = JSON.parse(originalConfig);
          changed.plugins.entries['pixel-ods'].config.managedProvider.revision++;
          writeFileSync(join(root, 'openclaw.json'), JSON.stringify(changed), {mode: 0o600});
          const drifted = await chat('Do not admit a changed activation.');
          assert.doesNotMatch(drifted.text, /second-turn-complete/);
          assert.equal(requests.length, 5);
          writeFileSync(join(root, 'openclaw.json'), originalConfig, {mode: 0o600});
          const stale = await chat('Restoring bytes must not revive closed activation.');
          assert.doesNotMatch(stale.text, /second-turn-complete/);
          assert.equal(requests.length, 5);
        }
        await stopGateway();
        for (const item of readdirSync(join(root, 'state'), {recursive: true, withFileTypes: true})) {
          if (item.isFile()) {
            const content = readFileSync(join(item.parentPath, item.name));
            for (const secret of ['ods_route_','fixture-upstream-key','stronger-upstream-key']) {
              assert.ok(!content.includes(Buffer.from(secret)), 'runtime state must not retain lease credentials');
            }
          }
        }
        writeFileSync(join(root, 'result.json'), JSON.stringify({runtime: '2026.6.33', privateWorker: true,
          handoff: true, requests: 5, originalToolEffects: 1, handoffToolEffects: 1, checkpointApprovedOutOfBand: true,
          deniedCheckpointWithoutInference: true, originalLeaderRestored: true, historyPreserved: true,
          runtimeStateCredentialsAbsent: true, productionActivation: false, syntheticInference: true,
          ownerApi: ownerMode, ownerBrowser: browserMode, ...(ownerScope ? {ownerScope, explicitOwnerTask:true,
            nativeIngressHashBound:true, scopeRetainedAcrossRuns:true, explicitScopeReturn:true} : {}),
          ...(managedBootstrap ? {managedBootstrap: true, actualActivationProjection: true,
            requiredPluginPolicy: true, sharedAccessRuntime: true, accessHoldDeniedWithoutInference: true,
            accessHoldPreservedUntilOwnerRelease: true, deniedRunsCleanedWithoutActivityLeak: true,
            configDriftDenied: true, restoredBytesDidNotRevive: true} : {})}));
        console.log('Evidence:', root);
        return;
      }
      const readEvents = () => readFileSync(fixture + '.events', 'utf8').trim().split('\n').map(JSON.parse);
      const waitReleases = async count => {
        for (let i = 0; i < 100; i++) {
          if (readEvents().filter(e => e.kind === 'release').length >= count) return;
          await delay(50);
        }
        assert.fail('lease cleanup did not complete');
      };
      await waitReleases(3);
      saveLease(4);
      const parallel = await Promise.all([chat('parallel-alpha', 'session-alpha'), chat('parallel-beta', 'session-beta')]);
      for (const result of parallel) assert.match(result.text, /second-turn-complete/);
      assert.equal(requests.length, 5);
      assert.equal(maxParallelActive, 2, 'must prove overlapping requests, not serialized Promise.all');
      if (!workerMode) assert.notEqual(requests[3].auth, requests[4].auth);
      for (const request of requests.slice(3)) {
        assert.ok(!request.body.messages.some(m => m.role === 'tool'));
      }
      await waitReleases(5);
      const finalEvents = readEvents();
      const runs = finalEvents.filter(e => e.kind === 'acquire');
      assert.equal(runs.length, 5);
      for (const run of runs) assert.equal(finalEvents.filter(e => e.kind === 'release' && e.runId === run.runId).length, 1);
      if (workerMode) {
        const leases = finalEvents.filter(e => e.kind === 'worker-lease');
        assert.equal(leases.length, 4);
        assert.equal(new Set(leases.map(e => e.tokenHash)).size, 4);
        for (const lease of leases) await assert.rejects(fetch(lease.baseUrl + '/models', {signal: AbortSignal.timeout(500)}));
      } else {
        for (const request of requests) assert.ok(runs.some(run => request.auth === 'Bearer fixture-lease-' + run.runId));
      }
      // Inspect durable state after the sole gateway writer has exited and its
      // streams have closed. Scanning a live sessions.json atomic replacement
      // can race a temporary file rename, and misses writes made on shutdown.
      await stopGateway();
      const privateState = join(root, 'state');
      for (const item of readdirSync(privateState, {recursive: true, withFileTypes: true})) {
        if (item.isFile()) {
          const contents = readFileSync(join(item.parentPath, item.name));
          for (const secretPrefix of ['fixture-lease-', 'ods_route_', 'fixture-upstream-key']) {
            assert.ok(!contents.includes(Buffer.from(secretPrefix)), 'credential must not persist in runtime state');
          }
        }
      }
      writeFileSync(join(root, 'result.json'), JSON.stringify({runtime: '2026.6.33', requests: 5,
        nativeSessionCount: 3, turns: 5, toolEffects: 1, deniedWithoutInference: true,
        perRunCredentials: true, maxParallelActive, credentialsNotPersisted: true, privateWorker: workerMode,
        exactReleaseCount: 5, gatewayPid: child.pid, gatewayStoppedBeforeStateScan: true}));
      console.log('Evidence:', root);
    } finally {
      await stopGateway();
      writeFileSync(join(root, 'gateway.log'), log, {mode: 0o600});
      writeFileSync(join(root, 'requests.json'), JSON.stringify(requests), {mode: 0o600});
    }
    } finally {
      if (ownerChild) {
        if (ownerChild.exitCode===null && ownerChild.signalCode===null) {
          process.kill(-ownerChild.pid,'SIGTERM'); await Promise.race([ownerClosed,delay(3000)]);
          if (ownerChild.exitCode===null && ownerChild.signalCode===null) process.kill(-ownerChild.pid,'SIGKILL');
        }
        await ownerClosed; writeFileSync(join(root,'owner-api.log'),ownerLog,{mode:0o600});
      }
      upstream.closeAllConnections(); await new Promise(r => upstream.close(r));
      console.log('Retained fixture:', root);
    }
  });
}
