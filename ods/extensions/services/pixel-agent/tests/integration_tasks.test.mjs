import test from 'node:test';
import assert from 'node:assert/strict';
import { createTaskHooks, REMINDER_HINT, REMINDER_MARK, reminderHint, reminderJob } from '../plugin/integration-tasks.mjs';

function recorder(replies) {
  const sent = [];
  const submit = async payload => {
    sent.push(payload);
    const reply = typeof replies === 'function' ? replies(payload) : replies[payload.action];
    if (reply instanceof Error) throw reply;
    return reply;
  };
  return {sent, submit};
}

test('finished runs of Portal tasks reach the service; other events do not', async () => {
  const {sent, submit} = recorder({'task-finished': {ok: true, notified: true}});
  const warnings = [];
  const hooks = createTaskHooks({submit, warn: message => warnings.push(message)});
  await hooks.cronChanged({action: 'started', jobId: 'j1'});
  await hooks.cronChanged({action: 'finished', jobId: 'j2', agentId: 'other', status: 'ok'});
  await hooks.cronChanged({action: 'finished', jobId: 'j3', agentId: 'pixel', status: 'error', error: 'Disk full',
    summary: 'x'.repeat(5000), runAtMs: 42, job: {name: 'Backup', description: 'Nightly'}});
  await hooks.cronChanged({action: 'finished', jobId: 'j4', status: 'ok', job: {}});
  assert.deepEqual(sent.map(item => [item.jobId, item.name, item.description, item.status, item.runAtMs, item.summary.length]),
    [['j3', 'Backup', 'Nightly', 'error', 42, 4000], ['j4', '', '', 'ok', 0, 0]]);
  assert.deepEqual(warnings, []);
  const failing = createTaskHooks({submit: async () => { throw new Error('down'); }, warn: message => warnings.push(message)});
  await failing.cronChanged({action: 'finished', jobId: 'j5', status: 'ok'});
  const refusing = createTaskHooks({submit: async () => ({ok: false, message: 'notifications off'}), warn: message => warnings.push(message)});
  await refusing.cronChanged({action: 'finished', jobId: 'j6', status: 'ok'});
  assert.deepEqual(warnings, ['Portal integrations: scheduled task notice failed: down',
    'Portal integrations: scheduled task notice failed: notifications off']);
});

function fakeCron(jobs = []) {
  const calls = [];
  return {
    calls, jobs,
    list: async options => { calls.push(['list', options]); return jobs; },
    add: async job => { calls.push(['add', job]); jobs.push({id: `j${jobs.length + 1}`, ...job}); return jobs.at(-1); },
    update: async (id, patch) => { calls.push(['update', id, patch]); },
    remove: async id => { calls.push(['remove', id]); return {removed: true}; },
  };
}

const PLAN = {enabled: true, name: 'Daily news digest', description: 'Created by Portal integrations for the daily news digest.',
  expr: '30 6 * * *', tz: 'Europe/Lisbon', message: 'Read the news digest.', timeoutSeconds: 600,
  tools: ['pixel_ods_mcp']};

test('the digest job is added, kept, updated and removed to follow the settings', async () => {
  let plan = PLAN;
  const {submit} = recorder(() => ({ok: true, plan}));
  const cron = fakeCron([{id: 'other', description: 'Someone else', schedule: {kind: 'cron', expr: '0 9 * * *'}}]);
  const hooks = createTaskHooks({submit, timers: {setInterval: () => ({}), clearInterval: () => {}}});
  assert.equal(await hooks.syncDigest(), 'no-cron');
  await hooks.gatewayStart({}, {getCron: () => cron});
  assert.deepEqual(cron.calls.at(-1), ['add', {name: PLAN.name, description: PLAN.description, enabled: true, agentId: 'pixel',
    schedule: {kind: 'cron', expr: '30 6 * * *', tz: 'Europe/Lisbon'}, sessionTarget: 'isolated', wakeMode: 'now',
    payload: {kind: 'agentTurn', message: PLAN.message, timeoutSeconds: 600, lightContext: true, toolsAllow: ['pixel_ods_mcp']},
    delivery: {mode: 'none'}, deleteAfterRun: false}]);
  assert.equal(await hooks.syncDigest(), 'kept');
  plan = {...PLAN, expr: '0 7 * * *'};
  assert.equal(await hooks.syncDigest(), 'updated');
  assert.deepEqual(cron.calls.at(-1)[2].schedule, {kind: 'cron', expr: '0 7 * * *', tz: 'Europe/Lisbon'});
  assert.equal('name' in cron.calls.at(-1)[2], false);
  cron.jobs.push({id: 'dupe', description: PLAN.description, schedule: {kind: 'cron', expr: '0 7 * * *', tz: 'Europe/Lisbon'}, payload: {message: PLAN.message, timeoutSeconds: 600, toolsAllow: ['pixel_ods_mcp']}});
  cron.jobs[1].schedule.expr = '0 7 * * *';
  assert.equal(await hooks.syncDigest(), 'kept');
  assert.deepEqual(cron.calls.at(-1), ['remove', 'dupe']);
  plan = {...PLAN, enabled: false};
  assert.equal(await hooks.syncDigest(), 'removed');
  assert.equal(cron.calls.filter(([kind, id]) => kind === 'remove' && id === 'other').length, 0);
});

