import test from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const api = await import(process.env.GUARD_MODULE
  ? pathToFileURL(process.env.GUARD_MODULE).href : '../plugin/tool-loop-guard.mjs');
const overview = ['host.cpu', 'host.gpu', 'host.memory', 'host.storage'];

for (const question of [
  'Can you tell me what kind of hardware this computer has?',
  'What are the specs of this laptop?',
  'List the hardware components in this machine.',
  'Show me the specifications of my computer.',
  'Describe the hardware of this system.',
]) {
  test(`general hardware question covers the basic overview: ${question}`, () => {
    const result = api.userMessageOperationsRequirements([], question);
    assert.equal(result.required, true);
    assert.deepEqual(result.actions, overview);
  });
}

for (const [question, expected] of [
  ['What CPU does this computer have?', ['host.cpu']],
  ['What GPU is in this laptop?', ['host.gpu']],
  ['How much RAM does this machine have?', ['host.memory']],
  ['What CPU and RAM does this computer have?', ['host.cpu', 'host.memory']],
  ['Tell me the hardware specs of this laptop, CPU and RAM only.', ['host.cpu', 'host.memory']],
]) {
  test(`explicit hardware facets stay bounded: ${question}`, () => {
    assert.deepEqual(api.userMessageOperationsRequirements([], question).actions, expected);
  });
}

for (const question of [
  "Don't tell me about the hardware on this computer.",
  "Write a tutorial on how to check this computer's hardware.",
  'Build a sample hardware specification page for this laptop.',
  'What hardware does the remote inference server have?',
]) {
  test(`non-observation question does not require a local inventory: ${question}`, () => {
    assert.equal(api.userMessageOperationsRequirements([], question).required, false);
  });
}

test('a basic overview respects an explicitly excluded GPU observation', () => {
  assert.deepEqual(api.userMessageOperationsRequirements([],
    'What hardware does this computer have? Do not inspect the GPU.').actions,
    ['host.cpu', 'host.memory', 'host.storage']);
});

for (const question of [
  'What hardware does this computer have? Do not inspect the CPU, GPU, memory or storage.',
  'What hardware does this computer have? Do not inspect the CPU. Do not inspect the GPU. Do not inspect memory. Do not inspect storage.',
]) {
  test(`excluding every basic facet does not require an impossible receipt: ${question}`, () => {
    assert.deepEqual(api.userMessageOperationsRequirements([], question), {
      required: false,
      actions: [],
    });
  });
}

test('general hardware scope retains explicit observation exclusions', () => {
  const guard = api.createToolLoopGuard();
  const ctx = {agentId:'pixel', runId:'basic-hardware', sessionId:'basic-hardware-session'};
  guard.observeRun(ctx, 'pixel', {prompt:'Can you tell me what kind of hardware this computer has? Do not inspect network routes, processes or system services.'});
  const call = actions => guard.beforeToolCall({toolName:'pixel_ods_host_observe',params:{actions}},
    {...ctx,toolName:'pixel_ods_host_observe'}, 'pixel');
  assert.notEqual(call(overview)?.block, true);
  for (const action of ['host.network-routes', 'host.network-addresses', 'host.processes', 'host.services']) {
    assert.equal(call([...overview, action])?.block, true, action);
  }
});
