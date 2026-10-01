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
  const applied=sha(original)===recipe.patchedSha256 ? recipe.replacements : recipe.previousReplacements?.[sha(original)];
  if (applied) {
    for (const [before, after] of [...applied].reverse()) {
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


// Run the exact installed caller with its real normalization/accumulator helpers.
// A yield's synthetic zero-usage abort previously hid the preceding model call.
const yielded = reviewed('yield-usage', 'embedded-agent-CJx-nG3W.js');
const selection = reviewed('compaction-budget', 'selection-BEwSQKM-.js');
function currentYieldAssistant(messagesSnapshot, prePromptMessageCount=0) {
  const start=selection.patched.indexOf('currentAttemptAssistant = yieldDetected');
  const end=selection.patched.indexOf('attemptUsage = getUsageTotals();',start);
  assert.ok(start>0&&end>start);
  return new Function('snapshotSelection','prePromptMessageCount',
    'const yieldDetected=true; let currentAttemptAssistant;\n'+selection.patched.slice(start,end)+'return currentAttemptAssistant;')({messagesSnapshot},prePromptMessageCount);
}
const {i:hasNonzeroUsage,o:normalizeUsage} = await import(pathToFileURL(path.join(root,'dist/usage-C67Kbb7n.js')));
const {C:createUsageAccumulator,w:mergeUsageIntoAccumulator,l:buildUsageAgentMetaFields} =
  await import(pathToFileURL(path.join(root,'dist/selection-BEwSQKM-.js')));
function yieldAccounting(source, attempt) {
  const start=source.includes('const yieldAssistant =') ? source.indexOf('const yieldAssistant =') : source.indexOf('const lastAssistantUsage = normalizeUsage(sessionLastAssistant?.usage);');
  const end=source.indexOf('const breakerStep =',start);
  const metaStart=source.indexOf('const usageMeta = buildUsageAgentMetaFields({');
  const metaEnd=source.indexOf('const reportedModelRef =',metaStart);
  assert.ok(start>0&&end>start&&metaStart>end&&metaEnd>metaStart);
  const execute=new Function('normalizeUsage','hasNonzeroUsage','mergeUsageIntoAccumulator','buildUsageAgentMetaFields','createUsageAccumulator','attempt',
    'const sessionLastAssistant=attempt.lastAssistant, currentAttemptAssistant=attempt.currentAttemptAssistant; const usageAccumulator=createUsageAccumulator(); let lastRunPromptUsage,lastTurnTotal;\n'
    +source.slice(start,end)+source.slice(metaStart,metaEnd)+'\nreturn {usageMeta,usageAccumulator,lastRunPromptUsage,lastTurnTotal};');
  return execute(normalizeUsage,hasNonzeroUsage,mergeUsageIntoAccumulator,buildUsageAgentMetaFields,createUsageAccumulator,{...attempt,currentAttemptAssistant:currentYieldAssistant(attempt.messagesSnapshot??[],attempt.prePromptMessageCount??0)});
}
const measured={role:'assistant',stopReason:'toolUse',usage:{input:27846,output:75,totalTokens:27921}};
const synthetic={role:'assistant',stopReason:'aborted',usage:{input:0,output:0,totalTokens:0}};
const yieldedAttempt={yieldDetected:true,lastAssistant:undefined,messagesSnapshot:[measured,synthetic],attemptUsage:{input:136627,output:1547,total:138174}};

test('native yield uses the last measured model call rather than run-wide billing',()=>{
  const before=yieldAccounting(yielded.original,yieldedAttempt),after=yieldAccounting(yielded.patched,yieldedAttempt);
  assert.equal(before.usageMeta.lastCallUsage.total,138174);
  assert.equal(after.usageMeta.lastCallUsage.total,27921);
  assert.equal(after.lastTurnTotal,27921);
  assert.deepEqual(after.usageAccumulator,before.usageAccumulator,'accumulated billing is unchanged');
});

test('yield accounting excludes failed/aborted synthetic messages and preserves native cache normalization',()=>{
  const call={...measured,usage:{input:20000,output:20,cacheRead:600,cacheWrite:200,totalTokens:20820}};
  const failed={role:'assistant',stopReason:'error',usage:{input:99999,output:1,totalTokens:100000}};
  const aborted={...failed,stopReason:'aborted'};
  const after=yieldAccounting(yielded.patched,{...yieldedAttempt,lastAssistant:aborted,messagesSnapshot:[call,failed,aborted]});
  assert.deepEqual(after.usageMeta.lastCallUsage,normalizeUsage(call.usage));
  assert.equal(after.lastTurnTotal,20820);
});

test('yield without any measured successful assistant does not invent context from billing or zero',()=>{
  const after=yieldAccounting(yielded.patched,{...yieldedAttempt,messagesSnapshot:[synthetic]});
  assert.equal(after.usageMeta.lastCallUsage,undefined);
  assert.equal(after.usageMeta.promptTokens,undefined);
  assert.equal(after.lastRunPromptUsage,undefined);
  assert.equal(after.lastTurnTotal,undefined);
  assert.equal(after.usageMeta.usage.input,136627);
});

test('non-yield accounting keeps the existing native normalization and error fallback',()=>{
  for(const lastAssistant of [measured,synthetic,undefined]) {
    const input={...yieldedAttempt,yieldDetected:false,lastAssistant};
    assert.deepEqual(yieldAccounting(yielded.patched,input),yieldAccounting(yielded.original,input));
  }
});


test('missing current usage does not backfill an older historical assistant measurement',()=>{
  for(const usage of [undefined,{input:0,output:0,totalTokens:0}]) {
    const unmeasured={role:'assistant',stopReason:'toolUse',usage};
    const after=yieldAccounting(yielded.patched,{...yieldedAttempt,messagesSnapshot:[measured,unmeasured,synthetic]});
    assert.equal(after.usageMeta.lastCallUsage,undefined);
    assert.equal(after.lastRunPromptUsage,undefined);
  }
});


test('yield selection ignores historical assistants and projected nested tool-call messages',()=>{
  const historical={...measured,usage:{input:99000,output:9,totalTokens:99009}};
  assert.equal(currentYieldAssistant([historical,synthetic],1),undefined);
  // The reviewed selector consumes snapshotSelection's raw messages, not the
  // later projected messagesSnapshot containing this synthetic tool_call.
  const selected=currentYieldAssistant([historical,measured,synthetic],1);
  assert.equal(selected,measured);
  assert.equal(yieldAccounting(yielded.patched,{...yieldedAttempt,prePromptMessageCount:1,messagesSnapshot:[historical,synthetic]}).usageMeta.lastCallUsage,undefined);
});
