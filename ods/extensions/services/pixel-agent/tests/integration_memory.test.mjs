import test from 'node:test';
import assert from 'node:assert/strict';
import {noProposalTurns, chatSession, createMemoryHooks, MEMORY_CHOICES, memoryAnswer, memoryQuestion} from '../plugin/integration-memory.mjs';
import {createIntegrationTools} from '../plugin/integrations.mjs';
import {parseQuestions} from '../plugin/questions-schema.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

const USER = `ods-${'a'.repeat(64)}`;
const SESSION = `agent:pixel:openai-user:${USER}`;
// As dashboard/src/lib/pixelQuestions.js answersMessage writes the owner's answer to one card.
const answersMessage = (questions, answers) => questions.map(q => `${q.question}\n${answers[q.id].trim()}`).join('\n\n');
const notes = options => createIntegrationTools(options).find(tool => tool.name === 'pixel_ods_notes');

function recorder(replies) {
  const sent = [];
  const submit = async (payload, options) => {
    sent.push({payload, options});
    const reply = typeof replies === 'function' ? replies(payload) : replies[payload.action];
    if (reply instanceof Error) throw reply;
    return reply;
  };
  return {sent, submit};
}

test('only Portal chat sessions are named to the memory service', () => {
  assert.equal(chatSession(SESSION), USER);
  for (const value of [undefined, '', USER, 'agent:pixel:cron:job-1', `agent:pixel:openai-user:ods-123`, `${SESSION}x`]) {
    assert.equal(chatSession(value), '', value);
  }
});

test('the dashboard answer to a memory card is read back exactly', () => {
  const question = memoryQuestion('The owner is vegetarian.');
  for (const [label, choice] of Object.entries(MEMORY_CHOICES)) {
    const message = answersMessage([{id: 'memory_save', question, options: Object.keys(MEMORY_CHOICES)}], {memory_save: label});
    assert.deepEqual(memoryAnswer(message), {text: 'The owner is vegetarian.', choice});
  }
  assert.equal(memoryAnswer(`${question}\nMaybe later`), null, 'a custom answer is not a decision');
  // As Portal's ingress passes it on: the owner's answer, then its delivery note.
  const delivered = `${question}\nDon't save\n\n[ODS Portal delivery requirement: Answer the owner's complete message above. If it asks for exact text, copy that full exact text. Do not answer with a generic acknowledgement. Do not output NO_REPLY.]`;
  assert.deepEqual(memoryAnswer(delivered), {text: 'The owner is vegetarian.', choice: 'discard'});
  assert.deepEqual(memoryAnswer(`${question}\nSave\n\n[ODS Pixel delivery requirement: x]`), {text: 'The owner is vegetarian.', choice: 'save'});
  assert.equal(memoryAnswer(`${question}\nSave\n\nAnd also book a table.`), null, 'more than the answer is the owner talking');
  assert.equal(memoryAnswer('Remember that I am vegetarian'), null);
  assert.equal(memoryAnswer(undefined), null);
});

