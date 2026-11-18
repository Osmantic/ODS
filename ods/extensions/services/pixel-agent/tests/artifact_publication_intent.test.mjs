import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard, userMessageRequestsWorkspaceDocumentDelivery as selectsDelivery} from '../plugin/tool-loop-guard.mjs';

const actual = 'The corrected code and archive layout now pass all 16 supplied tests plus six independent checks. Finish the README cleanup only: the example total is 109.97, not 89.99; remove the duplicate Running Tests section containing python and retain one python3 command. Keep code and tests unchanged. Re-publish the project with the sourcePath artifact tool, and give a concise final response.';
const positives = [actual,
  'Publish project.zip with the artifact tool.',
  'Please republish the archive using the workspace artifact tool.',
  'Could you re-publish report.csv via the artifact publication tool?',
  'Publish the project with the pixel_ods_workspace_artifact tool.',
  'Publish new.zip with the artifact tool. Do not republish old.zip.',
];
const negatives = [
  'The project was already published with the artifact tool.',
  'How do I publish project.zip with the artifact tool?',
  'Explain how to republish the project with the sourcePath artifact tool.',
  'The docs say "Publish project.zip with the artifact tool".',
  '> Publish project.zip with the artifact tool.',
  '```\nPublish project.zip with the artifact tool.\n```',
  'Do not republish project.zip with the artifact tool.',
  'Please do not publish project.zip with the artifact tool.',
  'Do not use tools. Publish project.zip with the artifact tool.',
  'Publish the website with the artifact tool.',
  'Publish the blog page with the artifact tool.',
  'Publish the project to example.com.',
  'Ask Alice to publish project.zip with the artifact tool.',
  'Publish new.zip with the artifact tool. Actually, do not republish anything.',
  'Publish new.zip with the artifact tool. Do not publish new.zip.',
  'Publish the project with the artifact tool. Never republish the project.',
  'The sourcePath example is `Publish project.zip with the artifact tool`.',
];
test('direct artifact publication requests select verified file delivery', () => {
  for (const prompt of positives) assert.equal(selectsDelivery([], prompt), true, prompt);
});
test('explanations, quoted requests, withdrawals and website publication do not select file delivery', () => {
  for (const prompt of negatives) assert.equal(selectsDelivery([], prompt), false, prompt);
});
test('the actual no-tool completion claim gets one artifact repair and cannot count as delivered', () => {
  const guard=createToolLoopGuard();
  const context={agentId:'pixel',runId:'publish',sessionId:'session',sessionKey:'agent:pixel:openai-user:ods-'+'d'.repeat(64),trigger:'user'};
  guard.observeRun(context,'pixel',{prompt:actual});
  const event={runId:context.runId,lastAssistantMessage:'README cleaned and ZIP republished.',messages:[{role:'assistant',content:[{type:'text',text:'README cleaned and ZIP republished.'}]}]};
  const revision=guard.beforeAgentFinalize(event,context);
  assert.equal(revision?.retry?.idempotencyKey,'pixel-ods-workspace-document-delivery');
  assert.equal(revision.retry.maxAttempts,1);
  assert.match(revision.retry.instruction,/pixel_ods_workspace_artifact/);
  assert.notEqual(guard.beforeAgentFinalize(event,context)?.retry?.idempotencyKey,revision.retry.idempotencyKey);
  assert.equal(guard.deliveryVerificationForRun('publish').status,'failed');
  assert.equal(guard.deliveryVerificationForRun('publish').artifacts,undefined);
});
