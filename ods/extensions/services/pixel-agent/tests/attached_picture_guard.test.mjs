import assert from 'node:assert/strict';
import test from 'node:test';
import {ATTACHED_PICTURE_IN_CONTEXT_REASON, createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {currentImageNote, nativeHistoryMessages} from '../host/chat_image_transport.mjs';

const ref = {id:'img-' + 'a'.repeat(32), sha256:'b'.repeat(64)};
const other = {id:'img-' + 'c'.repeat(32), sha256:'d'.repeat(64)};

function context(runId, toolName) {
  return {agentId:'pixel', runId, sessionId:`session-${runId}`, sessionKey:`agent:pixel:openai-user:${runId}`,
    toolName, toolCallId:`${runId}-${toolName}`};
}

// The shape pixel_ingress forwards for an image turn: owner text, pixels,
// Edge delivery guidance, then the current-turn note.
function imageTurn(references = [ref]) {
  return {role:'user', content:[
    {type:'text', text:'What is in this picture?'},
    {type:'image_url', image_url:{url:'data:image/png;base64,iVBORw0KGgo='}},
    {type:'text', text:'\n\n[ODS Portal delivery requirement: Answer the owner above.]'},
    {type:'text', text:currentImageNote(references)},
  ]};
}

async function decide(guard, runId, toolName, params) {
  return await guard.beforeToolCall({toolName, params}, context(runId, toolName));
}

test('tool calls naming a picture already in the current message are refused with a reason', async () => {
  const guard = createToolLoopGuard();
  guard.observeRun(context('picture', 'web_search'), 'pixel', {messages:[imageTurn([ref, other])]});
  for (const [toolName, params] of [
    ['web_search', {query:ref.id}],
    ['web_fetch', {url:`https://example.com/${other.id}`}],
    ['pixel_ods_image_read', {id:ref.id, sha256:ref.sha256}],
    ['read', {path:`${ref.id}.png`}],
    ['tool_call', {id:'openclaw:core:web_search', args:{query:`describe ${ref.id}`}}],
  ]) {
    const decision = await decide(guard, 'picture', toolName, params);
    assert.equal(decision?.block, true, toolName);
    assert.equal(decision.blockReason, ATTACHED_PICTURE_IN_CONTEXT_REASON, toolName);
  }
  assert.match(ATTACHED_PICTURE_IN_CONTEXT_REASON, /already in your context/);
  assert.match(ATTACHED_PICTURE_IN_CONTEXT_REASON, /no tool was run/);
});

test('the prompt form of an image turn is recognized too', async () => {
  const guard = createToolLoopGuard();
  const prompt = 'What is in this picture?\n\n[ODS Portal delivery requirement: Answer the owner above.]\n'
    + currentImageNote([ref]);
  guard.observeRun(context('prompt', 'web_search'), 'pixel', {prompt});
  assert.equal((await decide(guard, 'prompt', 'web_search', {query:ref.id}))?.blockReason, ATTACHED_PICTURE_IN_CONTEXT_REASON);
});

test('tools that do not name the picture are left to the rest of the guard', async () => {
  const guard = createToolLoopGuard();
  guard.observeRun(context('unrelated', 'web_search'), 'pixel', {messages:[imageTurn()]});
  assert.notEqual((await decide(guard, 'unrelated', 'web_search', {query:'red panda habitat'}))?.blockReason,
    ATTACHED_PICTURE_IN_CONTEXT_REASON);
});

test('pictures from history, quoted identifiers and later turns are not restricted', async () => {
  const history = nativeHistoryMessages([{role:'user', content:'Earlier picture', images:[ref]}])[0];
  const cases = [
    {messages:[history, {role:'assistant', content:'A cat.'}, {role:'user', content:'Show that picture again.'}]},
    {prompt:`Read ${ref.id} again please.`},
  ];
  for (const [index, event] of cases.entries()) {
    const guard = createToolLoopGuard();
    guard.observeRun(context(`plain-${index}`, 'pixel_ods_image_read'), 'pixel', event);
    assert.notEqual((await decide(guard, `plain-${index}`, 'pixel_ods_image_read', {...ref}))?.blockReason,
      ATTACHED_PICTURE_IN_CONTEXT_REASON, String(index));
  }
  const guard = createToolLoopGuard();
  guard.observeRun(context('first', 'web_search'), 'pixel', {messages:[imageTurn()]});
  guard.observeRun(context('second', 'pixel_ods_image_read'), 'pixel', {messages:[history,
    {role:'user', content:'Look at the earlier picture again.'}]});
  assert.notEqual((await decide(guard, 'second', 'pixel_ods_image_read', {...ref}))?.blockReason, ATTACHED_PICTURE_IN_CONTEXT_REASON);
});

test('after compaction the picture may be gone, so re-reading it is allowed', async () => {
  const guard = createToolLoopGuard();
  const runContext = context('compacted', 'pixel_ods_image_read');
  guard.observeRun(runContext, 'pixel', {messages:[imageTurn()]});
  assert.equal((await decide(guard, 'compacted', 'pixel_ods_image_read', {...ref}))?.blockReason, ATTACHED_PICTURE_IN_CONTEXT_REASON);
  guard.observeCompaction({sessionKey:runContext.sessionKey}, 'start');
  guard.observeCompaction({sessionKey:runContext.sessionKey}, 'end');
  assert.notEqual((await decide(guard, 'compacted', 'pixel_ods_image_read', {...ref}))?.blockReason, ATTACHED_PICTURE_IN_CONTEXT_REASON);
});
