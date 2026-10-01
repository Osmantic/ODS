import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

for (const wrapped of [false, true]) {
  test(`workspace-only host observation refusal explains scope, not malformed arguments (${wrapped})`, () => {
    const guard = createToolLoopGuard();
    const context = {agentId:'pixel', runId:'next-project', sessionId:'qa-next'};
    guard.observeRun(context, 'pixel', {
      prompt:'Create a Next.js project in the workspace. Install only its minimal dependencies through supported tools. Do not install code-server or change ODS protections.',
    }, {executionHost:'sandbox'});
    const args = {actions:['host.processes','host.services','host.listening-ports']};
    const event = wrapped
      ? {toolName:'tool_call',params:{id:'openclaw:pixel-ods:pixel_ods_host_observe',args}}
      : {toolName:'pixel_ods_host_observe',params:args};
    const result = guard.beforeToolCall(event, context);
    assert.equal(result?.block, true);
    assert.match(result.blockReason, /workspace-only/);
    assert.match(result.blockReason, /Changing the arguments does not grant host access/);
    assert.doesNotMatch(result.blockReason, /Check the tool argument schema|network peer/);
    assert.notEqual(guard.beforeToolCall({toolName:'read',params:{path:'package.json'}},context)?.block,true);
  });
}

test('explicit mixed host diagnostics retain typed read access without host command authority', () => {
  const guard = createToolLoopGuard();
  const context = {agentId:'pixel', runId:'mixed', sessionId:'qa-next'};
  guard.observeRun(context,'pixel',{prompt:'Inspect this computer CPU. Create a Next.js project in the workspace.'});
  assert.notEqual(guard.beforeToolCall({toolName:'pixel_ods_host_observe',params:{actions:['host.cpu']}},context)?.block,true);
  assert.equal(guard.beforeToolCall({toolName:'pixel_ods_host_command_propose',params:{command:'id'}},context)?.block,true);
});
