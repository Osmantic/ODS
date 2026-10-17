import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {createHash} from 'node:crypto';
import {createIngressServer} from '../host/pixel_ingress.mjs';
import {createToolLoopGuard, CODING_LOOP_ABORT_REASON} from '../plugin/tool-loop-guard.mjs';

function fixture(abortRun = () => true, options = {}, {prompt = 'Create tray_plan.py and run it for 48 and 49 seedlings.', capabilities} = {}) {
  const context = {agentId: 'pixel', runId: 'chatcmpl_3d1f6c0a-6b1e-4f7a-9a52-2e6f0c7d1a30', sessionId: 'coding-session', trigger: 'user',
    sessionKey: 'agent:pixel:openai-user:ods-' + 'a'.repeat(64)};
  const guard = createToolLoopGuard({abortRun, ...options});
  guard.observeRun(context, 'pixel', {prompt}, capabilities);
  let index = 0;
  const call = (toolName, params) => guard.beforeToolCall({toolName, params},
    {...context, toolName, toolCallId: 'call-' + ++index});
  const exhaust = (wrapped = false) => {
    const args = {path: 'Playground/tray-plan/tray_plan.py',
      edits: [{oldText: 'trays = 8', newText: 'trays = 8'}]};
    const invoke = () => wrapped ? call('tool_call', {id: 'edit', args}) : call('edit', args);
    assert.equal(invoke().block, true);
    assert.equal(invoke().block, true);
  };
  return {guard, context, call, exhaust};
}

function publishPreview(guard, context) {
  const content = '<!doctype html><title>Tray plan</title>';
  const write = {path: 'tray-plan/index.html', content};
  const invoke = (toolName, params, result, toolCallId) => {
    const ctx = {...context, toolName, toolCallId};
    assert.notEqual(guard.beforeToolCall({toolName, params, toolCallId}, ctx)?.block, true);
    guard.afterToolCall({toolName, params, result, toolCallId}, ctx);
  };
  invoke('write', write, {details: {status: 'completed'}}, 'write-preview');
  const length = (bytes, value) => {const b = Buffer.alloc(bytes); bytes === 4 ? b.writeUInt32BE(value) : b.writeBigUInt64BE(BigInt(value)); return b;};
  const sha256 = createHash('sha256').update(length(4, 10)).update('index.html').update(length(8, content.length)).update(content).digest('hex');
  const siteId = 'site-' + sha256.slice(0, 24);
  const receipt = {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'succeeded',
    relativeDirectory: 'tray-plan', port: 9437, siteId, files: 1, bytes: content.length, sha256,
    entryFile: 'index.html', entrySha256: createHash('sha256').update(content).digest('hex'),
    url: `http://${siteId}.localhost:9437/${siteId}/`, httpStatus: 200, readbackVerified: true,
    executable: false, overwritten: false};
  invoke('pixel_ods_workspace_preview', {relativeDirectory: 'tray-plan'}, {details: receipt}, 'publish-preview');
  const verified = guard.deliveryVerificationForRun(context.runId);
  assert.equal(verified.status, 'passed'); assert.ok(verified.text); assert.equal(verified.preview.url, receipt.url);
  return verified;
}

for (const wrapped of [false, true]) {
  test(`acknowledged coding-loop abort replaces the upstream timeout fallback: wrapped=${wrapped}`, () => {
    const aborts = [];
    const {guard, context, call, exhaust} = fixture(id => {aborts.push(id); return true;});
    // A script can catch its own NameError and exit zero. That receipt alone
    // must not certify the requested coding task or erase the guard's failure.
    const params = {command: 'python3 tray_plan.py', workdir: '/workspace/Playground/tray-plan'};
    call('exec', params);
    guard.afterToolCall({toolName: 'exec', params, result: {details: {exitCode: 0,
      aggregated: "Test case 1 failed: NameError - name 'trains_needed_1' is not defined"}}}, context);
    exhaust(wrapped);
    assert.deepEqual(guard.deliveryVerificationForRun(context.runId), {status: 'none'});
    assert.equal(call('read', {path: 'Playground/tray-plan/tray_plan.py'}).blockReason, CODING_LOOP_ABORT_REASON);
    guard.observeAgentEnd({success: false, messages: [{role: 'assistant', stopReason: 'aborted', content: []}]}, context);
    assert.deepEqual(aborts, ['coding-session']);
    const result = guard.deliveryVerificationForRun(context.runId);
    assert.equal(result.status, 'failed');
    assert.match(result.text, /stopped.*coding.*repeated/i);
    assert.match(result.text, /incomplete|did not complete/i);
    assert.match(result.text, /workspace.*preserved/i);
    assert.doesNotMatch(result.text, /timed out|completed successfully/i);
    assert.equal(guard.replyPayloadSending({runId: context.runId, kind: 'final',
      payload: {text: 'LLM request timed out.'}})?.payload?.text, result.text);
    assert.deepEqual(guard.deliveryVerificationForRun('another-run'), {status: 'none'});
  });
}

