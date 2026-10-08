import test from 'node:test';
import assert from 'node:assert/strict';
import {workspaceReadOnlyCall} from '../plugin/preview-revalidation.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {displayForActivity} from '../plugin/activity-display.mjs';
import {registeredPixelTools} from './tool-grammar-registration.mjs';

test('real discovery registers the Laya schema without requiring a running service', async () => {
  const tools = await registeredPixelTools();
  const found = tools.filter(tool => tool.name === 'pixel_ods_laya');
  assert.equal(found.length, 1);
  assert.deepEqual(found[0].parameters.required, ['items', 'questions']);
});

test('classification neither invalidates workspace bytes nor grants a verification receipt', () => {
  assert.equal(workspaceReadOnlyCall('pixel_ods_laya', {}), true);
  assert.equal(workspaceReadOnlyCall('tool_call', {id: 'openclaw:pixel-ods:pixel_ods_laya', args: {}}), true);
  const guard = createToolLoopGuard();
  const context = {agentId: 'pixel', runId: 'laya-review', sessionId: 'laya-review'};
  guard.observeRun(context, 'pixel', {prompt: "You are the Reviewer in the owner's Portal team. Classify these supplied findings."});
  for (const event of [{toolName: 'pixel_ods_laya', params: {}},
    {toolName: 'tool_call', params: {id: 'openclaw:pixel-ods:pixel_ods_laya', args: {}}}]) {
    assert.notEqual(guard.beforeToolCall(event, context)?.block, true);
  }
  assert.notEqual(guard.verificationStatus('laya-review'), 'passed');
  assert.equal(guard.beforeToolCall({toolName: 'write', params: {path: 'file', content: 'x'}}, context).block, true);
});

test('activity shows a short label without the private classification input or answer', () => {
  for (const wrapped of [false, true]) {
    const args = {items: [{id: 'a', text: 'private ticket'}], questions: [{instructions: 'private criterion'}]};
    const context = {toolName: wrapped ? 'tool_call' : 'pixel_ods_laya'};
    const params = wrapped ? {id: 'openclaw:pixel-ods:pixel_ods_laya', args} : args;
    const display = displayForActivity({params, result: {content: [{type: 'text', text: 'private answer'}]}}, context);
    assert.equal(display.label, 'Consulting Laya');
    assert.equal(display.detail, null);
    assert.doesNotMatch(JSON.stringify(display), /private/);
  }
});