test('the digest sync runs at gateway start and every minute, and stops with the gateway', async () => {
  const timers = {started: [], cleared: [], setInterval(fn, ms) { this.started.push([fn, ms]); return {unref() {}}; },
    clearInterval(handle) { this.cleared.push(handle); }};
  const warnings = [];
  const hooks = createTaskHooks({submit: async () => ({ok: false, message: 'service down'}), timers, warn: message => warnings.push(message)});
  await hooks.gatewayStart({}, {getCron: () => fakeCron()});
  await hooks.gatewayStart({}, {getCron: () => fakeCron()});
  assert.equal(timers.started.length, 1);
  assert.equal(timers.started[0][1], 60000);
  await timers.started[0][0]();
  assert.deepEqual(warnings, Array(3).fill('Portal integrations: daily digest not scheduled: service down'));
  hooks.gatewayStop();
  assert.equal(timers.cleared.length, 1);
});

test('overlapping digest syncs add the job only once', async () => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const cron = fakeCron();
  const slowList = cron.list;
  cron.list = async options => { await gate; return slowList(options); };
  const hooks = createTaskHooks({submit: async () => ({ok: true, plan: PLAN})});
  await hooks.cronChanged({action: 'added', jobId: 'x'}, {getCron: () => cron});
  const first = hooks.syncDigest();
  const second = hooks.syncDigest();  // The minute timer fires while the start-up sync still waits.
  release();
  assert.deepEqual(await Promise.all([first, second]), ['added', 'added']);
  assert.equal(cron.calls.filter(([kind]) => kind === 'add').length, 1);
  assert.equal(await hooks.syncDigest(), 'kept');  // A later sync runs on its own again.
});

const REMINDER_PLAN = {name: 'Reminder: Stretch', description: 'Portal reminder: Stretch your legs', atMs: Date.UTC(2026, 10, 3, 16, 22),
  when: 'Tue 03 Nov 2026 at 16:22 WET', text: 'Stretch your legs', runMessage: 'Reply with exactly: Done.'};

test('a reminder becomes a one-off job that deletes itself and runs nothing of note', () => {
  assert.deepEqual(reminderJob(REMINDER_PLAN, 'pixel'), {
    name: 'Reminder: Stretch', description: 'Portal reminder: Stretch your legs', enabled: true, agentId: 'pixel',
    schedule: {kind: 'at', at: '2026-11-03T16:22:00.000Z'}, sessionTarget: 'isolated', wakeMode: 'now',
    payload: {kind: 'agentTurn', message: 'Reply with exactly: Done.', timeoutSeconds: 60, lightContext: true, toolsAllow: []},
    delivery: {mode: 'none'}, deleteAfterRun: true});
  assert.ok(REMINDER_PLAN.description.startsWith(REMINDER_MARK));
});

test('reminders are scheduled with the gateway cron once it is known', async () => {
  const {submit} = recorder({'digest-plan': {ok: true, plan: {...PLAN, enabled: false}}});
  const hooks = createTaskHooks({submit, timers: {setInterval: () => ({}), clearInterval() {}}, shared: {}});
  await assert.rejects(hooks.scheduleReminder(REMINDER_PLAN), /scheduler is not ready/);
  const cron = fakeCron();
  await hooks.gatewayStart({}, {getCron: () => cron});
  await hooks.scheduleReminder(REMINDER_PLAN);
  assert.deepEqual(cron.calls.filter(([kind]) => kind === 'add').map(([, job]) => [job.name, job.schedule.kind, job.deleteAfterRun]),
    [['Reminder: Stretch', 'at', true]]);
});

