import test from 'node:test';
import assert from 'node:assert/strict';
import {createRunDeadline, responseDeadlineDelay, RUN_DEADLINE_GRACE_MS, runDeadlineReached, runDeadlineStopText,
  takeRunDeadlineNotice} from '../plugin/run-deadline.mjs';

const START = 1_000_000;
const at = seconds => START + seconds * 1000;

// The model-facing contract, byte for byte (prefix-cache sensitive).
const STAGE_1 = '[ODS Pixel time limit] About 5 minutes remain before Portal stops this response at its 30-minute limit, ' +
  'even in the middle of a change. Do not start new work. If a change across several files is only partly made, ' +
  'finish it now or undo the parts already made, so the saved project still works. Then stop calling tools and ' +
  'reply to the owner: what is done and checked, what is left, and that sending "continue" resumes the rest.';
const STAGE_2 = '[ODS Pixel time limit] About 2 minutes remain before Portal stops this response. Make at most the one ' +
  'edit needed to keep the saved files working together, then stop calling tools and reply to the owner with what ' +
  'is done, what is left, and that sending "continue" resumes the rest.';

test('a deadline needs the positive integer timeout ODS pins', () => {
  assert.deepEqual(createRunDeadline(1800, START), {startedAt: START, attemptStartedAt: START, timeoutMs: 1800000, notices: 0});
  for (const value of [0, -1, 1.5, '1800', undefined, null, NaN, Infinity, 2 ** 60])
    assert.equal(createRunDeadline(value, START), undefined, String(value));
});

test('a later attempt keeps the response start and delivered stages, with its own start', () => {
  const first = createRunDeadline(1800, START);
  first.notices = 1;
  assert.deepEqual(createRunDeadline(1800, at(600), first),
    {startedAt: START, attemptStartedAt: at(600), timeoutMs: 1800000, notices: 1});
  assert.equal(createRunDeadline(undefined, at(600), first), undefined);
});

test('the 30-minute deadline warns at minute 25 and minute 28, once each', () => {
  const deadline = createRunDeadline(1800, START);
  assert.equal(takeRunDeadlineNotice(deadline, at(0)), undefined);
  assert.equal(takeRunDeadlineNotice(deadline, at(1499)), undefined);
  assert.equal(takeRunDeadlineNotice(deadline, at(1500)), STAGE_1);
  assert.equal(takeRunDeadlineNotice(deadline, at(1600)), undefined);
  assert.equal(takeRunDeadlineNotice(deadline, at(1679)), undefined);
  assert.equal(takeRunDeadlineNotice(deadline, at(1680)), STAGE_2);
  for (const seconds of [1700, 1800, 1900]) assert.equal(takeRunDeadlineNotice(deadline, at(seconds)), undefined);
});

test('a later attempt warns on the response clock, not its own', () => {
  const deadline = createRunDeadline(1800, at(600), createRunDeadline(1800, START));
  assert.equal(takeRunDeadlineNotice(deadline, at(1500)), STAGE_1);
});

test('a clock jump past both thresholds sends only the last notice', () => {
  const deadline = createRunDeadline(1800, START);
  assert.match(takeRunDeadlineNotice(deadline, at(1700)), /About 2 minutes remain/);
  assert.equal(takeRunDeadlineNotice(deadline, at(1750)), undefined);
});

test('short timeouts scale the thresholds and name seconds', () => {
  const deadline = createRunDeadline(60, START);
  assert.equal(takeRunDeadlineNotice(deadline, at(49)), undefined);
  assert.match(takeRunDeadlineNotice(deadline, at(50)), /About 10 seconds remain .* at its 1-minute limit/);
  assert.equal(takeRunDeadlineNotice(deadline, at(55)), undefined);
  assert.match(takeRunDeadlineNotice(deadline, at(56)), /About 4 seconds remain/);
});

test('notice bytes depend only on the configured timeout, never the clock', () => {
  const texts = start => {
    const deadline = createRunDeadline(1800, start);
    return [takeRunDeadlineNotice(deadline, start + 1500123), takeRunDeadlineNotice(deadline, start + 1690456)];
  };
  assert.deepEqual(texts(5), texts(987654321));
  assert.doesNotMatch(texts(5).join(), /\d{3,}|:\d\d/);
});

