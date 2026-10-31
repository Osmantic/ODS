import test from 'node:test';
import assert from 'node:assert/strict';
import { createTaskHooks } from '../plugin/integration-tasks.mjs';

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
