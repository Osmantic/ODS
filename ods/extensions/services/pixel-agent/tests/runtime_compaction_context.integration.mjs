// Exact pinned source, offline provider and disposable in-memory transcript writes only.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';

const root = process.env.OPENCLAW_PACKAGE_DIR;
assert.ok(root, 'provide the reviewed OpenClaw package explicitly');
const sha = value => createHash('sha256').update(value).digest('hex');
function reviewed(name, module) {
  const recipe = JSON.parse(fs.readFileSync(new URL(`../host/openclaw-${name}.json`, import.meta.url)));
  let original = fs.readFileSync(path.join(root, 'dist', module), 'utf8');
  if (sha(original) === recipe.patchedSha256) {
    for (const [before, after] of [...recipe.replacements].reverse()) {
      assert.equal(original.split(after).length, 2);
      original = original.replace(after, before);
    }
  }
  assert.equal(sha(original), recipe.sourceSha256, 'reject unknown native runtime bytes');
  let patched = original;
  for (const [before, after] of recipe.replacements) {
    assert.equal(patched.split(before).length, 2);
    patched = patched.replace(before, after);
  }
  assert.equal(sha(patched), recipe.patchedSha256);
  return {original, patched};
}
const proxy = reviewed('compaction-empty', 'proxy-Bsfwfsp-.js');
const attempt = reviewed('context-usage', 'attempt-execution-DnVHak5f.js');
async function loadProxy(source) {
  const absolute = source.replace(/from "(\.\/[^"\n]+)"/g, (_all, relative) =>
    `from "${pathToFileURL(path.join(root, 'dist', relative)).href}"`);
  return await import(`data:text/javascript;base64,${Buffer.from(absolute).toString('base64')}`);
}
const baseline = await loadProxy(proxy.original), fixed = await loadProxy(proxy.patched);
const {i: buildUsageWithNoCost} = await import(pathToFileURL(path.join(root, 'dist/stream-message-shared-CdbBqwfX.js')));
const settings = {...fixed.S, keepRecentTokens:8192, reserveTokens:32768};
const usage = (input, output) => buildUsageWithNoCost({input, output, cacheRead:0, cacheWrite:0, totalTokens:input+output});
async function mirror(source, {gapFill = true, lastCallUsage = {input:27946, output:61, total:28007}} = {}) {
  const start = source.indexOf('function resolveTranscriptUsage(usage) {');
  const end = source.indexOf('function runAgentAttempt(params) {', start);
  assert.ok(start > 0 && end > start);
  let persisted;
  const persist = async (_scope, options) => {persisted = options.messages; return {sessionEntry:{}};};
  const run = new Function('buildUsageWithNoCost', 'persistSessionTranscriptTurn',
    'readTailAssistantTextFromSessionTranscript', 'normalizeTranscriptMirrorText', 'ACP_TRANSCRIPT_USAGE',
    source.slice(start, end) + '\nreturn persistCliTurnTranscript;')(
    buildUsageWithNoCost, persist, async()=>null, value=>value.trim(), usage(0,0));
  const result = {meta:{finalAssistantVisibleText:'Waiting for delegated review.',agentMeta:{
    provider:'fixture',model:'fixture',usage:{input:116539,output:1103,total:117642},
    ...(lastCallUsage ? {lastCallUsage} : {})}}};
  const billingBefore = structuredClone(result.meta.agentMeta.usage);
  await run({result,embeddedAssistantGapFill:gapFill,body:'Review with two agents.',sessionId:'fixture',sessionKey:'agent:pixel:fixture'});
  assert.deepEqual(result.meta.agentMeta.usage, billingBefore, 'billing metadata must remain unchanged');
  return persisted.at(-1).message;
}
function entries(mirrorMessage) {
  // 21 entries, like the live yielded run; no private prompt or generated files in this fixture.
  const rows = [{type:'model_change',id:'first',parentId:null}];
  rows.push({type:'message',id:'owner',parentId:'first',message:{role:'user',content:'Review the project using two agents.'}});
  for(let i=0;i<9;i++) {
    rows.push({type:'message',id:`assistant-${i}`,message:{role:'assistant',content:[{type:'text',text:'Reviewed evidence. '.repeat(70)}],usage:usage(27000+i*100,60),stopReason:'toolUse'}});
    rows.push({type:'message',id:`tool-${i}`,message:{role:'toolResult',toolName:'read',toolCallId:`call-${i}`,content:[{type:'text',text:'Read-only project evidence. '.repeat(50)}]}});
  }
  rows.push({type:'message',id:'mirror',message:mirrorMessage});
  for(let i=2;i<rows.length;i++) rows[i].parentId=rows[i-1].id;
  assert.equal(rows.length,21);
  return rows;
}

