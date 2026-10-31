import test from 'node:test';
import assert from 'node:assert/strict';
import {
  createToolLoopGuard, userMessageRequestsWorkspacePreview, userMessageRequestsWorkspaceVerificationContinuation,
} from '../plugin/tool-loop-guard.mjs';

// A bug report describes what the page does; it does not forbid a preview. On
// the laptop (2026-10-30) "…so attacks never show" in an owner's bug report was
// read as "never show a preview", and publishing was refused three times in the
// turn that asked Portal to "check it in the preview this time". An explicit
// request to use the preview, in a clause with no negation, now outweighs a
// negated show, open, view or display elsewhere. It never outweighs a negation
// that reaches a publishing word, and without such a request every negation
// forbids as before.
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

test('a bug report gets its preview when the owner asks to check it there', () => {
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
    "In demo, enemies attack but don't show the ring.",
    'In demo the cards flip and avoid showing the back face.',
    "In demo, the sprites don't show in the browser.", 'In demo, hit rings never show here, only in the editor.',
    "In demo, the buttons don't show anything.", "In demo, the nav links don't open the page.",
    "In demo, the menu items don't respond or open anything.", 'In demo, the HUD should never show it during cutscenes.',
    'In demo, clicking Save closes the dialog without showing a confirmation.',
    "In demo, the labels don't show after the server restarts.",
  ]) {
    const prompt = report === OWNER_2026_10_30 ? report : `${report} Fix it and check it in the preview.`;
    assert.equal(previewRefused(prompt), false, prompt);
  }
  for (const request of ['Please fix those and test it in the preview this time.', 'Can you verify it in the preview?',
    'Fix it, then publish demo/index.html.', 'Fix it and check it in the preview when you are done.']) {
    assert.equal(previewRefused(`In demo, attacks never show. ${request}`), false, request);
  }
  // A later constraint on the code is not a later "don't show".
  for (const constraint of ["Don't change how the menu opens.", "Don't change the display font.", 'Never remove the show-more button.']) {
    assert.equal(previewRefused(`In demo, attacks never show. Fix it and check it in the preview. ${constraint}`), false, constraint);
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
    "Don't show any preview.", 'Do not show previews.', 'Never display previews.', 'Skip showing previews.',
    'Fix the bug without showing any preview.', 'Do not open a live preview.', "Don't show the updated page.",
    "Don't show me until it's done.", "Build it but don't show me until it's done.", "Don't show the game yet.",
    'Do not open the game.', "Don't display the output.", "Fix the bug, don't show/publish yet.",
    "Don't show, just save the files.", 'Do not edit other files or show the output.', "Don't build or open the landing page.",
    'Make a snake game in demo/index.html, but don\'t show the game yet.', 'Create the page and never show it.',
    // The review's phrasings that hid a prohibition behind a seeming subject.
    "Whatever happens don't show it to anyone.", "Fix the menu don't show it yet.", "Till the tests pass don't show me the preview.",
    "Even if it works don't show it yet.", "On the laptop don't open the preview.", "Fix the menu don't show or publish it yet.",
    'Create a landing page and never show, publish or deploy it.', "All right don't show it yet.", 'My boss must not view the preview.',
    // A request in the same sentence as the negation does not outweigh it.
    "Don't show it to anyone and check it in the preview.", "Don't show the menu and open the existing site.",
    'Check it in the preview and don\'t show it to anyone.',
    // A later negation is the owner's later word.
    "Fix demo/index.html and check it in the preview. Actually, don't show it yet.",
    'Fix it and test it in the preview. On second thought, don\'t show it until I say so.',
    'Publish demo/index.html. Wait, no, don\'t show it yet.', 'Show me the existing site. Never mind, don\'t open it.',
    'In demo, attacks never show. Fix it and check it in the preview. Don\'t touch the layout or show it to anyone yet.',
    // Statements, questions, quotes, future requests and steps to reproduce are not requests.
    "I'll just check it in the preview myself. Don't show it.", 'I also test it in the preview myself. Don\'t show it to me.',
    'Should I just check it in the preview? Don\'t show it yet.', 'The docs say: check it in the preview. Don\'t show it yet.',
    "Don't show it yet. My teammate keeps saying “just check it in the preview”, ignore that.",
    "Fix demo/index.html. Don't show it yet. When I say go, then check it in the preview.",
    "Steps:\n1. Open the existing site.\n2. Click Start.\nFix it. Don't open the page until it works.",
    "Don't open the page until it works. Steps:\n1. Open the existing site.\n2. Click Start.",
    "Don't show it yet. I'll just check it in the preview myself.",
    "Don't show it yet. I'll test it myself, e.g. check it in the preview.",
    // A request put off until the owner acts, or to a later time, is not a request now.
    "Fix demo/index.html. Don't show it yet. Check it in the preview later.",
    "Fix demo/index.html. Don't show it yet. Check it in the preview after I've reviewed the code.",
    "Fix demo/index.html. Don't open it yet. Check it in the preview once I give the OK.",
    "Fix demo/index.html. Don't show it yet. Test it in the preview when I say go.",
    "Don't show it yet. Publish demo/index.html after the client signs off.", "Don't show it yet. Publish demo/index.html tomorrow.",
  ]) assert.equal(previewRefused(prompt), true, prompt);
});

test('a request to use the preview never outweighs a publishing prohibition', () => {
  for (const prompt of [
    "Check it in the preview. Don't publish.", "Fix it and check it in the preview, but don't show me the preview.",
    "Fix the menu and check it in the preview. Don't show or publish it yet.", 'Never show or deploy it. Check it in the preview.',
    // An owner who will look personally is not asking Portal to.
    "I'll check it in the preview myself. Don't show it.",
    // The app's own publishing feature reads like a prohibition; it is kept as one.
    'In demo, scheduled posts never publish. Fix it and check it in the preview.',
  ]) assert.equal(previewRefused(prompt), true, prompt);
});

test('a bug description does not weaken a publication request', () => {
  for (const prompt of ['In demo, the score resets every frame. Fix it and publish demo/index.html.',
    'In demo, attacks never show. Fix it and publish demo/index.html.']) {
    assert.equal(userMessageRequestsWorkspacePreview([], prompt), true, prompt);
  }
});

test('a prohibition still keeps a check of the existing page from republishing it', () => {
  for (const prompt of ['Check the existing site - do not republish it.', 'Inspect the current page. Btw don\'t publish.',
    "Check the existing page, but don't open a live preview.", "Verify the current site, but don't show me until it works.",
    "Check the existing site. Whatever happens don't show it to anyone."]) {
    assert.equal(userMessageRequestsWorkspaceVerificationContinuation([], prompt), false, prompt);
  }
});
