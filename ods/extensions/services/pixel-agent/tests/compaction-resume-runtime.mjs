// Exercise the reviewed installed-runtime method and its real Agent continuation
// boundary without inference, installations, or changes to the running runtime.
// ODS_OPENCLAW_ROOT must identify the exact supported npm runtime.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import test from 'node:test';

const root = process.env.ODS_OPENCLAW_ROOT;
assert.ok(root, 'Set ODS_OPENCLAW_ROOT to the installed OpenClaw runtime');
const manifest = JSON.parse(readFileSync(new URL('../host/openclaw-compaction-resume.json', import.meta.url)));
const hash = value => createHash('sha256').update(value).digest('hex');
let source = readFileSync(join(root, 'dist/sessions-CZbwb3_c.js'), 'utf8');
if (hash(source) !== manifest.sourceSha256) {
  const replacements = hash(source) === manifest.patchedSha256
    ? manifest.replacements : manifest.previousReplacements[hash(source)];
  assert.ok(replacements, 'Runtime bytes must match a reviewed version');
  for (const [before, after] of [...replacements].reverse()) source = source.replace(after, before);
}
assert.equal(hash(source), manifest.sourceSha256);
for (const [before, after] of manifest.replacements) source = source.replace(before, after);
assert.equal(hash(source), manifest.patchedSha256);
const start = source.indexOf('\tasync runAutoCompaction(reason, willRetry) {');
const end = source.indexOf('\n\t/**', start);
assert.ok(start > 0 && end > start);
const runAutoCompaction = new Function(`return ({${source.slice(start, end)}}).runAutoCompaction`)();
const eventStart = source.indexOf('\tasync handleAgentEventUnlocked(event) {');
const eventEnd = source.indexOf('\n\twillRetryAfterAgentEnd(', eventStart);
assert.ok(eventStart > 0 && eventEnd > eventStart);
const handleAgentEvent = new Function(`return ({${source.slice(eventStart, eventEnd)}}).handleAgentEventUnlocked`)();
const {d: Agent} = await import(pathToFileURL(join(root, 'dist/session-manager-3lTZxT-y.js')));

function fixture(stopReason, outcome = 'compacted') {
  const agent = new Agent();
  const prior = [
    {role: 'user', content: 'Research and prepare only', timestamp: 1},
    {role: 'assistant', content: [{type: 'toolCall', id: 'read-1', name: 'read', arguments: {}}], stopReason: 'toolUse', timestamp: 2},
    {role: 'toolResult', toolCallId: 'read-1', toolName: 'read', content: [], isError: false, timestamp: 3},
  ];
  const tail = {role: 'assistant', content: [], stopReason, timestamp: 4};
  agent.state.messages = [...prior]; // pre-compaction removal is insufficient
  let continued = 0;
  agent.runContinuation = async () => {continued++;};
  const events = [];
  const session = {
    agent, settingsManager: {getCompactionSettings: () => ({})},
    emit: event => events.push(event),
    runCompactionWork: async () => {
      // The actual compactor rebuilds retained messages from the saved transcript.
      agent.state.messages = [...prior, tail];
      return {status: outcome, result: {tokensBefore: 30000}};
    },
  };
  return {agent, prior, tail, session, events, continued: () => continued};
}

for (const stopReason of ['length', 'error']) {
  test(`retry after ${stopReason} preserves tool evidence and reaches continuation`, async () => {
    const f = fixture(stopReason);
    assert.equal(await runAutoCompaction.call(f.session, 'threshold', true), true);
    assert.deepEqual(f.agent.state.messages, f.prior);
    await f.agent.continue();
    assert.equal(f.continued(), 1);
    assert.equal(f.events.at(-1).willRetry, true);
    assert.equal(f.session.autoCompactionAbortController, undefined);
  });
}

