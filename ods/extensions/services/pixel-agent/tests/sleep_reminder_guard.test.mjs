import test from 'node:test';
import assert from 'node:assert/strict';
import {SLEEP_REMINDER_REASON, waitsToActLater, withSleepReminderBlock} from '../plugin/sleep-reminder-guard.mjs';

const exec = command => ({toolName: 'exec', params: {command}});

test('commands that fake a reminder wait to act later (#7794)', () => {
  for (const command of [
    '(sleep 120 && echo "Time to stretch!") &',
    'sleep 300; echo done',
    'sleep 2m && echo "Tea is ready"',
    'sleep 1h',
    'nohup sh -c "sleep 5; echo hi" >/dev/null',
    'setsid sleep 10 &',
    '{ sleep 30; notify-send hi; } &',
    'echo "sleep 3600 && echo late" | at now',
  ]) assert.equal(waitsToActLater(command), true, command);
});

test('ordinary commands and short waits do not', () => {
  for (const command of [
    'ls -la',
    'sleep 2 && curl -s localhost:3000',
    'npm start > server.log 2>&1 & sleep 3 && curl -s localhost:3000',
    'python3 -m http.server 8000 & sleep 1',
    'sleep 59',
    'make test 2>&1 | tail -5',
    'grep -rn "sleep 120" src/',  // A word in a quoted search is still read as a sleep, but a quoted grep has no &.
  ]) assert.equal(waitsToActLater(command), command === 'grep -rn "sleep 120" src/', command);
});

test('the guard refuses exec, also through the tool_call wrapper, and points at pixel_ods_notify', () => {
  assert.deepEqual(withSleepReminderBlock(undefined, exec('(sleep 120 && echo hi) &'), {toolName: 'exec'}),
    {block: true, blockReason: SLEEP_REMINDER_REASON});
  assert.match(SLEEP_REMINDER_REASON, /pixel_ods_notify/);
  assert.match(SLEEP_REMINDER_REASON, /inMinutes/);
  const wrapped = {toolName: 'tool_call', params: {id: 'exec', args: {command: 'sleep 600'}}};
  assert.equal(withSleepReminderBlock(undefined, wrapped, {toolName: 'tool_call'})?.block, true);
  assert.equal(withSleepReminderBlock(undefined, {toolName: 'tool_call', params: {id: 'read', args: {command: 'sleep 600'}}}, {}), undefined);
});

test('an earlier block is kept, a rewrite is checked, and other tools pass', () => {
  const earlier = {block: true, blockReason: 'owner authorization missing'};
  assert.equal(withSleepReminderBlock(earlier, exec('sleep 600'), {toolName: 'exec'}), earlier);
  const rewrite = {params: {command: 'sleep 600'}};
  assert.equal(withSleepReminderBlock(rewrite, exec('ls'), {toolName: 'exec'})?.block, true);
  // The cancellation wrapper hides the command in base64; the owner's own call still shows it.
  const wrapped = {params: {command: `/run/pixel-ods-control/cancellable-exec.sh run-1 ${Buffer.from('(sleep 120 && echo hi) &').toString('base64')}`}};
  assert.equal(withSleepReminderBlock(wrapped, exec('(sleep 120 && echo hi) &'), {toolName: 'exec'})?.block, true);
  const quick = {params: {command: '/run/pixel-ods-control/cancellable-exec.sh run-2 bHM='}};
  assert.equal(withSleepReminderBlock(quick, exec('ls'), {toolName: 'exec'}), quick);
  assert.equal(withSleepReminderBlock(undefined, {toolName: 'write', params: {command: 'sleep 600'}}, {toolName: 'write'}), undefined);
  assert.equal(withSleepReminderBlock(undefined, {toolName: 'exec', params: {code: 'x'}}, {toolName: 'exec'}), undefined);
});