for (const outcome of ['false', 'throws']) {
  test(`unacknowledged coding abort keeps the real upstream failure: ${outcome}`, () => {
    const {guard, context, call, exhaust} = fixture(() => {
      if (outcome === 'throws') throw new Error('fixture abort failed');
      return false;
    });
    exhaust();
    assert.equal(call('read', {path: 'Playground/tray-plan/tray_plan.py'}).block, true);
    assert.deepEqual(guard.deliveryVerificationForRun(context.runId), {status: 'none'});
    assert.equal(guard.replyPayloadSending({runId: context.runId, kind: 'final',
      payload: {text: 'Actual provider failure'}}), undefined);
  });
}

test('a provider failure without a coding guard is not relabeled', () => {
  const {guard, context} = fixture();
  guard.observeAgentEnd({success: false, messages: [{role: 'assistant', stopReason: 'error', content: []}]}, context);
  assert.deepEqual(guard.deliveryVerificationForRun(context.runId), {status: 'none'});
  assert.equal(guard.replyPayloadSending({runId: context.runId, kind: 'final',
    payload: {text: 'LLM request timed out.'}}), undefined);
});

test('owner cancellation keeps its own delivery after a coding abort', async () => {
  const {guard, context, call, exhaust} = fixture(() => true,
    {abortRunAndDrain: async () => ({aborted: true, drained: true})});
  exhaust(); call('read', {path: 'Playground/tray-plan/tray_plan.py'});
  assert.equal(await guard.abortUserRun('ods-' + 'a'.repeat(64)), true);
  const result = guard.deliveryVerificationForRun(context.runId);
  assert.deepEqual(result, {status: 'none'});
  assert.equal(guard.replyPayloadSending({runId: context.runId, kind: 'final', payload: {text: ''}}), undefined);
});

test('a known verification failure remains the authoritative result', () => {
  const {guard, context, call, exhaust} = fixture();
  const ctx = {...context, toolName: 'exec', toolCallId: 'failed-test'};
  const params = {command: 'python3 -m unittest', workdir: '/workspace/Playground/tray-plan'};
  const before = guard.beforeToolCall({toolName: 'exec', params}, ctx);
  assert.notEqual(before?.block, true);
  guard.afterToolCall({toolName: 'exec', params: before?.params ?? params,
    result: {isError: true, details: {status: 'completed', exitCode: 1}}}, ctx);
  const failed = guard.deliveryVerificationForRun(context.runId);
  assert.equal(failed.status, 'failed'); assert.ok(failed.text);
  exhaust(); call('read', {path: 'Playground/tray-plan/tray_plan.py'});
  assert.deepEqual(guard.deliveryVerificationForRun(context.runId), failed);
});

test('a bare successful verification cannot certify an aborted coding task', () => {
  const {guard, context, call, exhaust} = fixture();
  const ctx = {...context, toolName: 'exec', toolCallId: 'passed-test'};
  const params = {command: 'python3 -m unittest', workdir: '/workspace/Playground/tray-plan'};
  const before = guard.beforeToolCall({toolName: 'exec', params}, ctx);
  assert.notEqual(before?.block, true);
  guard.afterToolCall({toolName: 'exec', params: before?.params ?? params,
    result: {details: {status: 'completed', exitCode: 0}}}, ctx);
  assert.equal(guard.deliveryVerificationForRun(context.runId).status, 'passed');
  assert.equal(guard.deliveryVerificationForRun(context.runId).text, undefined);
  exhaust(); call('read', {path: 'Playground/tray-plan/tray_plan.py'});
  const result = guard.deliveryVerificationForRun(context.runId);
  assert.equal(result.status, 'failed');
  assert.match(result.text, /work is incomplete/);
});

test('an in-flight owner question keeps pending precedence over a coding abort', () => {
  const {guard, context, call, exhaust} = fixture();
  const questions = [{id: 'choice', question: 'Which output should I keep?', options: ['Text', 'File']}];
  const ctx = {...context, toolName: 'pixel_ods_ask_user', toolCallId: 'question'};
  assert.notEqual(guard.beforeToolCall({toolName: ctx.toolName, params: {questions}}, ctx)?.block, true);
  exhaust(); call('read', {path: 'Playground/tray-plan/tray_plan.py'});
  guard.afterToolCall({toolName: ctx.toolName, result: {details: {status: 'awaiting_user', questions}}}, ctx);
  const result = guard.deliveryVerificationForRun(context.runId);
  assert.equal(result.status, 'pending'); assert.deepEqual(result.questions, questions);
  assert.doesNotMatch(result.text, /repeated tool attempts/);
});