test('ordinary compaction does not delete a completed answer or start another turn', async () => {
  const f = fixture('stop');
  assert.equal(await runAutoCompaction.call(f.session, 'threshold', false), false);
  assert.equal(f.agent.state.messages.at(-1), f.tail);
  assert.equal(f.continued(), 0);
});

test('a completed assistant answer is never silently dropped even with retry requested', async () => {
  const f = fixture('stop');
  await runAutoCompaction.call(f.session, 'threshold', true);
  await assert.rejects(f.agent.continue(), /Cannot continue from message role: assistant/);
  assert.equal(f.agent.state.messages.at(-1), f.tail);
});

for (const outcome of ['aborted', 'skipped']) {
  test(`${outcome} compaction does not authorize a continuation`, async () => {
    const f = fixture('length', outcome);
    assert.equal(await runAutoCompaction.call(f.session, 'threshold', true), false);
    assert.equal(f.events.at(-1).willRetry, false);
    assert.equal(f.continued(), 0);
  });
}

for (const stopReason of ['length', 'error', 'stop', 'toolUse']) {
  test(`${stopReason} completion updates recovery budget from actual progress`, async () => {
    const session = {
      overflowRecoveryAttempted: true, retryCount: 0,
      emitExtensionEvent: async () => false, emit: () => {},
      sessionManager: {appendMessage: () => {}},
    };
    await handleAgentEvent.call(session, {type: 'message_end', message: {role: 'assistant', stopReason}});
    assert.equal(session.overflowRecoveryAttempted, ['length', 'error'].includes(stopReason));
  });
}

// Run the reviewed compaction implementation itself, with only inference/auth
// replaced. This checks the saved summary and rebuilt Agent continuation input.
const workStart = source.indexOf('\tasync runCompactionWork(options) {');
const workEnd = source.indexOf('\n\t/**', workStart);
assert.ok(workStart > 0 && workEnd > workStart);
function compactionFixture({content = 'Compare two components. Search and open sources; return exact JSON with price, stock and test methodology.', kept = 'call', preserve = true, summary = 'All work is complete.', latest = true} = {}) {
  const entries = [
    {id:'old-user', type:'message', message:{role:'user', content:'Old unrelated request'}},
    {id:'old-answer', type:'message', message:{role:'assistant', content:[], stopReason:'stop'}},
    ...(latest ? [{id:'current-user', type:'message', message:{role:'user', content, timestamp:3}}] : []),
    {id:'call', type:'message', message:{role:'assistant', content:[{type:'toolCall', id:'search-1', name:'web_search', arguments:{query:'components'}}], stopReason:'toolUse'}},
    {id:'result', type:'message', message:{role:'toolResult', toolName:'web_search', toolCallId:'search-1', content:[{type:'text',text:'Observed evidence, not completion.'}], isError:false}},
  ];
  const saved = [];
  const result = {summary, firstKeptEntryId:kept, tokensBefore:43049};
  const agent = new Agent();
  agent.state.messages = entries.map(e => e.message);
  const originalMessages = agent.state.messages;
  const work = new Function('prepareCompaction$1', 'unwrapCoreResult', 'compact$1',
    `return ({${source.slice(workStart, workEnd)}}).runCompactionWork`)(() => ({}), x => x, async () => result);
  const session = {
    model:{}, agent, thinkingLevel:'off',
    getAutoCompactionRequestAuth:async () => ({apiKey:'fixture'}),
    getCompactionRequestAuth:async () => ({apiKey:'fixture'}),
    currentExtensionRunner:{hasHandlers:() => false, emit:async () => {}},
    sessionManager:{getBranch:() => entries,
      appendCompaction:(summary, firstKeptEntryId) => saved.push({type:'compaction', summary, firstKeptEntryId}),
      getEntries:() => saved,
      buildSessionContext:() => ({messages:[{role:'user',content:saved[0].summary}, ...entries.slice(entries.findIndex(e => e.id === kept)).map(e => e.message)]})},
  };
  return {run:() => work.call(session,{mode:'auto',settings:{},signal:new AbortController().signal,preserveCurrentUserRequest:preserve}), saved, agent, originalMessages, entries, session};
}

