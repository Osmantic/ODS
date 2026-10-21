// RTX Spark, 2026-10-16. 18:09Z the owner asked Portal "Increase enemies by
// 10%" (session 02269765); the model called pixel_ods_ask_user with two
// questions and said it would make the change once answered. 20:28Z, after a
// 20:24:50Z gateway restart, the card's answer (dashboard answersMessage)
// became the first message of a new native session 8b4c4b17. The model read
// its own question as the owner asking where to apply it, answered with a
// location and edited nothing.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import {createHash} from 'node:crypto';
import {createAskUserTool, requestsChoiceQuestion} from '../plugin/ask-user.mjs';
import {requestsNewPlaygroundProject} from '../plugin/playground-projects.mjs';
import {createPendingClarifications, answersClarification, clarificationAnswerContext,
  IMAGE_ONLY_REQUEST, PENDING_CLARIFICATION_TTL_MS} from '../plugin/pending-clarification.mjs';
import {createToolLoopGuard, userMessageAuthorizesRecursiveDelete, RECURSIVE_DELETE_REQUIRES_OWNER_REASON,
  OWNER_CANCELLED_REQUEST_CONTEXT} from '../plugin/tool-loop-guard.mjs';
import {createConversationImageLifecycle} from '../plugin/conversation-image-lifecycle.mjs';
import {promptContractForAgent} from '../plugin/prompt-contract.mjs';
import {createSubagentDelivery} from '../plugin/subagent-delivery.mjs';
import {GOAL_CONTRACT} from '../plugin/goal-progress.mjs';
import {GOAL_TURN_LANGUAGE_NOTE} from '../plugin/response-language.mjs';

const USER = 'ods-2c7a1f3b9e0d4c6a8b5e7f9a1c3d5e7f9b1a3c5e7d9f1b3a5c7e9d1f3b5a7c9e';
const SESSION_KEY = `agent:pixel:openai-user:${USER}`;
const PORTAL = "\n\n[ODS Portal delivery requirement: Answer the owner's complete message above. If it asks for exact text, " +
  'copy that full exact text. Do not answer with a generic acknowledgement. Do not output NO_REPLY.]';
const REQUEST = 'Increase enemies by 10%';
const QUESTIONS = [
  {id: 'target', question: 'Where should I apply the 10% enemy increase?',
    options: ["A game project in my workspace (I'll locate the enemy/spawn config)", 'A game you will describe']},
  {id: 'meaning', question: 'What does "increase enemies by 10%" mean for you?',
    options: ['Enemy spawn count / density', 'Enemy health / damage']},
];
const ANSWERS = {target: QUESTIONS[0].options[0], meaning: 'Enemy spawn count / density'};
// dashboard/src/lib/pixelQuestions.js answersMessage; sendMessage trims it.
const answersMessage = (questions, answers) => questions.map(q => `${q.question}\n${answers[q.id].trim()}`).join('\n\n');
const FIELD_ANSWER = "Where should I apply the 10% enemy increase?\nA game project in my workspace (I'll locate the enemy/spawn config)\n\n" +
  'What does "increase enemies by 10%" mean for you?\nEnemy spawn count / density';
// The exact model-only context the 8b4c4b17 turn now receives after its owner message.
const FIELD_CONTEXT = '[ODS Portal note, not owner text: the owner has answered the clarification questions you asked earlier. ' +
  "Their message repeats each of your questions, then gives the answer; those questions are your words, not the owner asking you.\n" +
  'Original request:\nIncrease enemies by 10%\n' +
  'If an answer declines or changes the request, follow the answer. Otherwise finish the original request now using ' +
  'the answers, building on any work already done for it. This note does not set your reply language.]';