test('old gap-fill uses aggregate billing and creates an empty 21-entry checkpoint', async()=>{
  const old = await mirror(attempt.original);
  assert.equal(old.usage.totalTokens,117642);
  assert.equal(baseline.M(old.usage.totalTokens,131072,settings),true);
  const prepared=baseline.j(entries(old),settings);
  assert.equal(prepared.ok,true);
  assert.equal(prepared.value.firstKeptEntryId,'first');
  assert.deepEqual(prepared.value.messagesToSummarize,[]);
  let prompt;
  const result=await baseline.w(prepared.value,{maxTokens:4096},undefined,undefined,undefined,undefined,undefined,
    async(_model,context)=>({result:async()=>{prompt=context.messages[0].content[0].text;return {stopReason:'stop',content:[{type:'text',text:'Empty conversation.'}]};}}));
  assert.equal(result.ok,true);
  assert.match(prompt,/<conversation>\n\n<\/conversation>/);
});

test('gap-fill records last-call context, retains billing and skips needless compaction',async()=>{
  const current=await mirror(attempt.patched);
  assert.equal(current.usage.totalTokens,28007);
  assert.equal(fixed.M(current.usage.totalTokens,131072,settings),false);
  const prepared=fixed.j(entries(current),settings);
  assert.equal(prepared.ok,true);
  assert.equal(prepared.value,undefined);
});

test('missing last-call measurement does not replace earlier context with zero or aggregate usage',async()=>{
  const current=await mirror(attempt.patched,{lastCallUsage:null});
  assert.equal(Object.hasOwn(current,'usage'),false);
  const estimate=fixed.T(entries(current).filter(e=>e.type==='message').map(e=>e.message));
  assert.ok(estimate.tokens>27860 && estimate.tokens<40000);
});

test('last-call cache counters preserve native context accounting with and without explicit total',async()=>{
  for(const total of [undefined,0,21999]) {
    const current=await mirror(attempt.patched,{lastCallUsage:{input:20000,output:20,cacheRead:600,cacheWrite:200,...total===undefined?{}:{total}}});
    assert.equal(current.usage.input,20000);
    assert.equal(current.usage.cacheRead,600);
    assert.equal(current.usage.cacheWrite,200);
    // Native resolveTranscriptUsage defaults missing total to input+output;
    // explicit zero uses calculateContextTokens' existing cache-aware fallback.
    assert.equal(fixed.C(current.usage),total===undefined ? 20020 : total || 20820);
  }
});

test('actual CLI transcript retains its existing provider usage',async()=>{
  const current=await mirror(attempt.patched,{gapFill:false});
  assert.equal(current.usage.totalTokens,117642);
});

test('direct empty preparation fails before any provider request instead of storing a false summary',async()=>{
  const prepared=baseline.j(entries(await mirror(attempt.original)),settings).value;
  let called=false;
  const result=await fixed.w(prepared,{maxTokens:4096},undefined,undefined,undefined,undefined,undefined,
    async()=>{called=true;throw new Error('must not request provider');});
  assert.equal(result.ok,false);
  assert.equal(called,false);
  assert.match(result.error.message,/No compactable conversation messages/);
});

test('nonempty and split-turn compaction still call the provider with real history',async()=>{
  for(const keepRecentTokens of [1500,200]) {
    const prepared=fixed.j(entries(await mirror(attempt.patched)),{...settings,keepRecentTokens});
    assert.equal(prepared.ok,true);
    assert.ok(prepared.value.messagesToSummarize.length+prepared.value.turnPrefixMessages.length>0);
    const prompts=[];
    const result=await fixed.w(prepared.value,{maxTokens:4096},undefined,undefined,undefined,undefined,undefined,
      async(_model,context)=>({result:async()=>{prompts.push(context.messages[0].content[0].text);return {stopReason:'stop',content:[{type:'text',text:'Evidence summary.'}]};}}));
    assert.equal(result.ok,true);
    assert.ok(prompts.length>0);
    assert.ok(prompts.every(prompt=>!prompt.includes('<conversation>\n\n</conversation>')));
  }
});

if(process.env.ODS_COMPACTION_TRANSCRIPT) test('original local 21-event regression uses only read-only transcript input',async()=>{
  let rows=fs.readFileSync(process.env.ODS_COMPACTION_TRANSCRIPT,'utf8').trim().split('\n').map(JSON.parse);
  rows=rows.slice(1,rows.findIndex(row=>row.type==='compaction'));
  assert.equal(rows.length,21);
  const original=baseline.j(rows,settings).value;
  assert.equal(original.tokensBefore,117642);
  assert.equal(original.messagesToSummarize.length,0);
  assert.equal(original.turnPrefixMessages.length,0);
  assert.equal(fixed.j(rows,settings).value,undefined);
});