test('a reminder is sent when its job starts, and its run is removed when it ends', async () => {
  const {sent, submit} = recorder({'reminder-due': {ok: true, notified: true}, 'task-finished': {ok: true, notified: false}});
  const warnings = [];
  const cron = fakeCron();
  const hooks = createTaskHooks({submit, warn: message => warnings.push(message)});
  const job = {name: 'Reminder: Stretch', description: 'Portal reminder: Stretch your legs'};
  await hooks.cronChanged({action: 'added', jobId: 'r1', agentId: 'pixel', job}, {getCron: () => cron});
  await hooks.cronChanged({action: 'started', jobId: 'r1', agentId: 'pixel', job, runAtMs: 5}, {getCron: () => cron});
  await hooks.cronChanged({action: 'finished', jobId: 'r1', agentId: 'pixel', job, status: 'error', error: 'model busy'}, {getCron: () => cron});
  // Another agent's job is not Portal's, even when its description looks like a reminder.
  await hooks.cronChanged({action: 'started', jobId: 'x1', agentId: 'other', job}, {getCron: () => cron});
  assert.deepEqual(sent, [{action: 'reminder-due', jobId: 'r1', name: 'Reminder: Stretch', description: 'Portal reminder: Stretch your legs'}]);
  assert.deepEqual(cron.calls, [['remove', 'r1']]);
  assert.deepEqual(warnings, []);
});

test('a reminder that cannot be sent is a warning, not a crash', async () => {
  const warnings = [];
  const job = {name: 'Reminder: Tea', description: 'Portal reminder: Tea'};
  const refusing = createTaskHooks({submit: async () => ({ok: false, message: 'notifications off'}), warn: message => warnings.push(message)});
  await refusing.cronChanged({action: 'started', jobId: 'r2', job});
  const down = createTaskHooks({submit: async () => { throw new Error('down'); }, warn: message => warnings.push(message)});
  await down.cronChanged({action: 'started', jobId: 'r3', job});
  assert.deepEqual(warnings, ['Portal integrations: reminder not sent: notifications off', 'Portal integrations: reminder failed: down']);
});

test('a scheduler that appears after gateway start is still found for a reminder', async () => {
  const {submit} = recorder({'digest-plan': {ok: true, plan: {...PLAN, enabled: false}}});
  const hooks = createTaskHooks({submit, timers: {setInterval: () => ({}), clearInterval() {}}});
  let cron;
  // At gateway start OpenClaw has not created its cron service yet.
  await hooks.gatewayStart({}, {getCron: () => cron});
  cron = fakeCron();
  await hooks.scheduleReminder(REMINDER_PLAN);
  assert.equal(cron.calls.filter(([kind]) => kind === 'add').length, 1);
});

test('a reminder set from another instance of the plugin finds the scheduler the gateway gave the first', async () => {
  const shared = {};
  const {submit} = recorder({'digest-plan': {ok: true, plan: {...PLAN, enabled: false}}});
  const timers = {setInterval: () => ({}), clearInterval() {}};
  const hooked = createTaskHooks({submit, timers, shared});
  const tools = createTaskHooks({submit, timers, shared});
  await assert.rejects(tools.scheduleReminder(REMINDER_PLAN), /scheduler is not ready/);
  const cron = fakeCron();
  await hooked.gatewayStart({}, {getCron: () => cron});
  await tools.scheduleReminder(REMINDER_PLAN);
  assert.equal(cron.calls.filter(([kind]) => kind === 'add').length, 1);
  // Separate processes, or tests, keep separate slots.
  await assert.rejects(createTaskHooks({submit, timers, shared: {}}).scheduleReminder(REMINDER_PLAN), /not ready/);
});

test('a turn asking for a reminder is told which tool sets one', () => {
  for (const text of ['Remind me in 2 minutes to stretch', 'Set a reminder for 17:30', 'reminders for tomorrow please',
    'Lembre-me daqui a 10 minutos']) assert.equal(reminderHint(text), REMINDER_HINT, text);
  for (const text of ['What is the weather?', 'The reminiscence was nice', undefined]) assert.equal(reminderHint(text), '', String(text));
  assert.match(REMINDER_HINT, /pixel_ods_notify/);
  assert.match(REMINDER_HINT, /tool_call with id "openclaw:pixel-ods:pixel_ods_notify"/, 'the hint gives the exact call');
});
