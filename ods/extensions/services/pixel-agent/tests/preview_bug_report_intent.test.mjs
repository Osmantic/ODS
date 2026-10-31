import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard, userMessageRequestsWorkspacePreview} from '../plugin/tool-loop-guard.mjs';

// A bug report describes what the page does; it does not forbid a preview. On
// the laptop (2026-10-30) "…so attacks never show" in an owner's bug report was
// read as "never show a preview", and publishing was refused three times in the
// turn that asked Portal to "check it in the preview this time". Only a
// negation addressed to Portal ("do not publish", "without a preview") forbids.
function previewRefused(prompt) {
  const guard = createToolLoopGuard();
  const context = {agentId: 'pixel', runId: 'r', sessionId: 's', sessionKey: 'agent:pixel:openai-user:t', trigger: 'user'};
  guard.observeRun(context, 'pixel', {prompt, messages: []});
  const read = {toolName: 'read', params: {path: 'demo/index.html'}};
  guard.beforeToolCall(read, {...context, toolCallId: 'r1'});
  guard.afterToolCall({...read, result: {content: [{type: 'text', text: '<!doctype html>'}]}}, {...context, toolCallId: 'r1'});
  const decision = guard.beforeToolCall({toolName: 'pixel_ods_workspace_preview', params: {relativeDirectory: 'demo'}},
    {...context, toolCallId: 'p1'});
  return Boolean(decision?.block && /explicitly prohibits/.test(decision.blockReason));
}

const OWNER_2026_10_30 = 'In demo, I tested the sprite cache and it isn\'t working yet. Every bake fails with "SPRITE_restore is not defined", ' +
  'so the cache stays empty. Worse, since the restore never runs, drawing an enemy changes the enemy: every enemy ends up facing right ' +
  'and its attack timer gets reset to 0 every frame, so attacks never show. Drawing should never change the enemy, so bake from a copy ' +
  'instead of the real one. Please fix those and check it in the preview this time: the cache should actually fill up, enemies should ' +
  'face both ways, and attacks and hit rings should show like before.';

test('a bug report that says something never shows still gets its preview', () => {
  for (const prompt of [
    OWNER_2026_10_30,
    'In demo, attacks never show. Fix it and check it in the preview.',
    'The hit ring never shows when you shoot them; enemies never attack and show no ring. Please fix and check it in the preview.',
    "In demo the score doesn't display, and the labels don't show up. Fix it and check it in the preview.",
  ]) assert.equal(previewRefused(prompt), false, prompt);
});

test('a prohibition addressed to Portal still forbids the preview', () => {
  for (const prompt of [
    'Do not publish the current files.', "Don't republish anything.", 'Never create and publish a website.',
    'Read the files without showing a preview.', 'Do not edit, publish, run or delete anything.',
    'Read demo/index.html and report its h1 text exactly. Do not edit files or publish anything.',
    'Create the source code only. Do not show a preview.', 'Create an SVG and do not show it in the browser.',
    'Create an interactive trainer but do not show it here.', 'Please fix the bug, and please never publish it.',
    'Fix it. You must not publish a preview.',
  ]) assert.equal(previewRefused(prompt), true, prompt);
});

test('asking to check the work in the preview asks for a preview', () => {
  for (const prompt of [OWNER_2026_10_30, 'Fix the score display and test it in the live preview.', 'Verify the fix in the preview.']) {
    assert.equal(userMessageRequestsWorkspacePreview([], prompt), true, prompt);
  }
  for (const prompt of ['Fix the score display. Do not check it in the preview.', 'Fix the CSV parser and run its tests.']) {
    assert.equal(userMessageRequestsWorkspacePreview([], prompt), false, prompt);
  }
});