test('a real deadline after an unacknowledged coding abort remains a deadline', () => {
  let clock = 0;
  const {guard, context, call, exhaust} = fixture(() => false, {now: () => clock},
    {capabilities: {runTimeoutSeconds: 1800}});
  exhaust(); call('read', {path: 'Playground/tray-plan/tray_plan.py'});
  clock = 1800000;
  guard.observeAgentEnd({success: false, messages: [{role: 'assistant', stopReason: 'aborted', content: []}]}, context);
  const result = guard.deliveryVerificationForRun(context.runId);
  assert.equal(result.status, 'failed'); assert.match(result.text, /after 30 minutes/);
  assert.doesNotMatch(result.text, /repeated tool attempts/);
});

for (const stream of [false, true]) for (const published of [false, true]) {
  test(`coding stop reaches Portal through the real ingress: stream=${stream}, published=${published}`, async t => {
    const {guard, context, call, exhaust} = fixture(undefined, {}, published ?
      {prompt: 'Build and publish a website at /workspace/tray-plan/index.html, then finish the requested code change.'} : {});
    const previous = published ? publishPreview(guard, context) : undefined;
    const gateway = http.createServer(async (req, res) => {
      let raw = ''; for await (const chunk of req) raw += chunk;
      const request = raw ? JSON.parse(raw) : {};
      let result;
      switch (req.url) {
        case '/health': result = {ok: true}; break;
        case '/v1/chat/completions':
          exhaust(); call('read', {path: 'Playground/tray-plan/tray_plan.py'});
          guard.observeAgentEnd({success: false, messages: [{role: 'assistant', stopReason: 'aborted', content: []}]}, context);
          result = {id: context.runId, choices: [{index: 0, message: {role: 'assistant', content: 'LLM request timed out.'}, finish_reason: 'stop'}]}; break;
        case '/pixel-ods/verification': result = guard.deliveryVerificationForRun(request.runId); break;
        case '/pixel-ods/subagent-delivery': result = {schemaVersion: 1, kind: 'ods-subagent-delivery', runId: request.runId, status: 'not-delegated'}; break;
        case '/pixel-ods/read-only-extension-continuation': result = {schemaVersion: 1, kind: 'ods-extension-read-only-continuation', eligible: false}; break;
        case '/pixel-ods/unfinished-extension-decision': result = {schemaVersion: 1, kind: 'ods-extension-unfinished-decision', eligible: false}; break;
        case '/pixel-ods/activity': result = {task: null}; break;
        default: res.writeHead(404); res.end(); return;
      }
      res.writeHead(200, {'content-type': 'application/json'}); res.end(JSON.stringify(result));
    });
    const listen = server => new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(server.address().port)));
    const ingress = createIngressServer({token: 'fixture-only-token-0123456789abcdef', gatewayPort: await listen(gateway)});
    const port = await listen(ingress);
    t.after(async () => {for (const server of [ingress, gateway]) {server.closeAllConnections(); await new Promise(resolve => server.close(resolve));}});
    const response = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {method: 'POST',
      headers: {'content-type': 'application/json', connection: 'close'}, signal: AbortSignal.timeout(5000),
      body: JSON.stringify({user: 'fixture-coding-loop', stream, messages: [{role: 'user', content: 'Create tray_plan.py and run it.'}]})});
    const body = await response.text(); assert.equal(response.status, 200, body);
    const frames = stream ? body.split(/\r?\n/).filter(line => line.startsWith('data: {')).map(line => JSON.parse(line.slice(6))) : [];
    const delivered = stream ? frames.map(frame => frame.choices?.[0]?.delta?.content ?? '').join('') : JSON.parse(body).choices[0].message.content;
    assert.equal(delivered, guard.deliveryVerificationForRun(context.runId).text);
    assert.match(delivered, /work is incomplete/);
    if (published) {
      assert.ok(delivered.includes(previous.text));
      assert.deepEqual(guard.deliveryVerificationForRun(context.runId).preview, previous.preview);
    }
    assert.doesNotMatch(body, /LLM request timed out/);
    if (stream) assert.equal(frames.at(-1).pixel_outcome.status, 'failed');
  });
}
