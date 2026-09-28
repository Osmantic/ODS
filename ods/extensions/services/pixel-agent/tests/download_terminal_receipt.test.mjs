import test from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const api = await import(process.env.GUARD_MODULE
  ? pathToFileURL(process.env.GUARD_MODULE).href : '../plugin/tool-loop-guard.mjs');

const jobId = 'ops-1234567890123-abcdef123456';
const url = 'https://nodejs.org/dist/v22.23.2/node-v22.23.2-linux-x64.tar.xz';
const filename = 'node-v22.23.2-linux-x64.tar.xz';
const sha256 = 'a'.repeat(64);
function harness(wrapped, expectedDigest = true) {
  const guard = api.createToolLoopGuard();
  const ctx = {agentId: 'pixel', runId: 'artifact-terminal', sessionId: 'artifact-session'};
  guard.observeRun(ctx, 'pixel', {prompt: `Use Operations to stage ${url} and run the local Node tests.`});
  function finish(name, args, result) {
    const toolName = wrapped ? 'tool_call' : name;
    const id = `openclaw:pixel-operations-broker:${name}`;
    const params = wrapped ? {id, args} : args;
    const decision = guard.beforeToolCall({toolName, params}, {...ctx, toolName}, 'pixel');
    assert.notEqual(decision?.block, true, decision?.blockReason);
    if (wrapped) {
      // Tool Search executes the selected tool's own before/after hooks.
      const inner = guard.beforeToolCall({toolName: name, params: args}, {...ctx, toolName: name}, 'pixel');
      assert.notEqual(inner?.block, true, inner?.blockReason);
      guard.afterToolCall({toolName: name, params: args, result}, {...ctx, toolName: name}, 'pixel');
    }
    guard.afterToolCall({toolName, params, result: wrapped
      ? {details: {tool: {id, source: 'openclaw', sourceName: 'pixel-operations-broker', name}, result}} : result},
      {...ctx, toolName}, 'pixel');
  }
  finish('pixel_ops_download_stage', {url, filename, ...(expectedDigest ? {expectedSha256: sha256} : {})},
    {details: {jobId, status: 'submitted', kind: 'download'}});
  function complete(mutate = () => {}) {
    const details = {jobId, status: 'succeeded', waitTimedOut: false, steps: [{
      target: 'broker', action: 'download.stage', exitCode: 0,
      artifact: {path: `/var/lib/pixel-ops-broker/artifacts/${jobId}/${filename}`,
        filename, bytes: 1024, sha256, source: url, redirects: [],
        expectedSha256Matched: true, executable: false},
    }]};
    mutate(details);
    finish('pixel_ops_job_wait', {jobId}, {details});
    const params = {command: 'node --test'};
    guard.beforeToolCall({toolName: 'exec', params}, {...ctx, toolName: 'exec'}, 'pixel');
    guard.afterToolCall({toolName: 'exec', params, result: {details: {status: 'completed', exitCode: 0},
      content: [{type: 'text', text: '# tests 6\n# pass 6\n# fail 0\n'}]}}, {...ctx, toolName: 'exec'}, 'pixel');
    return guard.verificationForRun(ctx.runId);
  }
  function deliver() {
    const text = 'The local Node tests completed successfully.';
    return guard.replyPayloadSending({runId: ctx.runId, kind: 'final', payload: {text}})?.payload?.text ?? text;
  }
  return {complete, deliver};
}

for (const wrapped of [false, true]) {
  test(`canonical receipt also completes without an owner-supplied digest: ${wrapped}`, () => {
    const h = harness(wrapped, false);
    assert.notEqual(h.complete().status, 'failed');
    assert.match(h.deliver(), /local Node tests completed successfully/);
  });
  test(`canonical artifact receipt without shell fields completes delivery: ${wrapped}`, () => {
    const h = harness(wrapped);
    assert.notEqual(h.complete().status, 'failed');
    assert.match(h.deliver(), /local Node tests completed successfully/);
    assert.doesNotMatch(h.deliver(), /did not obtain a matching terminal broker result/);
  });
  for (const [name, mutate] of [
    ['wrong job', d => {d.jobId = 'ops-1234567890124-abcdef123456';}],
    ['wrong target', d => {d.steps[0].target = 'ods-host';}],
    ['wrong action', d => {d.steps[0].action = 'raw-shell';}],
    ['wrong path', d => {d.steps[0].artifact.path = '/tmp/unbound.tar.xz';}],
    ['wrong filename', d => {d.steps[0].artifact.filename = 'other.tar.xz';}],
    ['wrong source', d => {d.steps[0].artifact.source = 'https://example.invalid/other';}],
    ['wrong redirect binding', d => {d.steps[0].artifact.redirects = ['https://example.invalid/other'];}],
    ['wrong sha256', d => {d.steps[0].artifact.sha256 = 'b'.repeat(64);}],
    ['missing hash match', d => {delete d.steps[0].artifact.expectedSha256Matched;}],
    ['negative bytes', d => {d.steps[0].artifact.bytes = -1;}],
    ['fractional bytes', d => {d.steps[0].artifact.bytes = 1.5;}],
    ['executable artifact', d => {d.steps[0].artifact.executable = true;}],
    ['extra step', d => {d.steps.push(structuredClone(d.steps[0]));}],
    ['timed out wait', d => {d.waitTimedOut = true;}],
    ['nonterminal receipt', d => {d.status = 'submitted';}],
  ]) {
    test(`artifact mismatch remains unverified: ${wrapped}/${name}`, () => {
      const h = harness(wrapped);
      assert.equal(h.complete(mutate).status, 'failed');
      assert.doesNotMatch(h.deliver(), /local Node tests completed successfully/);
    });
  }
}
