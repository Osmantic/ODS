import test from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const api = await import(process.env.GUARD_MODULE
  ? pathToFileURL(process.env.GUARD_MODULE).href : '../plugin/tool-loop-guard.mjs');

function guidance(name) {
  return `\n\n[ODS ${name} delivery requirement: Answer the owner's complete message above. If it asks for exact text, copy that full exact text. Do not answer with a generic acknowledgement. Do not output NO_REPLY.]` +
    `\n[ODS ${name} host inspection route: Generic sandbox commands and status projections cannot establish host facts. Use the visible tool_call Tool Search control for the deferred Operations tools. Call tool_call with id pixel_ops_inventory and args {} to select the matching read-only ods-host actions. After terminal host evidence, continue any separately required ODS projection or workspace step before answering. Do not use generic sandbox commands as host evidence.]`;
}

for (const name of ['Pixel', 'Portal']) {
  test(`${name} routing guidance does not add owner host or workspace requirements`, () => {
    for (const question of [
      'What can you tell me about this computer and about ODS',
      'Can you tell me what kind of hardware this computer has?',
      "That's cool, can you use those specs to research and tell me how much a laptop like this is worth / would cost?",
      'What can you do with search? Can you tell me the Eagles season schedule this year?',
    ]) {
      const prompt = question + guidance(name);
      assert.deepEqual(api.userMessageOperationsRequirements([], prompt), api.userMessageOperationsRequirements([], question), question);
      assert.equal(api.userMessageRequestsWorkspaceContinuation([], prompt), false, question);
      assert.equal(api.userMessageRequestsWorkspaceTools([], prompt), false, question);
    }
  });
  test(`${name} guidance preserves real owner routes and workspace work`, () => {
    const owner = 'Report the ODS host network routes and save /workspace/routes.txt.';
    const intent = api.userMessageOperationsRequirements([], owner + guidance(name));
    assert.deepEqual(intent, api.userMessageOperationsRequirements([], owner));
    assert.ok(intent.actions.includes('host.network-routes'));
    assert.equal(api.userMessageRequestsWorkspaceContinuation([], owner + guidance(name)), true);
  });
  test(`${name} guidance retains a required host receipt without a spurious workspace failure`, () => {
    const ctx = { agentId:'pixel', runId:`guidance-${name}`, sessionId:`guidance-session-${name}` };
    const guard = api.createToolLoopGuard();
    guard.observeRun(ctx, 'pixel', {prompt:'Check the ODS host hostname.' + guidance(name)});
    const params = {actions:['host.identity']};
    assert.notEqual(guard.beforeToolCall({toolName:'pixel_ods_host_observe',params}, {...ctx,toolName:'pixel_ods_host_observe'}, 'pixel')?.block, true);
    guard.afterToolCall({toolName:'pixel_ods_host_observe',params,result:{details:{jobId:'ops-1234567890123-abcdef123456',status:'succeeded',waitTimedOut:false,
      steps:[{stepId:'observe-1',target:'ods-host',action:'host.identity',exitCode:0,stdout:'test-host\n',stderr:'',outputTruncated:{stdout:false,stderr:false},riskSignals:[]}]}}}, {...ctx,toolName:'pixel_ods_host_observe'}, 'pixel');
    assert.equal(guard.verificationForRun(ctx.runId).status, 'passed');
    const final = guard.replyPayloadSending({runId:ctx.runId,kind:'final',payload:{text:'The host is test-host.'}});
    assert.match(final.payload.text, /Hostname: `test-host`/);
    assert.doesNotMatch(final.payload.text, /Workspace continuation|requested workspace artifact/);
  });
}