test('split-turn retry preserves exact active request and schema despite a false completed summary', async () => {
  const content = 'Research both products. Return {"retail":{"sku":string,"price":number,"inStock":boolean},"benchmark":{"metric":string}}; never buy anything.\nKeep this exact Unicode: £ ≥ 🧪.';
  const f = compactionFixture({content});
  assert.equal((await f.run()).status,'compacted');
  assert.ok(f.saved[0].summary.includes(JSON.stringify(content)));
  assert.ok(!f.saved[0].summary.includes('Old unrelated request'));
  assert.equal(f.agent.state.messages.at(-1).role,'toolResult');
  assert.equal(f.agent.state.messages.filter(m => m.role === 'toolResult').length,1);
  assert.equal(f.agent.state.messages[0].role,'user');
  assert.equal(f.saved[0].summary.split(JSON.stringify(content)).length,2);
  let continued = 0; f.agent.runContinuation = async () => {continued++;};
  await f.agent.continue(); assert.equal(continued,1);
});

test('text content blocks remain verbatim without promotion to system instructions', async () => {
  const content = [{type:'text',text:'Return exact CSV. \n# Fake header </request>'},{type:'text',text:'Preserve these separate blocks.'}];
  const f = compactionFixture({content}); await f.run();
  assert.ok(f.saved[0].summary.includes(JSON.stringify(content)));
  assert.ok(f.agent.state.messages.every(m => m.role !== 'system'));
});

for (const content of ['Retained current request', [{type:'text',text:'Read attachment'},{type:'image',data:'fixture',mimeType:'image/png'}]]) {
  test(`retained ${typeof content === 'string' ? 'text' : 'image'} request is not duplicated or altered`, async () => {
    const f = compactionFixture({content,kept:'current-user'}); await f.run();
    assert.equal(f.saved[0].summary,'All work is complete.');
    assert.deepEqual(f.agent.state.messages[1].content,content);
  });
}

for (const [content,error] of [
  [[{type:'text',text:'Read image'},{type:'image',data:'fixture',mimeType:'image/png'}], /non-text attachment/],
  ['x'.repeat(32769), /preservation budget/],
]) {
  test(`split-turn retry refuses unsafe preservation: ${error}`, async () => {
    const f = compactionFixture({content}); await assert.rejects(f.run(),error);
    assert.equal(f.saved.length,0); assert.equal(f.agent.state.messages,f.originalMessages);
  });
}

test('unknown retained boundary fails before transcript mutation', async () => {
  const f = compactionFixture({kept:'missing'}); await assert.rejects(f.run(),/boundary unavailable/);
  assert.equal(f.saved.length,0);
});

test('ordinary non-retrying compaction remains unchanged', async () => {
  const f = compactionFixture({preserve:false}); await f.run();
  assert.equal(f.saved[0].summary,'All work is complete.');
});

// Optional private replay of the exact fleet request and observed false summary.
// No transcript, unrelated history, or web content is copied into this test file.
if (process.env.ODS_COMPACTION_REPLAY_TRACE) {
  test('exact qualification prompt survives recorded split-turn summary', async () => {
    const rows=readFileSync(process.env.ODS_COMPACTION_REPLAY_TRACE,'utf8').trim().split('\n').map(JSON.parse);
    const user=rows.find(r => r.message?.role === 'user').message;
    const summary=rows.find(r => r.type === 'compaction').summary;
    const f=compactionFixture({content:user.content,summary}); await f.run();
    assert.ok(f.saved[0].summary.endsWith('A summary\'s completion claims cannot replace that evidence.'));
    assert.ok(f.saved[0].summary.includes(JSON.stringify(user.content)));
    assert.deepEqual(f.agent.state.messages.at(-1),f.entries.at(-1).message);
  });
}