test('remember asks the owner with a card the chat can show, unless the owner said to save without asking', async () => {
  const {sent, submit} = recorder(payload => ({ok: true, status: 'ask', id: 'm1', text: payload.text}));
  const result = await notes({submit, sessionKey: SESSION}).execute('1', {action: 'remember', text: 'The owner is vegetarian.'});
  assert.deepEqual(sent[0].payload, {action: 'memory-propose', text: 'The owner is vegetarian.', session: USER});
  assert.equal(result.details.status, 'awaiting_user');
  assert.deepEqual(parseQuestions(result.details.questions), [{id: 'memory_save', question: 'Remember this about you? "The owner is vegetarian."',
    options: ['Save', "Don't save", 'Always save without asking']}]);
  // The card waits for the owner, but the reply to their message still comes, without tools.
  assert.match(result.content[0].text, /never answer it for them/);
  assert.match(result.content[0].text, /answer the owner's message in plain text/);
  for (const [status, expected] of [['saved', 'Remembered: The owner is vegetarian.'], ['known', 'Already remembered: The owner is vegetarian.']]) {
    const reply = await notes({submit: async () => ({ok: true, status, text: 'The owner is vegetarian.'}), sessionKey: SESSION})
      .execute('2', {action: 'remember', text: 'The owner is vegetarian.'});
    assert.equal(reply.content[0].text, expected);
    assert.equal(reply.details.status, 'done');
  }
  const outside = recorder({'memory-propose': {ok: true, status: 'ask', text: 'x'}});
  await notes({submit: outside.submit, sessionKey: 'agent:pixel:cron:job'}).execute('3', {action: 'remember', text: 'x'});
  assert.equal(outside.sent[0].payload.session, '', 'a scheduled task has no chat');
});

test('a refused memory is an error the model can read', async () => {
  const refused = await notes({submit: async () => ({ok: false, error: 'memory-quiet-chat', message: 'The owner set this chat not to be remembered. Do not save this.'})})
    .execute('1', {action: 'remember', text: 'x'});
  assert.equal(refused.isError, true);
  assert.match(refused.content[0].text, /not to be remembered/);
  const down = await notes({submit: async () => { throw new Error('The integrations service is not running.'); }}).execute('2', {action: 'remember', text: 'x'});
  assert.equal(down.isError, true);
  assert.match(notes({}).description, /action remember/);
  assert.deepEqual(notes({}).parameters.properties.action.enum, ['search', 'read', 'write', 'remember']);
});

test('the guard turns a memory card into the chat question card, like pixel_ods_ask_user', async () => {
  const guard = createToolLoopGuard(), ctx = {agentId: 'pixel', runId: 'memory-card', sessionId: 'session'};
  guard.observeRun(ctx, 'pixel', {prompt: 'I am vegetarian, by the way.'});
  const result = await notes({submit: async payload => ({ok: true, status: 'ask', id: 'm1', text: payload.text}), sessionKey: SESSION})
    .execute('remember', {action: 'remember', text: 'The owner is vegetarian.'});
  guard.afterToolCall({toolName: 'pixel_ods_notes', result}, {...ctx, toolCallId: 'remember'});
  assert.equal(guard.beforeToolCall({toolName: 'exec', params: {command: 'echo test'}}, ctx).block, true, 'the turn waits for the owner');
  assert.deepEqual(guard.deliveryVerificationForRun(ctx.runId).questions, result.details.questions);
  // An ordinary notes result is not a question.
  const other = createToolLoopGuard(), plain = {agentId: 'pixel', runId: 'notes-search', sessionId: 'session'};
  other.observeRun(plain, 'pixel', {prompt: 'Find my lease note'});
  other.afterToolCall({toolName: 'pixel_ods_notes', result: {content: [{type: 'text', text: 'Home/Lease.md'}], details: {status: 'done'}}}, {...plain, toolCallId: 'n'});
  assert.equal(other.deliveryVerificationForRun(plain.runId).questions, undefined);
});

test('each chat turn gets the facts and the learning hint; an answer to a card is applied first', async () => {
  const {sent, submit} = recorder({
    'memory-answer': {ok: true, status: 'saved'},
    'memory-context': {ok: true, text: 'Facts the owner chose…\n- The owner is vegetarian.', enabled: true, quiet: false},
  });
  const shared = {};
  const hooks = createMemoryHooks({submit, shared});
  const answer = `${memoryQuestion('The owner is vegetarian.')}\nSave`;
  const notesForTurn = await hooks.promptContext(answer, SESSION);
  assert.deepEqual(sent.map(item => item.payload), [
    {action: 'memory-answer', session: USER, text: 'The owner is vegetarian.', choice: 'save'},
    {action: 'memory-context', session: USER}]);
  assert.ok(sent.every(item => item.options.timeoutMs === 5000), 'a slow service never holds a turn for long');
  assert.match(notesForTurn.answer, /saved the memory/);
  assert.match(notesForTurn.facts, /vegetarian/);
  // A turn answering a card only acknowledges it: no invitation to propose more.
  assert.equal(notesForTurn.hint, undefined);
  assert.ok(noProposalTurns(shared).get(USER) === 'answered');
  const plain = await hooks.promptContext('What should I cook tonight?', SESSION);
  assert.equal(plain.answer, undefined);
  assert.equal(sent.at(-1).payload.action, 'memory-context');
  // The exact call, with the tool id the small model otherwise gets wrong.
  assert.match(plain.hint, /tool_call with id "openclaw:pixel-ods:pixel_ods_notes"/);
  assert.match(plain.hint, /"action": "remember"/);
  assert.ok(!noProposalTurns(shared).has(USER), 'the next turn may propose again');
});

test('in the turn that answers a memory card, a new proposal is refused without reaching the service', async () => {
  const shared = {};
  const {sent, submit} = recorder({'memory-answer': {ok: true, status: 'saved'}, 'memory-context': {ok: true, text: '', enabled: true, quiet: false},
    'memory-propose': {ok: true, status: 'ask', id: 'm2', text: "I will remember you're vegetarian."}});
  await createMemoryHooks({submit, shared}).promptContext(`${memoryQuestion('The owner is vegetarian.')}\nSave`, SESSION);
  const tool = notes({submit, sessionKey: SESSION, shared});
  const refused = await tool.execute('1', {action: 'remember', text: "I will remember you're vegetarian."});
  assert.equal(refused.details.status, 'done');
  assert.match(refused.content[0].text, /just answered/);
  assert.ok(!sent.some(item => item.payload.action === 'memory-propose'));
  // Another chat is not affected.
  const other = await notes({submit, sessionKey: `agent:pixel:openai-user:ods-${'b'.repeat(64)}`, shared})
    .execute('2', {action: 'remember', text: 'The owner likes tea.'});
  assert.equal(other.details.status, 'awaiting_user');
});

test('a reminder turn gets no learning hint, and a remember call is refused with the reminder call', async () => {
  const shared = {};
  const {sent, submit} = recorder({'memory-context': {ok: true, text: '- The owner is vegetarian.', enabled: true, quiet: false},
    'memory-propose': {ok: true, status: 'ask', id: 'm3', text: 'The owner reminds them to stretch in 2 minutes'}});
  const hooks = createMemoryHooks({submit, shared});
  const turn = await hooks.promptContext('remind me in 2 minutes to stretch', SESSION, {reminder: true});
  assert.equal(turn.hint, undefined);
  assert.match(turn.facts, /vegetarian/, 'the facts still come with the turn');
  assert.equal(noProposalTurns(shared).get(USER), 'reminder');
  const refused = await notes({submit, sessionKey: SESSION, shared})
    .execute('1', {action: 'remember', text: 'The owner reminds them to stretch in 2 minutes'});
  assert.equal(refused.details.status, 'done');
  assert.match(refused.content[0].text, /asked for a reminder/);
  assert.match(refused.content[0].text, /openclaw:pixel-ods:pixel_ods_notify/);
  assert.ok(!sent.some(item => item.payload.action === 'memory-propose'));
  // The next ordinary turn may propose again.
  const next = await hooks.promptContext('I live in Lisbon', SESSION);
  assert.match(next.hint, /"action": "remember"/);
  assert.ok(!noProposalTurns(shared).has(USER));
});

test('a quiet chat, memory off, other sessions and a missing service add nothing', async () => {
  const quiet = createMemoryHooks({submit: async () => ({ok: true, text: '', enabled: true, quiet: true})});
  assert.deepEqual(await quiet.promptContext('hello', SESSION), {});
  const off = createMemoryHooks({submit: async () => ({ok: true, text: '', enabled: false, quiet: false})});
  assert.deepEqual(await off.promptContext('hello', SESSION), {});
  const {sent, submit} = recorder({});
  assert.deepEqual(await createMemoryHooks({submit}).promptContext('hello', 'agent:pixel:cron:job'), {});
  assert.equal(sent.length, 0);
  const warnings = [];
  const down = createMemoryHooks({submit: async () => { throw new Error('The integrations service is not running.'); }, warn: message => warnings.push(message)});
  assert.deepEqual(await down.promptContext('hello', SESSION), {});
  assert.deepEqual(warnings, ['Portal memory: unavailable for this turn: The integrations service is not running.']);
  const stale = createMemoryHooks({submit: async payload => payload.action === 'memory-answer' ? {ok: true, status: 'unknown'} : {ok: true, text: '', enabled: true}});
  assert.match((await stale.promptContext(`${memoryQuestion('x')}\nSave`, SESSION)).answer, /no longer waiting/);
});
