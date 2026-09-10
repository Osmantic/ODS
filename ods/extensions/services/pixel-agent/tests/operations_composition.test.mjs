import test from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const api = await import(process.env.GUARD_MODULE
  ? pathToFileURL(process.env.GUARD_MODULE).href : '../plugin/tool-loop-guard.mjs');
const ctx = { agentId: 'pixel', runId: 'composition', sessionId: 'composition-session' };
function make(prompt = 'Check the ODS host hostname.') {
  const aborts = [];
  const guard = api.createToolLoopGuard({ abortRun: id => { aborts.push(id); return true; } });
  guard.observeRun(ctx, 'pixel', { prompt });
  return { guard, aborts };
}
function call(guard, name, params = {}) {
  return guard.beforeToolCall({ toolName: name, params, runId: ctx.runId }, { ...ctx, toolName: name }, 'pixel');
}
function receipt(guard, { target = 'ods-host', status = 'succeeded', output = 'test-host\n' } = {}) {
  const params = { actions: ['host.identity'] };
  call(guard, 'pixel_ods_host_observe', params);
  guard.afterToolCall({ toolName: 'pixel_ods_host_observe', params,
    result: { details: { jobId: 'ops-1234567890123-abcdef123456', status,
      waitTimedOut: false, steps: [{ stepId: 'observe-1', target,
        action: 'host.identity', exitCode: status === 'succeeded' ? 0 : 1,
        stdout: output, stderr: '', outputTruncated: { stdout: false, stderr: false }, riskSignals: [] }] } } },
    { ...ctx, toolName: 'pixel_ods_host_observe' }, 'pixel');
}
test('sandbox tools compose before host evidence without workspace verb recognition', () => {
  for (const wrapped of [false, true]) {
    for (const [name, params] of [['read', { path: 'report.txt' }], ['write', { path: 'report.txt', content: 'check' }],
      ['exec', { command: 'python3 --version' }], ['process', { action: 'list' }]]) {
      const { guard } = make();
      const result = wrapped ? call(guard, 'tool_call', { id: `openclaw:core:${name}`, args: params }) : call(guard, name, params);
      assert.notEqual(result?.block, true, `${wrapped}:${name}: ${result?.blockReason}`);
      assert.equal(guard.verificationForRun(ctx.runId).status, 'failed', 'sandbox evidence does not satisfy host work');
    }
  }
});
test('terminal host receipt allows sandbox continuation and natural final', () => {
  const { guard, aborts } = make();
  receipt(guard);
  assert.equal(guard.verificationForRun(ctx.runId).status, 'passed');
  assert.deepEqual(aborts, []);
  assert.equal(guard.deliveryVerificationForRun(ctx.runId).deliveryMode, 'append');
  assert.notEqual(call(guard, 'exec', { command: 'node --version' })?.block, true);
  const final = guard.replyPayloadSending({ runId: ctx.runId, kind: 'final', payload: { text: 'The sandbox check is complete.' } });
  assert.match(final.payload.text, /^The sandbox check is complete\./);
  assert.match(final.payload.text, /Hostname: `test-host`/);
  assert.match(final.payload.text, /does not establish completion/);
});
test('filesystem names never supply host intent', () => {
  for (const p of ['/workspace/laptop-package-probe-1608/.venv', 'C:\\workspace\\laptop-check\\.venv', '`/workspace/my laptop/.venv`']) {
    assert.equal(api.userMessageOperationsRequirements([], `Inspect ${p} and remove that partial directory.`).required, false, p);
  }
  assert.deepEqual(api.userMessageOperationsRequirements([], 'Check the ODS host hostname and save /workspace/laptop/report.txt.').actions, ['host.identity']);
});
test('read-only capability metadata does not grant permission for an action', () => {
  const { guard } = make('First list only currently configured Operations targets and allowed actions. Do not run any observation, host command or remote action.');
  assert.notEqual(call(guard, 'tool_call', { id: 'pixel_ops_inventory', args: {} })?.block, true);
  assert.equal(call(guard, 'pixel_ops_run', { target: 'ods-host', action: 'host.identity' })?.block, true);
});
test('wrong-target and failed broker receipts remain unverified and do not preserve a success claim', () => {
  for (const variant of [{ target: 'Tower1' }, { status: 'failed' }]) {
    const { guard } = make();
    receipt(guard, variant);
    assert.equal(guard.verificationForRun(ctx.runId).status, 'failed');
    assert.equal(guard.deliveryVerificationForRun(ctx.runId).deliveryMode, undefined);
    const final = guard.replyPayloadSending({ runId: ctx.runId, kind: 'final', payload: { text: 'Everything succeeded.' } });
    assert.doesNotMatch(final.payload.text, /Everything succeeded/);
  }
});
test('workspace deletion still requires owner authorization', () => {
  const { guard } = make();
  assert.equal(call(guard, 'exec', { command: 'rm -rf /workspace/unrequested' })?.block, true);
});
test('failed workspace verification is not hidden by successful host evidence', () => {
  const { guard } = make();
  receipt(guard);
  const params = { command: 'python3 -m unittest' };
  call(guard, 'exec', params);
  guard.afterToolCall({ toolName: 'exec', params, result: { details: { status: 'completed', exitCode: 1 }, content: [{ type: 'text', text: 'FAILED (failures=1)' }] } },
    { ...ctx, toolName: 'exec' }, 'pixel');
  assert.equal(guard.verificationForRun(ctx.runId).status, 'failed');
  assert.doesNotMatch(guard.replyPayloadSending({ runId: ctx.runId, kind: 'final', payload: { text: 'Everything succeeded.' } }).payload.text, /Everything succeeded/);
});
test('verified extension inventory retains workflow explanation and next step', () => {
  for (const malformed of [false, true]) {
    const { guard } = make('List the installed and enabled ODS extensions and identify their source. Explain what is missing for a PDF folder workflow and the next concrete setup step. Do not mutate anything.');
    const jobId = 'ops-1234567890123-abcdef123456';
    const params = { target: 'ods-host', action: 'ods.extensions.list' };
    call(guard, 'pixel_ops_run', params);
    guard.afterToolCall({ toolName: 'pixel_ops_run', params, result: { details: { jobId, status: 'submitted', kind: 'action' } } }, { ...ctx, toolName: 'pixel_ops_run' }, 'pixel');
    const inventory = { schemaVersion: 1, kind: 'ods-pixel-extension-inventory', outcome: 'succeeded',
      summary: { total: 1, installed: 0, enabled: 0, cliInstalled: 0, disabled: 0, stopped: 0, unhealthy: 0, installing: 0, settingUp: 0, error: 0, notInstalled: 1, incompatible: 0 },
      extensions: [{ id: 'crewai', name: 'CrewAI', category: 'agents', status: 'not_installed', source: 'library', installable: true }],
      boundary: 'Read-only live ODS extension inventory; it exposes only bounded status metadata and grants no installation, configuration, credential, Docker, or shell authority.' };
    const result = { details: { jobId, status: 'succeeded', waitTimedOut: false,
      steps: [{ stepId: 'step', target: 'ods-host', action: 'ods.extensions.list', exitCode: 0,
        stdout: malformed ? '{}' : JSON.stringify(inventory) + '\n', stderr: '', outputTruncated: { stdout: false, stderr: false }, riskSignals: [] }] } };
    guard.afterToolCall({ toolName: 'pixel_ops_job_wait', params: { jobId }, result }, { ...ctx, toolName: 'pixel_ops_job_wait' }, 'pixel');
    const text = 'A PDF folder workflow still needs a document ingestion component. Next choose and configure a suitable installed extension.';
    const final = guard.replyPayloadSending({ runId: ctx.runId, kind: 'final', payload: { text } });
    if (malformed) {
      assert.equal(guard.verificationForRun(ctx.runId).status, 'failed');
      assert.ok(!final.payload.text.includes(text));
    } else {
      assert.equal(guard.verificationForRun(ctx.runId).status, 'passed');
      assert.ok(final.payload.text.startsWith(text));
      assert.match(final.payload.text, /Catalog total: 1; installed: 0; enabled: 0/);
      assert.match(final.payload.text, /does not establish completion/);
    }
  }
});
