import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard, userMessageRequestsWorkspaceVerificationContinuation} from '../plugin/tool-loop-guard.mjs';

// A bug report describes what the page does; it does not forbid a preview. On
// the laptop (2026-10-30) "…so attacks never show" in an owner's bug report was
// read as "never show a preview", and publishing was refused three times in the
// turn that asked Portal to "check it in the preview this time". Publishing
// verbs still forbid wherever they are negated; showing, displaying, opening
// or viewing forbids only with an object aimed at Portal.
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

test('a bug report about what never shows still gets its preview', () => {
  for (const report of [
    OWNER_2026_10_30,
    'In demo, attacks never show.',
    'The hit ring never shows when you shoot them; enemies never attack and show no ring.',
    "In demo the score doesn't display, and the labels don't show up.",
    'In demo, the game restarts without showing the game-over screen.',
    'In demo, the page loads without displaying the score.',
    'In demo, the dialog closes without showing a confirmation.',
    'In demo, the hit rings flicker and never show.',
    'In demo, the labels load, then never display.',
    'In demo, enemies reset every frame, so never show their attack.',
    'In demo, enemies attack but don\'t show the ring.',
    'In demo the cards flip and avoid showing the back face.',
  ]) {
    const prompt = report === OWNER_2026_10_30 ? report : `${report} Fix it and check it in the preview.`;
    assert.equal(previewRefused(prompt), false, prompt);
  }
});

test('a prohibition still forbids the preview, however it is written', () => {
  for (const prompt of [
    'Do not publish the current files.', "Don't republish anything.", 'Never create and publish a website.',
    'Read the files without showing a preview.', 'Do not edit, publish, run or delete anything.',
    'Read demo/index.html and report its h1 text exactly. Do not edit files or publish anything.',
    'Create the source code only. Do not show a preview.', 'Create an SVG and do not show it in the browser.',
    'Create an interactive trainer but do not show it here.', 'Please fix the bug, and please never publish it.',
    'Fix it. You must not publish a preview.',
    // The review's phrasings that an earlier version let through.
    'Fix the hit ring in demo/index.html. Btw don\'t publish.', 'Fix the bug.\n- Don\'t publish.', 'Fix the bug.\n* Do not publish.',
    'Fix the bug. **Do not publish.**', 'Fix the bug.\n1) Don\'t publish.', 'Fix the bug - do not publish.', 'Fix the bug — don\'t publish it.',
    'Fix the bug. You must never publish it.', 'Fix the bug. Portal must not publish.', 'Fix the bug. Make sure you do not publish.',
    'Fix the bug. For now don\'t publish.', 'Fix the bug, but this time don\'t publish.', 'Fix the bug & don\'t publish.',
    'Fix the bug - do not edit other files or publish anything.', 'Create the page - do not create or publish a preview.',
    'Fix the bug. Don\'t show me the preview.', 'Fix the bug. Do not open it in the browser.',
  ]) assert.equal(previewRefused(prompt), true, prompt);
});

test('a prohibition still keeps a check of the existing page from republishing it', () => {
  for (const prompt of ['Check the existing site - do not republish it.', 'Inspect the current page. Btw don\'t publish.']) {
    assert.equal(userMessageRequestsWorkspaceVerificationContinuation([], prompt), false, prompt);
  }
});