function stateDir(t) {
  const root = fs.mkdtempSync(path.join(tmpdir(), 'ods-pending-clarification-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  return path.join(root, '.ods-pending-clarifications');
}
const context = (runId, extra = {}) => ({agentId: 'pixel', runId, sessionId: `session-${runId}`, sessionKey: SESSION_KEY, trigger: 'user', ...extra});
const records = dir => fs.existsSync(dir) ? fs.readdirSync(dir) : [];
// OpenClaw agent_end for one attempt: success is !aborted && !promptError.
const ended = (guard, ctx, success = true) => guard.observeAgentEnd({runId: ctx.runId, success, messages: []}, ctx);

// One owner turn that ends on a delivered card, as the plugin hooks see it.
async function askingRun(guard, runId, prompt, questions = QUESTIONS, {capabilities, before = [], extra = {}, contract = PORTAL} = {}) {
  const ctx = context(runId, extra);
  guard.observeRun(ctx, 'pixel', {prompt: prompt + contract}, capabilities);
  for (const call of before) call(guard, ctx);
  const result = await createAskUserTool().execute('ask', {questions});
  guard.afterToolCall({toolName: 'pixel_ods_ask_user', toolCallId: `${runId}:ask`, params: {questions}, result}, {...ctx, toolCallId: `${runId}:ask`});
  assert.equal(guard.beforeAgentFinalize({lastAssistantMessage: 'I will make the change once you answer.'}, ctx).action, 'finalize');
  assert.equal(guard.deliveryVerificationForRun(runId).status, 'pending');
  return ctx;
}

test('the card answer is recognized only in the exact shape the dashboard sends', () => {
  assert.equal(answersMessage(QUESTIONS, ANSWERS), FIELD_ANSWER);
  for (const text of [
    FIELD_ANSWER,
    answersMessage(QUESTIONS, {...ANSWERS, meaning: 'More bosses\n\nand faster ones'}),
    FIELD_ANSWER.replace(/\n/g, '\r\n'),
    `\n  ${FIELD_ANSWER}`,
  ]) assert.equal(answersClarification(QUESTIONS, text), true, text);
  assert.equal(answersClarification(QUESTIONS.slice(0, 1), 'Where should I apply the 10% enemy increase?\nworld.js'), true);
  // The dashboard trims what it sends, so a first question that starts with
  // spaces arrives without them.
  const spaced = [{...QUESTIONS[0], question: `  ${QUESTIONS[0].question}`}, QUESTIONS[1]];
  assert.equal(answersClarification(spaced, answersMessage(spaced, ANSWERS).trim()), true);
  for (const text of [
    'Increase them by 20% instead',
    REQUEST,
    'Where should I apply the 10% enemy increase?',
    'Where should I apply the 10% enemy increase?\n',
    'Where should I apply the 10% enemy increase?\nworld.js',
    `Please: ${FIELD_ANSWER}`,
    answersMessage([...QUESTIONS].reverse(), ANSWERS),
    FIELD_ANSWER.replace('\n\nWhat does', '\nWhat does'),
    'Where should I apply the 10% enemy increase?\n\n\nWhat does "increase enemies by 10%" mean for you?\nEnemy spawn count / density',
    `${FIELD_ANSWER.split('\n\n')[1]}\n\n${FIELD_ANSWER.split('\n\n')[0]}`,
    `${FIELD_ANSWER.split('\n\n')[0]}\n\nWhat does "increase enemies by 10%" mean for you?\n  `,
  ]) assert.equal(answersClarification(QUESTIONS, text), false, text);
  for (const text of ['Where should I apply the 10% enemy increase?\n', 'Where should I apply the 10% enemy increase?\n \n']) {
    assert.equal(answersClarification(QUESTIONS.slice(0, 1), text), false, 'an empty answer is not an answer');
  }
  for (const questions of [undefined, [], [{question: ''}]]) assert.equal(answersClarification(questions, FIELD_ANSWER), false);
});

test('records are private, bounded, replaced by a newer card, claimed once and expire', t => {
  let clock = Date.parse('2026-10-16T18:10:00Z');
  const dir = stateDir(t), store = createPendingClarifications({stateDir: dir, now: () => clock});
  const answer = () => true, other = () => false;
  assert.equal(store.claim(SESSION_KEY, answer), null, 'no store directory yet');
  assert.equal(store.forget(SESSION_KEY), false);
  assert.equal(store.remember({sessionKey: SESSION_KEY, runId: 'ask', questions: QUESTIONS, request: REQUEST + PORTAL}), true);
  if (process.platform !== 'win32') {
    assert.equal(fs.statSync(dir).mode & 0o777, 0o700);
    for (const name of fs.readdirSync(dir)) assert.equal(fs.statSync(path.join(dir, name)).mode & 0o777, 0o600);
  }
  const [name] = fs.readdirSync(dir);
  assert.match(name, /^[a-f0-9]{64}\.json$/, 'one hashed record, no temporary file');
  assert.ok(!name.includes(USER.slice(4, 20)), 'the chat identity is hashed again');
  clock += 2 * 60 * 60 * 1000 + 18 * 60 * 1000;
  const expected = {schemaVersion: 1, sessionHash: name.slice(0, 64), runId: 'ask',
    createdAt: Date.parse('2026-10-16T18:10:00Z'), request: REQUEST, questions: QUESTIONS, earlierAnswers: []};
  // The answer keeps it until its run settles; a resend reads it again.
  assert.deepEqual(store.claim(SESSION_KEY, record => { assert.deepEqual(record, expected); return true; }), expected);
  assert.deepEqual(store.claim(SESSION_KEY, answer), expected);
  assert.equal(store.settle(SESSION_KEY, {...expected, runId: 'another'}), false, 'only that card');
  assert.equal(store.settle(SESSION_KEY, expected), true);
  assert.deepEqual(fs.readdirSync(dir), []);
  assert.equal(store.settle(SESSION_KEY, expected), false);
  // Any other turn consumes it unused.
  assert.equal(store.remember({sessionKey: SESSION_KEY, runId: 'ask', questions: QUESTIONS, request: REQUEST}), true);
  assert.equal(store.claim(SESSION_KEY, other), null);
  assert.deepEqual(fs.readdirSync(dir), [], 'consumed');
  assert.equal(store.claim(SESSION_KEY, answer), null);

  assert.equal(store.remember({sessionKey: SESSION_KEY, runId: 'first', questions: QUESTIONS, request: 'first'}), true);
  assert.equal(store.remember({sessionKey: SESSION_KEY, runId: 'second', questions: QUESTIONS.slice(1), request: 'second'}), true);
  const newer = store.claim(SESSION_KEY, answer);
  assert.equal(newer.runId, 'second');
  assert.deepEqual(newer.questions, QUESTIONS.slice(1));
  assert.equal(store.forget(SESSION_KEY), true);
  assert.deepEqual(fs.readdirSync(dir), [], 'forgotten');

  const long = 'a'.repeat(3999) + '\u{1F600}' + 'b'.repeat(2000);
  assert.equal(store.remember({sessionKey: SESSION_KEY, runId: 'long', questions: QUESTIONS, request: long,
    earlierAnswers: ['one', 'two' + PORTAL, 'x'.repeat(5000) + PORTAL]}), true);
  const kept = store.claim(SESSION_KEY, answer);
  assert.equal(kept.request, 'a'.repeat(3999) + ' [shortened]', 'no lone surrogate at the cut');
  assert.deepEqual(kept.earlierAnswers, ['two', 'x'.repeat(2000) + ' [shortened]'], 'bounded, without the delivery contract');
  store.forget(SESSION_KEY);
  assert.equal(store.remember({sessionKey: SESSION_KEY, runId: 'empty', questions: QUESTIONS, request: REQUEST,
    earlierAnswers: ['two', PORTAL]}), true);
  assert.deepEqual(store.claim(SESSION_KEY, answer).earlierAnswers, ['two'], 'an earlier answer of only the contract is dropped');
  store.forget(SESSION_KEY);

  // Portal's contract is cut at its last marker that starts a line, in the
  // current, multipart (one newline) and legacy forms. A marker the owner
  // quotes inside a line, or before more of their own text, stays.
  const quoted = 'Why does my log say "[ODS Portal delivery requirement: answer]" here?';
  const logged = 'Here is the log:\n[ODS Portal delivery requirement: answer briefly.]\nWhy is that line there?';
  for (const [request, expected] of [
    [`${REQUEST}\n${PORTAL.trimStart()}`, REQUEST],
    [`${REQUEST}\n\n[ODS Pixel delivery requirement: Answer the owner's complete message above.]`, REQUEST],
    [quoted + PORTAL, quoted],
    [logged + PORTAL, logged],
    [logged, logged],
    // An image-only turn: image parts never reach the prompt text, so the
    // edge's contract is all of it. Its answer still gets a note.
    [PORTAL.trimStart(), IMAGE_ONLY_REQUEST],
    [PORTAL, IMAGE_ONLY_REQUEST],
  ]) {
    assert.equal(store.remember({sessionKey: SESSION_KEY, runId: 'r', questions: QUESTIONS, request}), true);
    assert.equal(store.claim(SESSION_KEY, answer).request, expected, request);
    store.forget(SESSION_KEY);
  }

  for (const value of [
    {sessionKey: 'agent:pixel:main', runId: 'r', questions: QUESTIONS, request: REQUEST},
    {sessionKey: SESSION_KEY, runId: 'r', questions: [{...QUESTIONS[0], options: ['one']}], request: REQUEST},
    {sessionKey: SESSION_KEY, runId: '', questions: QUESTIONS, request: REQUEST},
  ]) assert.equal(store.remember(value), false);
  assert.equal(store.claim('agent:pixel:main', answer), null);
  assert.equal(store.forget('agent:pixel:main'), false);
  assert.deepEqual(fs.readdirSync(dir), []);

  assert.equal(store.remember({sessionKey: SESSION_KEY, runId: 'stale', questions: QUESTIONS, request: REQUEST}), true);
  clock += PENDING_CLARIFICATION_TTL_MS + 1;
  assert.equal(store.claim(SESSION_KEY, answer), null, 'expired');
  assert.deepEqual(fs.readdirSync(dir), []);
  assert.equal(store.remember({sessionKey: SESSION_KEY, runId: 'future', questions: QUESTIONS, request: REQUEST}), true);
  clock -= 2 * 60 * 1000;
  assert.equal(store.claim(SESSION_KEY, answer), null, 'a record from the future is not trusted');
  assert.deepEqual(fs.readdirSync(dir), []);
});

test('a forged, oversized or linked record is refused and deleted, and a crashed write is pruned', t => {
  const dir = stateDir(t), store = createPendingClarifications({stateDir: dir});
  const other = `agent:pixel:openai-user:ods-${'e'.repeat(64)}`;
  assert.equal(store.remember({sessionKey: SESSION_KEY, runId: 'ask', questions: QUESTIONS, request: REQUEST}), true);
  const [name] = fs.readdirSync(dir), file = path.join(dir, name), valid = JSON.parse(fs.readFileSync(file, 'utf8'));
  for (const [label, write, error = /Invalid clarification record/] of [
    ['an extra key', () => fs.writeFileSync(file, JSON.stringify({...valid, extra: true}))],
    ['another schema', () => fs.writeFileSync(file, JSON.stringify({...valid, schemaVersion: 2}))],
    ['another chat', () => fs.writeFileSync(file, JSON.stringify({...valid, sessionHash: 'f'.repeat(64)}))],
    ['three earlier answers', () => fs.writeFileSync(file, JSON.stringify({...valid, earlierAnswers: ['a', 'b', 'c']}))],
    ['an over-long earlier answer', () => fs.writeFileSync(file, JSON.stringify({...valid, earlierAnswers: ['a'.repeat(2013)]}))],
    ['an empty earlier answer', () => fs.writeFileSync(file, JSON.stringify({...valid, earlierAnswers: ['']}))],
    ['a non-integer createdAt', () => fs.writeFileSync(file, JSON.stringify({...valid, createdAt: valid.createdAt + 0.5}))],
    ['no asking run', () => fs.writeFileSync(file, JSON.stringify({...valid, runId: ''}))],
    ['an over-long request', () => fs.writeFileSync(file, JSON.stringify({...valid, request: 'r'.repeat(4100)}))],
    ['invalid questions', () => fs.writeFileSync(file, JSON.stringify({...valid, questions: [{...QUESTIONS[0], options: ['one']}]}))],
    ['an oversized file', () => fs.writeFileSync(file, JSON.stringify({...valid, pad: 'p'.repeat(70000)}))],
    ['not JSON', () => fs.writeFileSync(file, '{'), /JSON/],
  ]) {
    write();
    assert.throws(() => store.claim(SESSION_KEY, () => true), error, label);
    assert.equal(fs.existsSync(file), false, `${label}: removed, never used`);
    assert.equal(store.remember({sessionKey: SESSION_KEY, runId: 'ask', questions: QUESTIONS, request: REQUEST}), true);
  }
  const copy = path.join(dir, `${createHash('sha256').update(other).digest('hex')}.json`);
  fs.copyFileSync(file, copy);
  assert.throws(() => store.settle(other, valid), /Invalid clarification record/, 'a record copied to another chat');
  assert.equal(fs.existsSync(copy), false);
  if (process.platform !== 'win32') {
    const outside = path.join(path.dirname(dir), 'outside.json');
    fs.renameSync(file, outside);
    fs.symlinkSync(outside, file);
    assert.throws(() => store.claim(SESSION_KEY, () => true), /Invalid clarification record/);
    assert.equal(fs.existsSync(file), false, 'the link is removed');
    assert.equal(fs.existsSync(outside), true, 'its target is not');
    fs.linkSync(outside, file);
    assert.throws(() => store.claim(SESSION_KEY, () => true), /Invalid clarification record/, 'a hard link is refused');
    assert.equal(store.remember({sessionKey: SESSION_KEY, runId: 'ask', questions: QUESTIONS, request: REQUEST}), true);
    fs.chmodSync(file, 0o644);
    assert.throws(() => store.claim(SESSION_KEY, () => true), /Invalid clarification record/, 'a readable record is refused');
    fs.chmodSync(dir, 0o755);
    assert.throws(() => store.claim(SESSION_KEY, () => true), /Private clarification store required/);
    assert.throws(() => store.forget(SESSION_KEY), /Private clarification store required/, 'deletion fails closed too');
    fs.chmodSync(dir, 0o700);
  }
  const crashed = path.join(dir, `.write-${'a'.repeat(32)}`), fresh = path.join(dir, `.write-${'b'.repeat(32)}`);
  fs.writeFileSync(crashed, '{"request":"private"}');
  fs.writeFileSync(fresh, '{}');
  const old = new Date(Date.now() - 2 * 60 * 1000);
  fs.utimesSync(crashed, old, old);
  store.forget(SESSION_KEY);
  assert.equal(fs.existsSync(crashed), false, 'a crashed write leaves no owner text behind');
  assert.equal(fs.existsSync(fresh), true);
});

test('store boundaries: clock skew, the expiry edge, which card settles, pruning on claim', t => {
  // A minute behind the wall clock, so the files' real mtimes never look expired.
  const start = Date.now() - 60 * 1000;
  let clock = start;
  const dir = stateDir(t), store = createPendingClarifications({stateDir: dir, now: () => clock});
  const remember = (sessionKey = SESSION_KEY) => store.remember({sessionKey, runId: 'ask', questions: QUESTIONS, request: REQUEST});
  const accept = () => true;
  clock = start + 30 * 1000; remember(); clock = start;
  assert.equal(store.claim(SESSION_KEY, accept)?.runId, 'ask', 'a record 30 s ahead is within the skew allowance');
  store.forget(SESSION_KEY);
  remember();
  clock = start + PENDING_CLARIFICATION_TTL_MS;
  assert.equal(store.claim(SESSION_KEY, accept)?.runId, 'ask', 'exactly 24 hours old is still answerable');
  clock += 1;
  assert.equal(store.claim(SESSION_KEY, accept), null, 'one millisecond later it is expired');
  clock = start;
  remember();
  const stored = store.claim(SESSION_KEY, accept);
  assert.equal(store.settle(SESSION_KEY, {...stored, createdAt: stored.createdAt + 1}), false, 'a card of the same run asked at another time');
  assert.equal(records(dir).length, 1);
  assert.equal(store.settle(SESSION_KEY, stored), true);
  // A claim in any chat prunes another chat's expired card.
  const other = `agent:pixel:openai-user:ods-${'e'.repeat(64)}`;
  remember(other);
  const abandoned = path.join(dir, `${createHash('sha256').update(other).digest('hex')}.json`);
  const old = new Date(clock - PENDING_CLARIFICATION_TTL_MS - 60 * 1000);
  fs.utimesSync(abandoned, old, old);
  assert.equal(store.claim(SESSION_KEY, accept), null);
  assert.equal(fs.existsSync(abandoned), false);
});

test('abandoned cards expire to make room and the store refuses new chats only when full', t => {
  let clock = Date.now();
  const dir = stateDir(t), store = createPendingClarifications({stateDir: dir, now: () => clock, maximumSessions: 2});
  const key = n => `agent:pixel:openai-user:ods-${String(n).repeat(64)}`;
  assert.equal(store.remember({sessionKey: key(1), runId: 'a', questions: QUESTIONS, request: REQUEST}), true);
  assert.equal(store.remember({sessionKey: key(2), runId: 'b', questions: QUESTIONS, request: REQUEST}), true);
  assert.equal(store.remember({sessionKey: key(3), runId: 'c', questions: QUESTIONS, request: REQUEST}), false, 'full');
  assert.equal(store.remember({sessionKey: key(2), runId: 'b2', questions: QUESTIONS, request: REQUEST}), true, 'a chat with a card may replace it');
  clock += PENDING_CLARIFICATION_TTL_MS + 60 * 1000;
  assert.equal(store.remember({sessionKey: key(3), runId: 'c', questions: QUESTIONS, request: REQUEST}), true);
  assert.equal(fs.readdirSync(dir).length, 1, 'expired records were deleted');
  assert.equal(store.claim(key(3), () => true).runId, 'c');
});

test('field case: the answer in a replacement session gets its request as model-only context once', async t => {
  const dir = stateDir(t), store = createPendingClarifications({stateDir: dir});
  const warnings = [];
  await askingRun(createToolLoopGuard({pendingClarifications: store}), 'run-02269765', REQUEST);
  // A gateway restart drops every in-memory run; the record is on disk.
  const guard = createToolLoopGuard({pendingClarifications: store, warn: message => warnings.push(message)});
  const answer = context('run-8b4c4b17');
  const event = {prompt: FIELD_ANSWER + PORTAL, messages: []};
  const before = structuredClone(event);
  guard.observeRun(answer, 'pixel', event);
  assert.equal(guard.clarificationContextForRun(answer.runId), FIELD_CONTEXT);
  assert.deepEqual(event, before, 'the owner turn is not rewritten');
  assert.deepEqual(guard.ownerIntentEventForRun(answer.runId, event), before, 'classifiers read the owner turn unchanged');
  assert.equal(guard.promptContextForRun(answer.runId), undefined, 'nothing is prepended to the owner turn');
  // Every attempt of that run keeps it; the record goes once the run ends well.
  guard.observeRun(answer, 'pixel', {prompt: 'Continue where you left off.'});
  assert.equal(guard.clarificationContextForRun(answer.runId), FIELD_CONTEXT);
  assert.equal(records(dir).length, 1);
  ended(guard, answer);
  assert.deepEqual(records(dir), []);
  assert.equal(guard.clarificationContextForRun(answer.runId), FIELD_CONTEXT, 'a later attempt still has it');
  const repeat = context('run-repeat');
  guard.observeRun(repeat, 'pixel', event);
  assert.equal(guard.clarificationContextForRun(repeat.runId), undefined, 'the next run never sees it again');
  assert.deepEqual(warnings, []);
  assert.equal(clarificationAnswerContext({request: REQUEST, earlierAnswers: []}), FIELD_CONTEXT);
});

test('an answer that was blocked, failed or lost to a restart keeps its card for the resend', async t => {
  const dir = stateDir(t), store = createPendingClarifications({stateDir: dir});
  await askingRun(createToolLoopGuard({pendingClarifications: store}), 'ask', REQUEST);
  // A before_agent_run block or a provider error ends with success false;
  // a gateway restart mid-run ends with no agent_end at all.
  const blocked = createToolLoopGuard({pendingClarifications: store});
  const first = context('answer-blocked');
  blocked.observeRun(first, 'pixel', {prompt: FIELD_ANSWER + PORTAL});
  assert.equal(blocked.clarificationContextForRun(first.runId), FIELD_CONTEXT);
  ended(blocked, first, false);
  assert.equal(records(dir).length, 1);
  const lost = context('answer-lost');
  blocked.observeRun(lost, 'pixel', {prompt: FIELD_ANSWER + PORTAL});
  assert.equal(blocked.clarificationContextForRun(lost.runId), FIELD_CONTEXT);
  const restarted = createToolLoopGuard({pendingClarifications: store});
  const resent = context('answer-resent');
  restarted.observeRun(resent, 'pixel', {prompt: FIELD_ANSWER + PORTAL});
  assert.equal(restarted.clarificationContextForRun(resent.runId), FIELD_CONTEXT);
  ended(restarted, resent);
  assert.deepEqual(records(dir), []);
  // After a failed answer, any other owner turn still ends the card.
  await askingRun(restarted, 'ask-again', REQUEST);
  const failed = context('answer-failed');
  restarted.observeRun(failed, 'pixel', {prompt: FIELD_ANSWER + PORTAL});
  ended(restarted, failed, false);
  const instead = context('instead');
  restarted.observeRun(instead, 'pixel', {prompt: 'Never mind, leave the enemies as they are.' + PORTAL});
  assert.equal(restarted.clarificationContextForRun(instead.runId), undefined);
  assert.deepEqual(records(dir), []);
});

test('only an owner-authored turn claims the card: a subagent report, cron, workers and other sessions leave it pending', async t => {
  const dir = stateDir(t), store = createPendingClarifications({stateDir: dir});
  for (const [prompt, expected] of [['Increase them by 20% instead', false], [FIELD_ANSWER, true]]) {
    const guard = createToolLoopGuard({pendingClarifications: store});
    const asked = await askingRun(guard, `ask-${expected}`, REQUEST);
    // A later attempt of the asking run, a child's announce continuation
    // (trigger user in the owner session), a cron turn, a non-Portal session
    // and a team worker in the same chat leave the card pending.
    const child = 'agent:pixel:subagent:22222222-2222-4333-8444-555555555555';
    const announce = `announce:v1:${child}:run-${expected}`;
    guard.observeRun(asked, 'pixel', {prompt: FIELD_ANSWER + PORTAL});
    guard.observeRun(context(announce, {inputProvenance: {kind: 'inter_session',
      sourceTool: 'subagent_announce', sourceSessionKey: child}}), 'pixel', {prompt: 'Child result: world.js holds ROSTER_SCALE.',
      messages: [{role: 'user', content: REQUEST + PORTAL}]});
    guard.observeRun(context('cron', {trigger: 'cron'}), 'pixel', {prompt: FIELD_ANSWER + PORTAL});
    guard.observeRun(context('main', {sessionKey: 'agent:pixel:main'}), 'pixel', {prompt: FIELD_ANSWER + PORTAL});
    guard.observeRun(context('worker'), 'pixel', {prompt: "You are the Reviewer in the owner's Portal team.\n" + FIELD_ANSWER});
    // A worker whose role is in its history, with the exact answer as its prompt.
    guard.observeRun(context('worker-history'), 'pixel', {prompt: FIELD_ANSWER + PORTAL,
      messages: [{role: 'user', content: "You are the Reviewer in the owner's Portal team.\nCheck the change."}]});
    for (const runId of [asked.runId, announce, 'cron', 'main', 'worker', 'worker-history']) {
      assert.equal(guard.clarificationContextForRun(runId), undefined, `${runId} never claims the card`);
    }
    assert.equal(records(dir).length, 1);
    const next = context(`next-${expected}`);
    guard.observeRun(next, 'pixel', {prompt: prompt + PORTAL});
    assert.equal(Boolean(guard.clarificationContextForRun(next.runId)), expected, prompt);
    ended(guard, next);
    assert.deepEqual(records(dir), [], 'the card ends with the next owner turn');
    const late = context(`late-${expected}`);
    guard.observeRun(late, 'pixel', {prompt: FIELD_ANSWER + PORTAL});
    assert.equal(guard.clarificationContextForRun(late.runId), undefined);
  }
  // No card is recorded for a team worker, a cron run, a cancelled run or without a store.
  const result = await createAskUserTool().execute('ask', {questions: QUESTIONS});
  for (const [prompt, extra] of [["You are the Builder in the owner's Portal team.\nBuild it.", {}], [REQUEST, {trigger: 'cron'}]]) {
    const guard = createToolLoopGuard({pendingClarifications: store}), ctx = context('not-owner', extra);
    guard.observeRun(ctx, 'pixel', {prompt});
    guard.afterToolCall({toolName: 'pixel_ods_ask_user', result}, {...ctx, toolCallId: 'ask'});
    guard.beforeAgentFinalize({lastAssistantMessage: '?'}, ctx);
    assert.deepEqual(records(dir), [], prompt);
  }
  const cancelling = createToolLoopGuard({pendingClarifications: store, abortRunAndDrain: async () => ({aborted: true, drained: true})});
  const cancelledAsk = context('cancelled-ask');
  cancelling.observeRun(cancelledAsk, 'pixel', {prompt: REQUEST + PORTAL});
  assert.equal(await cancelling.abortUserRun(USER), true);
  cancelling.afterToolCall({toolName: 'pixel_ods_ask_user', result}, {...cancelledAsk, toolCallId: 'ask'});
  assert.deepEqual(records(dir), [], 'a stopped run leaves no card');
  await askingRun(createToolLoopGuard(), 'no-store', REQUEST);
  assert.deepEqual(records(dir), []);
  // A cancel of the answering run withdraws the note with the rest of the
  // turn; the card stays, so resending the same answer is still understood.
  // That resend is not told its own text is withdrawn; a changed answer, or
  // any other message, still is.
  const changed = answersMessage(QUESTIONS, {...ANSWERS, meaning: 'Enemy health / damage'});
  for (const [index, [resend, withdrawnNote]] of [[FIELD_ANSWER, undefined], [changed, OWNER_CANCELLED_REQUEST_CONTEXT]].entries()) {
    const guard = createToolLoopGuard({pendingClarifications: store, abortRunAndDrain: async () => ({aborted: true, drained: true})});
    await askingRun(guard, `ask-cancel-${index}`, REQUEST);
    const cancelled = context(`answer-cancel-${index}`);
    guard.observeRun(cancelled, 'pixel', {prompt: FIELD_ANSWER + PORTAL});
    assert.ok(guard.clarificationContextForRun(cancelled.runId));
    assert.equal(await guard.abortUserRun(USER), true);
    ended(guard, cancelled, false);
    assert.equal(guard.clarificationContextForRun(cancelled.runId), undefined);
    assert.equal(records(dir).length, 1);
    const again = context(`answer-again-${index}`);
    guard.observeRun(again, 'pixel', {prompt: resend + PORTAL});
    assert.equal(guard.promptContextForRun(again.runId), withdrawnNote, resend);
    assert.equal(guard.clarificationContextForRun(again.runId), FIELD_CONTEXT, resend);
    ended(guard, again);
    assert.deepEqual(records(dir), []);
  }
  // A different message after the stop consumes the card and keeps the cancel note.
  const guard = createToolLoopGuard({pendingClarifications: store, abortRunAndDrain: async () => ({aborted: true, drained: true})});
  await askingRun(guard, 'ask-cancel-other', REQUEST);
  const cancelled = context('answer-cancel-other');
  guard.observeRun(cancelled, 'pixel', {prompt: FIELD_ANSWER + PORTAL});
  assert.equal(await guard.abortUserRun(USER), true);
  ended(guard, cancelled, false);
  const instead = context('instead-after-cancel');
  guard.observeRun(instead, 'pixel', {prompt: 'Leave the enemies alone.' + PORTAL});
  assert.equal(guard.promptContextForRun(instead.runId), OWNER_CANCELLED_REQUEST_CONTEXT);
  assert.equal(guard.clarificationContextForRun(instead.runId), undefined);
  assert.deepEqual(records(dir), []);
});

test('every delivered card is recorded: the tool, its Tool Search form and the requested-choice repair', async t => {
  const name = 'pixel_ods_ask_user', id = `openclaw:pixel-ods:${name}`;
  const result = await createAskUserTool().execute('ask', {questions: QUESTIONS});
  const wrapped = {toolName: 'tool_call', params: {id, args: {questions: QUESTIONS}},
    result: {details: {tool: {id, source: 'openclaw', sourceName: 'pixel-ods', name}, result}}};
  const request = 'Me pergunte qual cor com opções para escolher.';
  const text = 'Qual cor principal você prefere?\n\n- Azul\n- Roxo\n- Vermelho';
  const repaired = [{id: 'preference', question: 'Qual cor principal você prefere?', options: ['Azul', 'Roxo', 'Vermelho']}];
  for (const [prompt, ask, questions] of [
    [REQUEST, (guard, ctx) => guard.afterToolCall(wrapped, {...ctx, toolCallId: 'ask'}), QUESTIONS],
    [request, (guard, ctx) => guard.beforeAgentFinalize({lastAssistantMessage: text}, ctx), repaired],
  ]) {
    const guard = createToolLoopGuard({pendingClarifications: createPendingClarifications({stateDir: stateDir(t)})});
    const asking = context(`ask-${questions[0].id}`);
    guard.observeRun(asking, 'pixel', {prompt: prompt + PORTAL});
    ask(guard, asking);
    assert.deepEqual(guard.deliveryVerificationForRun(asking.runId).questions, questions);
    const answer = context(`answer-${questions[0].id}`);
    guard.observeRun(answer, 'pixel', {prompt: answersMessage(questions, {[questions[0].id]: questions[0].options[1], meaning: ANSWERS.meaning}) + PORTAL});
    assert.equal(guard.clarificationContextForRun(answer.runId), clarificationAnswerContext({request: prompt, earlierAnswers: []}));
  }
});

test('a follow-up card keeps the original request, carries the earlier answers and survives its asking run', async t => {
  const dir = stateDir(t), store = createPendingClarifications({stateDir: dir});
  const guard = createToolLoopGuard({pendingClarifications: store});
  await askingRun(guard, 'ask-one', REQUEST, QUESTIONS.slice(0, 1));
  const first = answersMessage(QUESTIONS.slice(0, 1), ANSWERS);
  const two = await askingRun(guard, 'ask-two', first, QUESTIONS.slice(1));
  assert.equal(guard.clarificationContextForRun('ask-two'), FIELD_CONTEXT, 'the first answer ran with its request');
  ended(guard, two);
  assert.equal(records(dir).length, 1, 'settling the first card left the follow-up pending');
  const last = context('answer-two');
  guard.observeRun(last, 'pixel', {prompt: answersMessage(QUESTIONS.slice(1), ANSWERS) + PORTAL});
  assert.equal(guard.clarificationContextForRun(last.runId), FIELD_CONTEXT.replace(
    'Original request:\nIncrease enemies by 10%\n',
    `Original request:\nIncrease enemies by 10%\nEarlier clarification answers:\n${first}\n`));
});

test('deleting a conversation removes its pending card with the transcripts', async t => {
  const dir = stateDir(t), store = createPendingClarifications({stateDir: dir});
  const sessions = fs.mkdtempSync(path.join(tmpdir(), 'ods-clarify-sessions-'));
  t.after(() => fs.rmSync(sessions, {recursive: true, force: true}));
  const lifecycle = createConversationImageLifecycle({readConfig: () => ({}), resolveStorePath: () => path.join(sessions, 'sessions.json'),
    getSessionEntry: () => null, patchSessionEntry: async () => {}, callGateway: async () => assert.fail('no native session'),
    compactor: {withMaintenance: async (_user, fn) => fn()}, forgetConversation: key => store.forget(key)});
  const kept = `agent:pixel:openai-user:ods-${'e'.repeat(64)}`;
  await askingRun(createToolLoopGuard({pendingClarifications: store}), 'unanswered', REQUEST);
  assert.equal(store.remember({sessionKey: kept, runId: 'other-chat', questions: QUESTIONS, request: 'Keep this chat'}), true);
  assert.equal(records(dir).length, 2);
  assert.deepEqual(await lifecycle.purge(USER), {schemaVersion: 1, deleted: true});
  assert.equal(records(dir).length, 1, 'only that chat');
  assert.equal(store.claim(kept, () => true).request, 'Keep this chat');
  // A retried deletion of the already deleted chat still clears a late copy.
  assert.equal(store.remember({sessionKey: SESSION_KEY, runId: 'late', questions: QUESTIONS, request: REQUEST}), true);
  assert.deepEqual(await lifecycle.purge(USER), {schemaVersion: 1, deleted: true});
  assert.equal(store.claim(SESSION_KEY, () => true), null);
});

test('a deletion that cannot remove the pending card fails closed and is not recorded as done', async t => {
  const dir = stateDir(t), store = createPendingClarifications({stateDir: dir});
  const sessions = fs.mkdtempSync(path.join(tmpdir(), 'ods-clarify-sessions-'));
  t.after(() => fs.rmSync(sessions, {recursive: true, force: true}));
  let unavailable = true;
  const lifecycle = createConversationImageLifecycle({readConfig: () => ({}), resolveStorePath: () => path.join(sessions, 'sessions.json'),
    getSessionEntry: () => null, patchSessionEntry: async () => {}, callGateway: async () => assert.fail('no native session'),
    compactor: {withMaintenance: async (_user, fn) => fn()},
    forgetConversation: key => { if (unavailable) throw new Error('clarification store unavailable'); return store.forget(key); }});
  const custody = path.join(sessions, '.ods-image-custody', `${USER}.json`);
  assert.equal(store.remember({sessionKey: SESSION_KEY, runId: 'ask', questions: QUESTIONS, request: REQUEST}), true);
  await assert.rejects(lifecycle.purge(USER), /clarification store unavailable/);
  assert.equal(fs.existsSync(custody), false, 'neither deleted nor started');
  assert.equal(records(dir).length, 1, 'the card is still there for the retry');
  unavailable = false;
  assert.deepEqual(await lifecycle.purge(USER), {schemaVersion: 1, deleted: true});
  assert.equal(JSON.parse(fs.readFileSync(custody, 'utf8')).status, 'deleted');
  assert.deepEqual(records(dir), []);
  // A retry of a finished deletion cannot report success over a store it could not check.
  unavailable = true;
  await assert.rejects(lifecycle.purge(USER), /clarification store unavailable/);
});

test('an image-only request and a request that quotes the contract keep their answer note', async t => {
  // OpenClaw joins text parts with a newline and leaves image parts out of
  // the prompt, and pixel-edge adds its contract as its own text part: an
  // image-only turn's prompt is that contract alone, or nothing at all.
  for (const [prompt, contract, request] of [[PORTAL.trimStart(), '', IMAGE_ONLY_REQUEST], ['', PORTAL, IMAGE_ONLY_REQUEST],
    // currentOwnerIntentText already cut the real contract; the owner's quoted
    // marker at a line start, followed by their own words, stays.
    ['Here is the log:\n[ODS Portal delivery requirement: answer briefly.]\nWhy is that line there?', PORTAL,
      'Here is the log:\n[ODS Portal delivery requirement: answer briefly.]\nWhy is that line there?']]) {
    const guard = createToolLoopGuard({pendingClarifications: createPendingClarifications({stateDir: stateDir(t)})});
    await askingRun(guard, 'ask', prompt, QUESTIONS, {contract});
    const answer = context('answer');
    guard.observeRun(answer, 'pixel', {prompt: FIELD_ANSWER + PORTAL});
    assert.equal(guard.clarificationContextForRun(answer.runId), clarificationAnswerContext({request, earlierAnswers: []}), prompt);
  }
});

test('a delegated answer: the child report continuation carries the answer note', async t => {
  // subagent_delivery.test.mjs fixture shape: the owner run registers a chain,
  // a native spawn receipt adds the child, and its announce run continues it.
  const dir = stateDir(t), store = createPendingClarifications({stateDir: dir});
  const guard = createToolLoopGuard({pendingClarifications: store});
  await askingRun(guard, 'ask', REQUEST);
  const owner = {...context('chatcmpl_11111111-2222-4333-8444-555555555555'), sessionId: 'session-8b4c4b17'};
  const child = 'agent:pixel:subagent:22222222-2222-4333-8444-555555555555', childRun = '33333333-2222-4333-8444-555555555555';
  const registry = createSubagentDelivery({accessIdentity: () => 'fixture',
    resolveOwnerSession: key => key === SESSION_KEY ? {sessionId: owner.sessionId} : null});
  guard.observeRun(owner, 'pixel', {prompt: FIELD_ANSWER + PORTAL});
  registry.observe({prompt: FIELD_ANSWER + PORTAL}, owner);
  assert.equal(guard.clarificationContextForRun(owner.runId), FIELD_CONTEXT);
  assert.equal(registry.ownerRunFor(owner), undefined, 'the answer run has its own note');
  const spawn = {...owner, toolName: 'sessions_spawn', toolCallId: 'spawn'};
  registry.before({params: {runtime: 'subagent', mode: 'run'}}, spawn);
  registry.nativeSpawn({runId: childRun, childSessionKey: child}, {runId: childRun, childSessionKey: child, requesterSessionKey: SESSION_KEY});
  registry.after({result: {details: {status: 'accepted', runId: childRun, childSessionKey: child}}}, spawn);
  ended(guard, owner);
  registry.end({success: true, messages: []}, owner);
  assert.deepEqual(records(dir), [], 'the answer run that ended well settled its card');
  const announce = {...owner, runId: `announce:v1:${child}:${childRun}`,
    inputProvenance: {kind: 'inter_session', sourceTool: 'subagent_announce', sourceSessionKey: child}};
  assert.equal(registry.admission(announce), undefined);
  const report = {prompt: 'Child result: world.js holds ROSTER_SCALE = 1.3.'};
  guard.observeRun(announce, 'pixel', report);
  registry.observe(report, announce);
  assert.equal(guard.clarificationContextForRun(announce.runId), undefined, 'the continuation never claims a card');
  assert.equal(registry.ownerRunFor(announce), owner.runId);
  assert.equal(guard.clarificationContextForRun(registry.ownerRunFor(announce)), FIELD_CONTEXT);
  assert.equal(registry.ownerRunFor({...announce, sessionId: 'another-session'}), undefined, 'another native session ends that custody');
  assert.equal(registry.ownerRunFor(announce), undefined);
  // The real plugin entry test below drives the same path through index.js.
});

// Run the same two owner turns through a guard with and without the store and
// compare everything the owner text decides: the owner event, its prompt
// contract, tool decisions, finalization and delivery.
async function ownerTurnTrace(store, {request, questions, answers, history = false, workspace, before, calls = [], finalText}) {
  const guard = createToolLoopGuard(store ? {pendingClarifications: store} : {}), capabilities = workspace?.();
  await askingRun(guard, 'ask', request, questions, {capabilities, before});
  const answerText = answersMessage(questions, answers) + PORTAL;
  const event = {prompt: answerText, messages: history ? [
    {role: 'user', content: request + PORTAL},
    {role: 'assistant', content: [{type: 'toolCall', id: 'ask', name: 'pixel_ods_ask_user', arguments: {questions}}]},
    {role: 'toolResult', toolCallId: 'ask', toolName: 'pixel_ods_ask_user', content: [{type: 'text', text: 'Wait for the owner to answer.'}]},
    {role: 'user', content: answerText},
  ] : []};
  const ctx = context('answer');
  guard.observeRun(ctx, 'pixel', event, capabilities);
  const ownerEvent = guard.ownerIntentEventForRun(ctx.runId, event);
  const trace = {
    ownerEvent, contract: promptContractForAgent(ctx, 'pixel', ownerEvent), prepended: guard.promptContextForRun(ctx.runId) ?? null,
    decisions: calls.map((call, index) => guard.beforeToolCall({toolCallId: `call-${index}`, ...call}, {...ctx, toolCallId: `call-${index}`}) ?? null),
    finalize: guard.beforeAgentFinalize({lastAssistantMessage: finalText ?? 'Done.'}, ctx) ?? null,
    delivery: guard.deliveryVerificationForRun(ctx.runId),
  };
  return {trace, note: guard.clarificationContextForRun(ctx.runId)};
}
async function assertSameOwnerTurn(t, scenario) {
  const plain = await ownerTurnTrace(undefined, scenario);
  const stored = await ownerTurnTrace(createPendingClarifications({stateDir: stateDir(t)}), scenario);
  assert.equal(plain.note, undefined);
  assert.equal(stored.note, clarificationAnswerContext({request: scenario.request, earlierAnswers: []}));
  assert.deepEqual(stored.trace, plain.trace);
  assert.ok(!JSON.stringify(stored.trace).includes('ODS Portal note'), 'the note never reaches owner text');
  return stored.trace;
}

test('owner-intent classifiers see the field answer exactly as today, in a new or the same session', async t => {
  for (const history of [false, true]) {
    const trace = await assertSameOwnerTurn(t, {request: REQUEST, questions: QUESTIONS, answers: ANSWERS, history,
      calls: [{toolName: 'read', params: {path: 'Playground/elf-of-doom/world.js'}},
        {toolName: 'edit', params: {path: 'Playground/elf-of-doom/world.js', oldText: 'ROSTER_SCALE = 1.3', newText: 'ROSTER_SCALE = 1.43'}}]});
    assert.equal(trace.ownerEvent.prompt, FIELD_ANSWER + PORTAL);
  }
});

test('a declined delete stays unauthorized: the carried request never reaches the delete guard', async t => {
  const request = 'Delete the folder /workspace/Playground/old recursively';
  const questions = [{id: 'confirm', question: 'Should I permanently delete /workspace/Playground/old and everything in it?',
    options: ['Yes, delete it', 'No, keep it']}];
  const answer = answersMessage(questions, {confirm: 'No, keep it'});
  assert.equal(userMessageAuthorizesRecursiveDelete([], request), true, 'the request alone would authorize it');
  assert.equal(userMessageAuthorizesRecursiveDelete([], answer + PORTAL), false);
  // Why the request stays out of owner text: the refuted in-text frame authorized it.
  assert.equal(userMessageAuthorizesRecursiveDelete([], `Original request:\n${request}\n\n${answer}`), true);
  const trace = await assertSameOwnerTurn(t, {request, questions, answers: {confirm: 'No, keep it'},
    calls: [{toolName: 'exec', params: {command: 'rm -rf /workspace/Playground/old'}}]});
  assert.equal(trace.decisions[0].blockReason, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
});

test('an answered "ask me questions with options" request does not reopen choice-question intent', async t => {
  const request = 'Build me a choose your own adventure game. Ask me a few questions with options first.';
  const questions = [{id: 'theme', question: 'Which theme do you prefer?', options: ['Fantasy', 'Space']}];
  const answer = answersMessage(questions, {theme: 'Space'});
  assert.equal(requestsChoiceQuestion(request), true);
  assert.equal(requestsChoiceQuestion(answer), false);
  assert.equal(requestsChoiceQuestion(`Original request:\n${request}\n\n${answer}`), true, 'the refuted in-text frame reopened it');
  // The narrow text repair needs ownerQuestionIntent; it stays off as today.
  const trace = await assertSameOwnerTurn(t, {request, questions, answers: {theme: 'Space'},
    finalText: 'Which ending do you prefer?\n- Happy\n- Sad\n- Open'});
  assert.notEqual(trace.delivery.status, 'pending');
});

test('Playground keeps the project the request created when its card is answered', async t => {
  const roots = [];
  const workspace = () => {
    const root = fs.mkdtempSync(path.join(tmpdir(), 'ods-clarify-playground-'));
    t.after(() => fs.rmSync(root, {recursive: true, force: true}));
    roots.push(root);
    return {workspaceRoot: root};
  };
  const write = (guard, ctx) => {
    const params = {path: 'snake/index.html', content: '<!doctype html><title>Snake</title>'};
    const decision = guard.beforeToolCall({toolName: 'write', toolCallId: 'write-index', params}, {...ctx, toolCallId: 'write-index'});
    assert.equal(decision.params.path, 'Playground/snake/index.html');
    const root = roots.at(-1);
    fs.writeFileSync(path.join(root, decision.params.path), decision.params.content);
    guard.afterToolCall({toolName: 'write', toolCallId: 'write-index', params: decision.params,
      result: {content: [{type: 'text', text: 'File written'}]}}, {...ctx, toolCallId: 'write-index'});
  };
  const request = 'Build me a snake game';
  const questions = [{id: 'sound', question: 'Should the game have sound effects?', options: ['Yes, add sound', 'No sound']}];
  const answer = answersMessage(questions, {sound: 'Yes, add sound'});
  assert.equal(requestsNewPlaygroundProject(request), true);
  assert.equal(requestsNewPlaygroundProject(answer), false);
  // The refuted in-text frame reserved a second folder (Playground/snake-2).
  assert.equal(requestsNewPlaygroundProject(`Original request:\n${request}\n\n${answer}`), true);
  const trace = await assertSameOwnerTurn(t, {request, questions, answers: {sound: 'Yes, add sound'}, workspace, before: [write],
    calls: [{toolName: 'write', params: {path: 'sound.js', content: 'export const beep = () => {};'}}]});
  assert.equal(trace.decisions[0].params.path, 'Playground/snake/sound.js');
  assert.equal(roots.length, 2);
  for (const root of roots) assert.deepEqual(fs.readdirSync(path.join(root, 'Playground')), ['snake']);
});

// The real plugin entry with only the OpenClaw SDK boundary replaced (the
// tool-grammar-registration.mjs pattern), registered for a gateway and driven
// through its hooks in the pinned runtime's order. Each load is a fresh
// index.js instance: a gateway restart for every registry it owns.
let pluginLoads = 0;
async function loadPlugin(sessions, home, sdkOverrides = {}) {
  // Each gateway instance gets its own home. On POSIX the access runtime keeps
  // an admission lock there, and a second instance in this same live process
  // would refuse it as held by a running owner. It also needs the .openclaw
  // parent to exist.
  fs.mkdirSync(path.join(home, '.openclaw'), {recursive: true, mode: 0o700});
  process.env.HOME = home;
  process.env.USERPROFILE = home;
  const entry = new URL('../plugin/index.js', import.meta.url);
  const source = fs.readFileSync(entry, 'utf8').replace(/from\s+(['"])([^'"]+)\1/g, (match, _quote, specifier) =>
    specifier.startsWith('.') ? `from ${JSON.stringify(new URL(specifier, entry).href)}` : match);
  // Only these SDK calls are expected; any other fails the test.
  const sdk = {getSessionEntry: () => undefined, resolveStorePath: () => path.join(sessions, 'sessions.json'),
    resolveActiveEmbeddedRunSessionId: () => undefined, resolveAgentWorkspaceDir: () => undefined, resolveUserPath: value => value,
    ...sdkOverrides};
  globalThis.__odsClarificationSdk = (name, args) => {
    if (!Object.hasOwn(sdk, name)) throw new Error(`Unexpected SDK call: ${name}`);
    return sdk[name](...args);
  };
  const stubbed = source.replace(/import\s*\{([^}]+)\}\s*from\s*['"]openclaw\/plugin-sdk\/[^'"]+['"];?/g, (_match, names) =>
    names.split(',').map(name => name.trim()).filter(Boolean).map(name => name === 'definePluginEntry'
      ? 'const definePluginEntry = entry => entry;' : name === 'OPENCLAW_VERSION' ? 'const OPENCLAW_VERSION = "2026.6.33";'
        : `const ${name} = (...args) => globalThis.__odsClarificationSdk(${JSON.stringify(name)}, args);`).join('\n'));
  assert.doesNotMatch(stubbed, /from\s*['"]openclaw\//, 'all SDK imports are isolated');
  const {default: plugin} = await import(`data:text/javascript;base64,${Buffer.from(
    `${stubbed}\n//# sourceURL=${entry.href}?clarification-${++pluginLoads}`).toString('base64')}`);
  const hooks = new Map(), tools = new Map();
  plugin.register({registrationMode: 'full', pluginConfig: {perplexicaPort: 1}, logger: {warn() {}, info() {}},
    config: {agents: {list: [{id: 'pixel', sandbox: {mode: 'off'}, tools: {exec: {host: 'gateway'}}}]}},
    on: (name, handler) => hooks.set(name, [...(hooks.get(name) ?? []), handler]),
    registerHttpRoute() {}, registerTool: (factory, options) => tools.set(options?.names?.[0], factory)});
  const fire = async (name, event, ctx) => {
    const results = [];
    for (const handler of hooks.get(name) ?? []) results.push(await handler(event, ctx));
    return results.filter(Boolean);
  };
  // OpenClaw resolvePromptBuildHookResult: context fields of every handler, joined.
  const promptBuild = async (event, ctx) => {
    const results = await fire('before_prompt_build', event, ctx);
    const joined = key => results.map(result => result[key]).filter(Boolean).join('\n\n') || undefined;
    return {prependContext: joined('prependContext'), appendContext: joined('appendContext'),
      prependSystemContext: joined('prependSystemContext'), appendSystemContext: joined('appendSystemContext'),
      systemPrompt: results.map(result => result.systemPrompt).find(Boolean)};
  };
  return {tools, fire, promptBuild};
}

test('the real plugin entry: the note goes beside the owner turn only and the run that ends well settles it', async t => {
  const root = fs.mkdtempSync(path.join(tmpdir(), 'ods-clarify-plugin-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const sessions = path.join(root, 'sessions'), dir = path.join(root, 'state', '.ods-pending-clarifications');
  fs.mkdirSync(sessions, {recursive: true, mode: 0o700});
  // index.js keeps one store for the process, under the OpenClaw state
  // directory. Every other state root the full registration opens (access
  // runtime, exec control) follows the home directory: keep both disposable.
  for (const [name, value] of [['OPENCLAW_STATE_DIR', path.join(root, 'state')], ['HOME', root], ['USERPROFILE', root]]) {
    const previous = process.env[name];
    process.env[name] = value;
    t.after(() => { if (previous === undefined) delete process.env[name]; else process.env[name] = previous; });
  }
  let gateways = 0;
  const gateway = sdk => loadPlugin(sessions, path.join(root, `home-${++gateways}`), sdk);
  const runId = n => `chatcmpl_00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
  const turn = (n, sessionId) => ({agentId: 'pixel', runId: runId(n), sessionId, sessionKey: SESSION_KEY, trigger: 'user'});
  async function ask(host, ctx, prompt = REQUEST) {
    const event = {prompt: prompt + PORTAL, messages: []};
    const built = await host.promptBuild(event, ctx);
    await host.fire('before_agent_run', event, ctx);
    assert.ok(!String(built.appendContext ?? '').includes('ODS Portal note'), 'no note on the asking turn');
    const toolCtx = {...ctx, toolCallId: 'call_ask'};
    const [decision] = await host.fire('before_tool_call', {toolName: 'pixel_ods_ask_user', toolCallId: 'call_ask', params: {questions: QUESTIONS}}, toolCtx);
    assert.ok(!decision?.block, JSON.stringify(decision));
    const result = await host.tools.get('pixel_ods_ask_user')(ctx).execute('call_ask', {questions: QUESTIONS});
    await host.fire('after_tool_call', {toolName: 'pixel_ods_ask_user', toolCallId: 'call_ask', params: {questions: QUESTIONS}, result, durationMs: 1}, toolCtx);
    const [final] = await host.fire('before_agent_finalize', {runId: ctx.runId, lastAssistantMessage: 'I will make the change once you answer.'}, ctx);
    assert.equal(final?.action, 'finalize');
    await host.fire('agent_end', {runId: ctx.runId, success: true, messages: []}, ctx);
    return built.appendContext;
  }
  async function answer(host, ctx, prompt = FIELD_ANSWER, success = true, during = async () => {}) {
    const event = {prompt: prompt + PORTAL, messages: []}, before = structuredClone(event);
    const built = await host.promptBuild(event, ctx);
    await host.fire('before_agent_run', event, ctx);
    assert.deepEqual(event, before, 'the hooks never rewrite the owner event');
    assert.equal(built.prependContext, undefined, 'nothing is prepended to the owner turn');
    assert.ok(!String(built.appendSystemContext ?? '').includes(REQUEST) && !built.systemPrompt, 'the cached system prompt is unchanged');
    await during(ctx);
    await host.fire('agent_end', {runId: ctx.runId, success, messages: []}, ctx);
    return built.appendContext;
  }

  // Asked, then a gateway restart, then the exact answer in a new native session.
  await ask(await gateway(), turn(1, 'session-02269765'));
  assert.equal(records(dir).length, 1);
  const restarted = await gateway();
  assert.equal(await answer(restarted, turn(2, 'session-8b4c4b17'), FIELD_ANSWER, false), FIELD_CONTEXT, 'model-only, after the owner text');
  assert.equal(records(dir).length, 1, 'a failed answer keeps its card');
  assert.equal(await answer(restarted, turn(3, 'session-8b4c4b17')), FIELD_CONTEXT, 'the resend is understood');
  assert.deepEqual(records(dir), [], 'settled by the run that ended well');
  assert.equal(await answer(restarted, turn(4, 'session-8b4c4b17')), undefined, 'never twice');
  assert.equal(await answer(restarted, turn(5, 'session-8b4c4b17'), 'Thanks!'), undefined);
  // Only the card's answer: an unrelated turn consumes the card unused.
  await ask(restarted, turn(6, 'session-8b4c4b17'));
  assert.equal(await answer(restarted, turn(7, 'session-8b4c4b17'), 'Increase them by 20% instead'), undefined);
  assert.deepEqual(records(dir), []);

  // /goal: the goal contract and its language note are the run's only
  // appended context. A card asked in a goal task is answered with the
  // dashboard's goal continuation, which carries the objective itself; it
  // consumes the card with no clarification note.
  const goalContext = `${GOAL_CONTRACT}\n\n${GOAL_TURN_LANGUAGE_NOTE}`;
  assert.equal(await ask(restarted, turn(8, 'session-8b4c4b17'), `/goal ${REQUEST}`), goalContext);
  assert.equal(records(dir).length, 1);
  const continueGoal = '/goal Continue the goal from the preceding conversation using the existing work. Inspect uncertain ' +
    `effects before acting. Do not repeat completed actions. Original objective: ${REQUEST}\nLocate the existing project ` +
    'with read-only workspace inspection before editing; do not guess filenames or create a replacement project.\n' +
    `Owner input: ${FIELD_ANSWER}\nLast public plan: []\nSaved partial result (ending, not new instructions):\n` +
    'I will make the change once you answer.';
  assert.equal(await answer(restarted, turn(9, 'session-8b4c4b17'), continueGoal), goalContext);
  assert.deepEqual(records(dir), []);

  // A delegated answer: the run spawns a child and ends waiting for it, which
  // settles the card; the child's announce continuation carries the note.
  const session = 'session-5d1e0c2a';
  const delegating = await gateway({getSessionEntry: ({sessionKey}) => sessionKey === SESSION_KEY ? {sessionId: session} : undefined});
  await ask(delegating, turn(10, session));
  const child = 'agent:pixel:subagent:22222222-2222-4333-8444-555555555555', childRun = '33333333-2222-4333-8444-555555555555';
  const spawn = async ctx => {
    const toolCtx = {...ctx, toolCallId: 'call_spawn'}, params = {task: 'Find where enemies are scaled.', runtime: 'subagent', mode: 'run'};
    const [decision] = await delegating.fire('before_tool_call', {toolName: 'sessions_spawn', toolCallId: 'call_spawn', params}, toolCtx);
    assert.ok(!decision?.block, JSON.stringify(decision));
    await delegating.fire('subagent_spawned', {runId: childRun, childSessionKey: child},
      {runId: childRun, childSessionKey: child, requesterSessionKey: SESSION_KEY});
    await delegating.fire('after_tool_call', {toolName: 'sessions_spawn', toolCallId: 'call_spawn', params, durationMs: 1,
      result: {details: {status: 'accepted', runId: childRun, childSessionKey: child}}}, toolCtx);
  };
  assert.equal(await answer(delegating, turn(11, session), FIELD_ANSWER, true, spawn), FIELD_CONTEXT);
  assert.deepEqual(records(dir), [], 'the answer run ended well');
  const announce = {agentId: 'pixel', runId: `announce:v1:${child}:${childRun}`, sessionId: session, sessionKey: SESSION_KEY,
    trigger: 'user', inputProvenance: {kind: 'inter_session', sourceTool: 'subagent_announce', sourceSessionKey: child}};
  const report = await delegating.promptBuild({prompt: 'Child result: world.js holds ROSTER_SCALE = 1.3.', messages: []}, announce);
  assert.match(report.prependContext ?? '', /ODS delegation delivery state: 1\/1/, 'the delivery chain admitted the continuation');
  assert.equal(report.appendContext, FIELD_CONTEXT, 'the continuation keeps the request in view');
});

test('index.js hands conversation deletion the same store the guard records into', t => {
  // Source composition (the access_hook_composition pattern): the production
  // lifecycle construction, with the real store. The lifecycle's own purge
  // path is covered above; its maintenance lease needs the POSIX runtime.
  const source = fs.readFileSync(new URL('../plugin/index.js', import.meta.url), 'utf8');
  const begin = source.indexOf('    conversationImageLifecycle ??= createConversationImageLifecycle({');
  const finish = source.indexOf('    conversationImageLifecycle.register(api);', begin);
  assert.ok(begin >= 0 && finish > begin);
  assert.match(source, /\nconst pendingClarifications = createPendingClarifications\(\);\n/, 'one store per gateway process');
  assert.match(source, /\n {6}pendingClarifications,\n/, 'the guard records into it');
  const dir = stateDir(t), store = createPendingClarifications({stateDir: dir});
  let dependencies;
  vm.runInNewContext(source.slice(begin, finish), {api: {config: {}}, conversationImageLifecycle: undefined,
    createConversationImageLifecycle: value => { dependencies = value; return {}; }, pendingClarifications: store,
    getSessionEntry() {}, patchSessionEntry() {}, resolveStorePath() {}, callGatewayTool() {}, contextCompaction: {}});
  assert.equal(store.remember({sessionKey: SESSION_KEY, runId: 'ask', questions: QUESTIONS, request: REQUEST}), true);
  assert.equal(dependencies.forgetConversation(SESSION_KEY), true);
  assert.deepEqual(records(dir), []);
});
