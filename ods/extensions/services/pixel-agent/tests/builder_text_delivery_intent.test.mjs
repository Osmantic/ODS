import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createToolLoopGuard, userMessageRequestsWorkspaceDocumentDelivery as selects} from '../plugin/tool-loop-guard.mjs';

// The dashboard adds this role prose to the owner's native user message.
// It describes a text reply, not a request for a downloadable file.
const role = "Carry out the owner's requested work, using available tools when needed. For a writing or discussion request, deliver the requested text instead of making files. Verify the result. Do not merely promise to do it.";
test('fixture is the actual dashboard builder role', () => {
  assert.ok(readFileSync(new URL('../../dashboard-api/pixel_agent_teams.py', import.meta.url), 'utf8').includes(JSON.stringify(role)));
});

for (const request of [
  'Verify the existing preview with the browser inspector. Do not create files.',
  'Summarize the supplied notes. No files are requested.',
  'Inspect the project and report its status.',
]) test(`builder text instructions cannot invent a file handoff: ${request}`, () => {
  const prompt = `${request}\n\n${role}`;
  assert.equal(selects([], prompt), false);
  assert.equal(selects([{role:'user',content:prompt}]), false);
  const guard = createToolLoopGuard();
  const context = {trigger:'user',agentId:'pixel', runId:'read-only-builder', sessionId:'text-only-session', sessionKey:'agent:pixel:openai-user:ods-'+'b'.repeat(64)};
  guard.observeRun(context, 'pixel', {prompt}, {executionHost:'sandbox', workspaceRoot:'/workspace'});
  assert.doesNotMatch(guard.deliveryVerificationForRun(context.runId).text ?? '', /requested download was not attached/);
});

for (const request of [
  'Attach report.pdf.',
  'Create report.md. Attach it.',
  'Inspect the project and give me the project files.',
]) test(`a real handoff remains required beside builder text instructions: ${request}`, () => {
  const prompt = `${request}\n\n${role}`;
  assert.equal(selects([],prompt),true);
  const guard=createToolLoopGuard();
  const context={trigger:'user',agentId:'pixel',runId:'file-builder',sessionId:'file-session',sessionKey:'agent:pixel:openai-user:ods-'+'c'.repeat(64)};
  guard.observeRun(context,'pixel',{prompt},{executionHost:'sandbox',workspaceRoot:'/workspace'});
  assert.match(guard.deliveryVerificationForRun(context.runId).text,/requested download was not attached/);
});

test('text instead of files can coexist with an independent file attachment', () => {
  assert.equal(selects([], 'Deliver the requested text instead of making files and attach report.pdf.'),true);
  assert.equal(selects([], 'Deliver the requested text instead of making files.'),false);
  assert.equal(selects([], 'Deliver text instead of creating files.'),false);
});