test('the deadline is reached only by a failed attempt that ran its full time without an answer', () => {
  const deadline = createRunDeadline(1800, START);
  const aborted = {success: false, messages: [{role: 'user', content: 'Fix it.'},
    {role: 'assistant', stopReason: 'toolUse', content: [{type: 'toolCall', id: 'edit-7', name: 'edit', arguments: {}}]}]};
  assert.equal(runDeadlineReached(deadline, aborted, at(1800)), true);
  assert.equal(runDeadlineReached(deadline, aborted, at(1800) - RUN_DEADLINE_GRACE_MS), true);
  assert.equal(runDeadlineReached(deadline, aborted, at(1800) - RUN_DEADLINE_GRACE_MS - 1000), false);
  assert.equal(runDeadlineReached(deadline, aborted, at(1200)), false);
  assert.equal(runDeadlineReached(deadline, {...aborted, success: true}, at(1800)), false);
  assert.equal(runDeadlineReached(deadline, {messages: aborted.messages}, at(1800)), false);
  assert.equal(runDeadlineReached(undefined, aborted, at(1800)), false);
  assert.equal(runDeadlineReached(deadline, {success: false}, at(1800)), true);
  // A later attempt has its own runtime timer, unless the guard stopped it at
  // the response's deadline.
  const retry = createRunDeadline(1800, at(600), deadline);
  assert.equal(runDeadlineReached(retry, aborted, at(1800)), false);
  assert.equal(runDeadlineReached(retry, aborted, at(2400)), true);
  assert.equal(runDeadlineReached(retry, aborted, at(1800), true), true);
  assert.equal(runDeadlineReached(retry, {...aborted, success: true}, at(1800), true), false);
  assert.equal(runDeadlineReached(undefined, aborted, at(1800), true), false);
});

// Portal's ingress cuts the whole response off 1920 s after it began.
test('the guard stops a later attempt at the response\'s deadline, and the first after the grace', () => {
  const first = createRunDeadline(1800, START);
  // The first attempt's own timer fires first, unless a compaction extended it.
  assert.equal(responseDeadlineDelay(first, at(0)), 1800000 + RUN_DEADLINE_GRACE_MS);
  assert.equal(responseDeadlineDelay(first, at(1700)), 100000 + RUN_DEADLINE_GRACE_MS);
  // A later attempt's own timer fires 30 minutes after it began.
  const retry = createRunDeadline(1800, at(600), first);
  assert.equal(responseDeadlineDelay(retry, at(600)), 1200000);
  assert.equal(responseDeadlineDelay(retry, at(1799)), 1000);
  // One that begins past the deadline is stopped at once.
  assert.equal(responseDeadlineDelay(createRunDeadline(1800, at(1830), retry), at(1830)), 0);
  assert.equal(responseDeadlineDelay(undefined, at(0)), undefined);
});

test('a completed final answer is OpenClaw\'s to deliver, a cut-off one is not', () => {
  const deadline = createRunDeadline(1800, START);
  const ending = message => ({success: false, messages: [{role: 'user', content: 'Fix it.'}, message,
    {role: 'toolResult', toolCallId: 'x', content: [{type: 'text', text: 'ok'}]}]});
  for (const stopReason of ['stop', 'end_turn', 'completed', ' STOP '])
    for (const responseStopped of [false, true])
      assert.equal(runDeadlineReached(deadline, ending({role: 'assistant', stopReason,
        content: [{type: 'text', text: 'Both hitboxes are fixed and every level loads.'}]}), at(1800), responseStopped), false, stopReason);
  for (const message of [
    {role: 'assistant', stopReason: 'aborted', content: []},
    {role: 'assistant', stopReason: 'toolUse', content: [{type: 'text', text: 'Now the chars.js edit.'}, {type: 'toolCall', id: 'e', name: 'edit'}]},
    {role: 'assistant', stopReason: 'stop', content: []},
    {role: 'assistant', stopReason: 'stop', content: [{type: 'text', text: '<think>still planning</think>'}]},
    {role: 'assistant', stopReason: 'error', content: [{type: 'text', text: 'partial'}]},
  ]) assert.equal(runDeadlineReached(deadline, ending(message), at(1800)), true, JSON.stringify(message));
});

test('owner copy names the limit and the recovery, never configuration', () => {
  const deadline = createRunDeadline(1800, START);
  const changed = runDeadlineStopText(deadline, true);
  assert.equal(changed, '**Portal stopped this response after 30 minutes, the most one response can run.** ' +
    'This request needed more time. Files saved before the stop are kept, but the last change may be unfinished, ' +
    'so the project may not work yet. Send "continue" to pick up where it left off.' +
    ' If it keeps happening, [get help on Discord](https://discord.gg/4ntNp9MAwC).');
  // A response that changed no workspace files has no project to warn about.
  const unchanged = runDeadlineStopText(deadline, false);
  assert.equal(unchanged, '**Portal stopped this response after 30 minutes, the most one response can run.** ' +
    'This request needed more time. Send "continue" to pick up where it left off.' +
    ' If it keeps happening, [get help on Discord](https://discord.gg/4ntNp9MAwC).');
  for (const text of [changed, unchanged]) assert.doesNotMatch(text, /timeoutSeconds|openclaw\.json|config/i);
});
