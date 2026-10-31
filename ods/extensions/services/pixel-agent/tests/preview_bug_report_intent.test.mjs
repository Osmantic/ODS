import test from 'node:test';
import assert from 'node:assert/strict';
import {
  createToolLoopGuard, userMessageRequestsWorkspacePreview, userMessageRequestsWorkspaceVerificationContinuation,
} from '../plugin/tool-loop-guard.mjs';

// A bug report describes what the page does; it does not forbid a preview. On
// the laptop (2026-10-30) "…so attacks never show" in an owner's bug report was
// read as "never show a preview", and publishing was refused three times in the
// turn that asked Portal to "check it in the preview this time". A negation with
// a subject of its own ("attacks never show", "the page loads without showing
// the score") describes the page. An instruction to Portal has none, so it
// still forbids exactly as before.
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
    "In demo the labels load but don't show up.",
    'In demo, the game restarts without showing the game-over screen.',
    'In demo, the page loads without displaying the score.',
    'In demo, the dialog closes without showing a confirmation.',
    'In demo, the hit rings flicker and never show.',
    'In demo, the labels load, then never display.',
    'In demo, when I shoot, they never show.',
    // The review's phrasings that an earlier version still refused.
    "In demo, the sprites don't show in the browser.", 'In demo, hit rings never show here, only in the editor.',
    "In demo, the labels don't show up here.", "In demo, the buttons don't show anything.",
    "In demo, the nav links don't open the page.", "In demo, the menu items don't respond or open anything.",
    'In demo, the thumbnails never open the preview.', 'In demo, the HUD should never show it during cutscenes.',
    'In demo, the form submits without showing the result.',
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
    'Fix the hit ring in demo/index.html. Btw don\'t publish.', 'Fix the bug.\n- Don\'t publish.', 'Fix the bug.\n* Do not publish.',
    'Fix the bug. **Do not publish.**', 'Fix the bug.\n1) Don\'t publish.', 'Fix the bug - do not publish.', 'Fix the bug — don\'t publish it.',
    'Fix the bug. You must never publish it.', 'Fix the bug. Portal must not publish.', 'Fix the bug. Make sure you do not publish.',
    'Fix the bug. For now don\'t publish.', 'Fix the bug, but this time don\'t publish.', 'Fix the bug & don\'t publish.',
    'Fix the bug - do not edit other files or publish anything.', 'Create the page - do not create or publish a preview.',
    'Fix the bug. Don\'t show me the preview.', 'Fix the bug. Do not open it in the browser.',
    // Showing, opening, viewing and displaying forbid with any object or none.
    "Don't show any preview.", 'Do not show previews.', 'Never display previews.', 'Skip showing previews.',
    'Fix the bug without showing any preview.', 'Do not open a live preview.', "Don't show the updated page.",
    "Don't show me until it's done.", "Build it but don't show me until it's done.", "Don't show the game yet.",
    'Do not open the game.', "Don't display the output.", "Fix the bug, don't show/publish yet.",
    "Don't show, just save the files.", 'Do not edit other files or show the output.', "Don't build or open the landing page.",
    // Words that can stand right before a negation without being its subject.
    'Create the page and never show it.', "Create the page but don't show it to me.", "Fix it, but this time don't show it.",
    "The next time don't show me the preview.", "If the tests fail don't show me the preview.", "If it works don't show it yet.",
    'The agent must never show the preview.', 'Portal should not open the preview.', "Pls don't show it yet.",
    'Clients must not view the preview.', 'The previews must never open.', 'They must not open the preview.',
  ]) assert.equal(previewRefused(prompt), true, prompt);
});

test('a publishing verb is never read as a description', () => {
  // An app's own publishing feature reads like a prohibition; it is kept as one.
  assert.equal(previewRefused('In demo, scheduled posts never publish. Fix it and check it in the preview.'), true);
});

test('a bug description does not weaken a publication request', () => {
  for (const prompt of ['In demo, the score resets every frame. Fix it and publish demo/index.html.',
    'In demo, attacks never show. Fix it and publish demo/index.html.']) {
    assert.equal(userMessageRequestsWorkspacePreview([], prompt), true, prompt);
  }
});

test('a prohibition still keeps a check of the existing page from republishing it', () => {
  for (const prompt of ['Check the existing site - do not republish it.', 'Inspect the current page. Btw don\'t publish.',
    "Check the existing page, but don't open a live preview.", "Verify the current site, but don't show me until it works."]) {
    assert.equal(userMessageRequestsWorkspaceVerificationContinuation([], prompt), false, prompt);
  }
});
