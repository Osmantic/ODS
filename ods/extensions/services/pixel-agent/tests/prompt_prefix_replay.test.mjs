// Replay of consecutive owner turns as a local llama.cpp server renders them.
// A hybrid model (Qwen3.5/3.6) can only reuse its cached prompt up to the first
// byte that differs from the previous request, so each owner turn must extend
// the previous one: the system+tools block first, then the whole earlier run.
import test from 'node:test';
import assert from 'node:assert/strict';
import {commonPrefixLength, renderQwen35} from './qwen-chat-render.mjs';
import {replayOwnerTurns, slotAfter, systemBlockLength, OWNER_TURNS} from './prompt-prefix-replay.mjs';
import {ODS_WORKSPACE_PREVIEW_CONTRACT, ODS_WORKSPACE_VISUAL_CONTINUATION_CONTRACT} from '../plugin/prompt-contract.mjs';
import {hostDateContext} from '../plugin/completion-assurance.mjs';
import {TURN_GUIDANCE_HEADER} from '../plugin/turn-guidance.mjs';
import {registeredPixelTools} from './tool-grammar-registration.mjs';

const render = preserveThinking => request => renderQwen35({...request, preserveThinking});
const lastOfTurn = (requests, turn) => requests.filter(request => request.turn === turn).at(-1);
const firstOfTurn = (requests, turn) => requests.find(request => request.turn === turn);

// For each owner-turn transition: how much of the previous slot (rendered
// prompt plus generated answer) the next owner turn's first request shares.
function transitions(requests, renderer) {
  return OWNER_TURNS.slice(1).map((_, index) => {
    const slot = slotAfter(renderer, lastOfTurn(requests, index));
    const next = renderer(firstOfTurn(requests, index + 1));
    return {slot, next, shared: commonPrefixLength(slot, next), block: systemBlockLength(next)};
  });
}

test('two consecutive owner turns share the full system+tools block and the whole earlier run', async () => {
  const {requests} = await replayOwnerTurns();
  const [create, edit, question] = [0, 1, 2].map(turn => firstOfTurn(requests, turn));
  // Byte-identical system prompt and tool definitions across owner messages.
  assert.equal(edit.system, create.system);
  assert.equal(question.system, create.system);
  assert.equal(JSON.stringify(edit.tools), JSON.stringify(create.tools));
  // The turns really select different guidance, which now rides on the owner message.
  assert.ok(create.messages.at(-1).content.includes(ODS_WORKSPACE_PREVIEW_CONTRACT.trim()));
  assert.ok(edit.messages.at(-1).content.includes(ODS_WORKSPACE_VISUAL_CONTINUATION_CONTRACT.trim()));
  assert.ok(!create.system.includes(ODS_WORKSPACE_PREVIEW_CONTRACT.trim()));
  for (const {slot, next, shared, block} of transitions(requests, render(true))) {
    assert.ok(block > 0 && shared >= block, `shared ${shared} < system+tools block ${block}`);
    // Stored guidance plus preserved empty think blocks: append-only.
    assert.equal(shared, slot.length, `diverged at ${shared}: ${JSON.stringify(next.slice(shared - 80, shared + 80))}`);
    assert.ok(next.startsWith(slot));
  }
});

test('without the preserved think blocks the break moves to the first assistant turn of the earlier run', async () => {
  const {requests} = await replayOwnerTurns();
  const header = '<|im_start|>assistant\n';
  for (const [index, {slot, next, shared, block}] of transitions(requests, render(false)).entries()) {
    assert.ok(shared >= block, 'the system+tools block is still shared');
    assert.ok(shared < slot.length);
    // The earlier run's first assistant turn lost its empty think block.
    const owner = slot.lastIndexOf(OWNER_TURNS[index].prompt);
    const firstAssistant = slot.indexOf(header, owner) + header.length;
    assert.ok(owner > 0 && shared >= firstAssistant && shared < firstAssistant + '<think>'.length, `diverged at ${shared}`);
    assert.ok(slot.startsWith('<think>\n\n</think>\n\n', firstAssistant));
    assert.ok(!next.startsWith('<think>', firstAssistant));
  }
});

test('unstored guidance breaks at the end of the previous owner message', async () => {
  const {requests} = await replayOwnerTurns({mode: 'unstored'});
  const [first] = transitions(requests, render(true));
  assert.ok(first.shared >= first.block);
  assert.ok(first.slot.slice(first.shared).startsWith(`\n\n${TURN_GUIDANCE_HEADER}`));
});

test('the earlier composition broke inside the system block on every owner turn', async () => {
  const {requests} = await replayOwnerTurns({mode: 'legacy'});
  for (const {shared, block} of transitions(requests, render(true))) {
    assert.ok(shared < block, `legacy composition unexpectedly shared ${shared} of ${block}`);
  }
});

test('the host date is stated once, with the first owner message, and stored there', async () => {
  const {requests, transcript} = await replayOwnerTurns();
  const statement = hostDateContext().trim();
  const owners = transcript.filter(message => message.role === 'user').map(message => message.content[0].text);
  assert.ok(owners[0].includes(statement));
  assert.ok(!owners[1].includes(statement) && !owners[2].includes(statement));
  assert.ok(!requests.some(request => request.system.includes(statement)));
  assert.doesNotMatch(requests[0].system, /\d{4}-\d{2}-\d{2}/);
});

test('plugin tool definitions do not depend on the run or owner message', async () => {
  const session = 'agent:pixel:openai-user:ods-' + 'a'.repeat(64);
  const first = await registeredPixelTools({context: {agentId: 'pixel', sessionKey: session, runId: 'run-a'}});
  const second = await registeredPixelTools({context: {agentId: 'pixel', sessionKey: session, runId: 'run-b',
    messageChannel: 'webchat'}});
  const schema = tools => JSON.stringify(tools.map(({name, description, parameters}) => ({name, description, parameters})));
  assert.equal(schema(second), schema(first));
});
