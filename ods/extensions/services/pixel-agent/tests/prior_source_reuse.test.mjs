import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {createCompletionAssurance, ownerAuthorizesPriorSourceReuse} from '../plugin/completion-assurance.mjs';

const url='https://docs.python.org/3/library/decimal.html#rounding-modes-2';
const user='ods-'+'d'.repeat(64);
const command=`python3 -c "from decimal import Decimal, ROUND_HALF_UP; print(Decimal('1.005').quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))"`;
const prompt=`Continue and execute this exact terminal command now: ${command}. Report the observed stdout and the official documentation page you already found.`;
const answer=`Observed stdout: 1.01. Official documentation: ${url}`;
function fixture() {
  let time=1_000_000;
  const guard=createToolLoopGuard({now:()=>time,
    abortRunAndDrain:async()=>({aborted:true,drained:true,forceCleared:false})});
  const context=(runId, overrides={})=>({agentId:'pixel',runId,sessionId:'decimal-session',
    sessionKey:'agent:pixel:openai-user:'+user,trigger:'user',...overrides});
  const begin=(runId, text=prompt, overrides={})=>{
    const c=context(runId,overrides);guard.observeRun(c,'pixel',{prompt:text});return c;
  };
  const tool=(c,name,params,result,id=name)=>{
    const scoped={...c,toolName:name,toolCallId:id};
    const prepared=guard.beforeToolCall({toolName:name,params},scoped);
    assert.notEqual(prepared?.block,true,prepared?.blockReason);
    guard.afterToolCall({toolName:name,params:prepared?.params??params,result},scoped);
  };
  const read=(c,source=url,status=200)=>tool(c,'web_fetch',{url:source},
    {details:{status,url:source,finalUrl:source,text:'ROUND_HALF_UP rounds ties away from zero.'},
      content:[{type:'text',text:'Decimal rounding documentation.'}]});
  const exec=c=>tool(c,'exec',{command,workdir:'/workspace'},
    {details:{status:'completed',exitCode:0,aggregated:'1.01'}});
  const finish=(c,text=answer)=>{
    const decision=guard.beforeAgentFinalize({runId:c.runId,lastAssistantMessage:text,
      messages:[{role:'assistant',content:[{type:'text',text}]}]},c);
    return {decision,delivery:guard.deliveryVerificationForRun(c.runId)};
  };
  const first=()=>{
    const c=begin('first','Search the web for the official Python Decimal rounding documentation. Then execute Python.');
    read(c);guard.observeAgentEnd({success:false},c);return c;
  };
  return {guard,context,begin,tool,read,exec,finish,first,advance:ms=>{time+=ms;},now:()=>time};
}
function passed(result) {
  assert.notEqual(result.decision?.action,'revise',JSON.stringify(result.decision));
  assert.equal(result.delivery.status,'passed',JSON.stringify(result.delivery));
  assert.match(result.delivery.text,/1\.01/);
  assert.match(result.delivery.text,/read earlier in this chat and were not reread this turn/);
}
function failed(result) {assert.equal(result.delivery.status,'failed',JSON.stringify(result));}

test('the real two-turn Decimal request preserves successful stdout and the previously read page',()=>{
  const f=fixture();f.first();const c=f.begin('second');f.exec(c);passed(f.finish(c));
});
test('same source identity accepts fragments and trailing slashes without another read',()=>{
  const f=fixture();const c1=f.begin('first','Read official documentation.');
  f.read(c1,'https://docs.python.org/3/library/decimal.html/');
  f.guard.observeAgentEnd({success:true},c1);
  const c2=f.begin('second');f.exec(c2);passed(f.finish(c2));
});
test('a successful source read does not stand in for execution, including failed commands',()=>{
  for(const executed of [false,true]) {
    const f=fixture();f.first();const c=f.begin('second');
    if(executed)f.tool(c,'exec',{command,workdir:'/workspace'},
      {details:{status:'completed',exitCode:1,aggregated:'Python failed'}});
    failed(f.finish(c));
  }
});
test('different chats or owner session keys cannot import a prior receipt',()=>{
  for(const overrides of [{sessionId:'other-chat'},
    {sessionKey:'agent:pixel:openai-user:ods-'+'e'.repeat(64)}]) {
    const f=fixture();f.first();const c=f.begin('second',prompt,overrides);f.exec(c);failed(f.finish(c));
  }
});
test('a conflicting identity after seeding cannot keep historical source acceptance',()=>{
  const f=fixture();f.first();const c=f.begin('second');f.exec(c);
  f.guard.observeRun({...c,sessionId:'changed-chat'},'pixel',{prompt});
  failed(f.finish({...c,sessionId:'changed-chat'}));
});
test('a final hook with a different session cannot keep historical source acceptance',()=>{
  const f=fixture();f.first();const c=f.begin('second');f.exec(c);
  failed(f.finish({...c,sessionId:'changed-chat'}));
});
test('a native prompt hook without a session key can bind once at the first tool hook',()=>{
  const f=fixture();f.first();const c=f.context('second');
  f.guard.observeRun({...c,sessionKey:undefined},'pixel',{prompt});
  f.exec(c);passed(f.finish(c));
});
test('noninteractive prior turns cannot supply an owner-chat receipt',()=>{
  const f=fixture();const c1=f.begin('first','Read official documentation.',{trigger:'cron'});
  f.read(c1);f.guard.observeAgentEnd({success:true},c1);
  const c2=f.begin('second');f.exec(c2);failed(f.finish(c2));
});
test('a failed page fetch or search result cannot become a historical read',()=>{
  for(const kind of ['failed','search']) {
    const f=fixture();const c1=f.begin('first','Read official documentation.');
    if(kind==='failed')f.read(c1,url,500);
    else f.tool(c1,'web_search',{query:'Python Decimal'},
      {content:[{type:'text',text:JSON.stringify({results:[{url}]})}],details:{results:[{url}]}});
    f.guard.observeAgentEnd({success:true},c1);
    const c2=f.begin('second');f.exec(c2);failed(f.finish(c2));
  }
});
test('cancelling the prior run withdraws its source receipts',async()=>{
  const f=fixture();const c1=f.begin('first','Read official documentation.');f.read(c1);
  assert.equal(await f.guard.abortUserRun(user),true);
  const c2=f.begin('second');f.exec(c2);failed(f.finish(c2));
});
test('cancelling the current run prevents a successful historical-source delivery',async()=>{
  const f=fixture();f.first();const c=f.begin('second');f.exec(c);passed(f.finish(c));
  assert.equal(await f.guard.abortUserRun(user),true);
  assert.notEqual(f.guard.deliveryVerificationForRun(c.runId).status,'passed');
});
test('receipt age expires before or during a follow-up and is not renewed by an intervening answer',()=>{
  for(const delayBefore of [31,29]) {
    const f=fixture();f.first();f.advance(delayBefore*60_000);const c=f.begin('second');
    if(delayBefore===29)f.advance(2*60_000);
    f.exec(c);failed(f.finish(c));
  }
  const f=fixture();f.first();f.advance(20*60_000);const c2=f.begin('second');f.exec(c2);
  passed(f.finish(c2));f.guard.observeAgentEnd({success:true},c2);
  f.advance(20*60_000);const c3=f.begin('third');f.exec(c3);failed(f.finish(c3));
});
test('fresh research and ordinary official-source requests do not implicitly reuse history',()=>{
  for(const request of [
    `Execute ${command}. Report stdout and the official documentation.`,
    `Execute ${command}. Reread the official documentation page you already found.`,
    `Execute ${command}. Do not use the official documentation page you already found.`,
  ]) {const f=fixture();f.first();const c=f.begin('second',request);f.exec(c);failed(f.finish(c));}
});
test('an extra unread citation still prevents successful delivery',()=>{
  const f=fixture();f.first();const c=f.begin('second');f.exec(c);
  failed(f.finish(c,answer+' Another source: https://example.com/unread.'));
});
test('same-run prompt retries cannot replace the original authorization or receipt selection',()=>{
  const f=fixture();f.first();const c=f.begin('second',`Execute ${command}. Report stdout and the official documentation.`);
  f.guard.observeRun(c,'pixel',{prompt});f.exec(c);failed(f.finish(c));
  const a=createCompletionAssurance({now:()=>100});
  a.begin(prompt,{},[]);
  a.begin(prompt,{},[{url,timestamp:99}]);
  assert.equal(a.finalize(answer)?.action,'revise');
  assert.equal(a.terminalStatus,'failed');
});
test('late identity binding cannot import later reads or change original owner intent',()=>{
  let time=100;const a=createCompletionAssurance({now:()=>time});a.begin(prompt,{});
  time=102;a.begin(prompt,{},[{url,timestamp:101}]);
  assert.equal(a.finalize(answer)?.action,'revise');
  const b=createCompletionAssurance({now:()=>100});
  b.begin('Report the official documentation.',{});
  b.begin(prompt,{},[{url,timestamp:99}]);
  assert.equal(b.finalize(answer)?.action,'revise');
});
test('imported receipts stay separate from current reads and source exports',()=>{
  const a=createCompletionAssurance({now:()=>100});a.begin(prompt,{},[{url,timestamp:99}]);
  assert.equal(a.finalize(answer)?.action,'finalize');
  assert.deepEqual(a.currentRunSourceReceipts(),[]);
  assert.deepEqual(a.readPages,[]);assert.deepEqual(a.synthesisSources(),[]);
  assert.deepEqual(a.unverifiedCitations(answer),['https://docs.python.org/3/library/decimal.html']);
});
test('prior receipt selection is bounded to the newest 32 supplied unique valid URLs',()=>{
  const receipts=Array.from({length:40},(_,i)=>({url:`https://example.com/page-${i}`,timestamp:100-i}));
  const make=()=>{const a=createCompletionAssurance({now:()=>100});a.begin('Use the official sources you read earlier.',{},receipts);return a;};
  const accepted=make();assert.equal(accepted.finalize('Verified answer https://example.com/page-31')?.action,'finalize');
  assert.equal(accepted.terminalStatus,'passed');
  const excluded=make();assert.equal(excluded.finalize('Verified answer https://example.com/page-32')?.action,'revise');
  assert.equal(excluded.terminalStatus,'failed');
});
test('rereading the cited page this turn removes the historical-source note',()=>{
  const f=fixture();f.first();const c=f.begin('second');f.read(c);f.exec(c);
  const result=f.finish(c);assert.notEqual(result.decision?.action,'revise');
  assert.doesNotMatch(result.delivery.text??'',/not reread this turn/);
});
test('reuse intent excludes quoted examples and source refreshes while allowing execution now',()=>{
  for(const text of [prompt,'Compute the current balance, use docs read earlier.','Use the sources you read earlier.'])
    assert.equal(ownerAuthorizesPriorSourceReuse(text),true,text);
  for(const text of [
    'Do not use the official documentation page you already found.',
    'Without reusing the sources you already found, answer.',
    'Ignore the documentation page you already found.',
    'The example says: reuse the sources you read earlier.',
    'Explain the phrase reuse the sources you read earlier.',
    'The docs say "reuse the sources you read earlier".',
    "'Reuse the sources you read earlier.' Explain this.",
    '“Reuse the sources you read earlier.” Explain this.',
    '```\nReuse the sources you read earlier.\n```',
    '> Reuse the sources you read earlier.',
    'Reuse the sources you read earlier and fetch the latest documentation.',
    'Use the official documentation.',
  ])assert.equal(ownerAuthorizesPriorSourceReuse(text),false,text);
});

// Append to prior_source_reuse.test.mjs

test('exact natural citation-repair prompt authorizes prior reuse', () => {
  const text = 'Please add clickable links to the official documentation you actually consulted. Keep the observed stdout unchanged.';
  assert.equal(ownerAuthorizesPriorSourceReuse(text), true, text);
  // Variants that must also match the new branch.
  for (const variant of [
    'Please include links to the docs you already read.',
    'Provide URLs for the sources you previously opened.',
    'Show citations for the pages you consulted.',
    'Give me clickable links to the documentation you used.',
    'Attach links to the docs you actually consulted.',
    'Insert URLs for the sources you read.',
  ]) assert.equal(ownerAuthorizesPriorSourceReuse(variant), true, variant);
});

test('exact natural citation-repair prompt retains real CSV prior receipt but neutralises unread decimal link', () => {
  // Real CSV prior receipt (read in the first turn) plus a decimal link that
  // was never read. The repair prompt authorizes reuse of the CSV receipt.
  const csvUrl = 'https://docs.python.org/3/library/csv.html';
  const decimalUrl = 'https://docs.python.org/3/library/decimal.html';
  const repairPrompt = 'Please add clickable links to the official documentation you actually consulted. Keep the observed stdout unchanged.';
  const answerWithBoth = `Observed stdout: 1.01. CSV docs: ${csvUrl}. Decimal docs: ${decimalUrl}`;

  const a = createCompletionAssurance({now: () => 100});
  a.begin(repairPrompt, {}, [{url: csvUrl, timestamp: 99}]);
  // First finalize arms a revision (decimal link is unread).
  const first = a.finalize(answerWithBoth);
  assert.equal(first?.action, 'revise', JSON.stringify(first));
  assert.equal(a.terminalStatus, 'failed');
  // Second finalize exhausts revision: partial answer must keep the CSV link,
  // neutralise the decimal link, retain stdout, and add the historical note.
  const second = a.finalize(answerWithBoth);
  assert.equal(second?.action, 'finalize', JSON.stringify(second));
  assert.equal(a.terminalStatus, 'failed');
  const delivered = a.terminal;
  assert.match(delivered, /1\.01/, 'stdout retained');
  assert.ok(delivered.includes(csvUrl), 'real CSV prior receipt retained');
  assert.ok(!delivered.includes(decimalUrl), 'unread decimal link neutralised');
  assert.match(delivered, /\[source not verified\]/);
  assert.match(delivered, /read earlier in this chat and were not reread this turn/);
});

test('valid CSV-only citation passes with historical note', () => {
  const csvUrl = 'https://docs.python.org/3/library/csv.html';
  const repairPrompt = 'Please add clickable links to the official documentation you actually consulted. Keep the observed stdout unchanged.';
  const a = createCompletionAssurance({now: () => 100});
  a.begin(repairPrompt, {}, [{url: csvUrl, timestamp: 99}]);
  const result = a.finalize(`Observed stdout: 1.01. CSV docs: ${csvUrl}`);
  assert.equal(result?.action, 'finalize', JSON.stringify(result));
  assert.equal(a.terminalStatus, 'passed');
  assert.match(a.terminal, /read earlier in this chat and were not reread this turn/);
});

test('wrong chat, expired, or cancelled prior receipts are blocked for the repair prompt', async () => {
  const csvUrl = 'https://docs.python.org/3/library/csv.html';
  const repairPrompt = 'Please add clickable links to the official documentation you actually consulted. Keep the observed stdout unchanged.';
  const answer = `Observed stdout: 1.01. CSV docs: ${csvUrl}`;

  // Wrong chat: receipt timestamp is fine but session differs.
  {
    const f = fixture();
    const c1 = f.begin('first', 'Read official documentation.');
    f.read(c1, csvUrl);
    f.guard.observeAgentEnd({success: true}, c1);
    const c2 = f.begin('second', repairPrompt, {sessionId: 'other-chat'});
    f.exec(c2);
    failed(f.finish(c2, answer));
  }
  // Expired: receipt older than 30 minutes at finalize.
  {
    const a = createCompletionAssurance({now: () => 100});
    a.begin(repairPrompt, {}, [{url: csvUrl, timestamp: 100 - 31 * 60 * 1000}]);
    const result = a.finalize(answer);
    assert.equal(result?.action, 'revise', JSON.stringify(result));
    assert.equal(a.terminalStatus, 'failed');
  }
  // Cancelled prior run withdraws the receipt.
  {
    const f = fixture();
    const c1 = f.begin('first', 'Read official documentation.');
    f.read(c1, csvUrl);
    assert.equal(await f.guard.abortUserRun(user), true);
    const c2 = f.begin('second', repairPrompt);
    f.exec(c2);
    failed(f.finish(c2, answer));
  }
});

test('exact repair prompt does not gain prior on quoted, negated, refresh, or bare-doc requests', () => {
  const blocked = [
    // Quoted / example forms of the repair prompt.
    'The example says: "Please add clickable links to the official documentation you actually consulted. Keep the observed stdout unchanged."',
    'Explain the phrase "add clickable links to the documentation pages you actually consulted".',
    '```\nPlease add clickable links to the official documentation you actually consulted. Keep the observed stdout unchanged.\n```',
    '> Please add clickable links to the official documentation you actually consulted. Keep the observed stdout unchanged.',
    // Negated.
    'Do not add clickable links to the documentation pages you actually consulted.',
    'Without adding links to the docs you already read, answer.',
    // Refresh.
    'Add clickable links to the documentation pages you actually consulted, then refresh the sources.',
    'Add clickable links to the documentation pages you actually consulted and fetch the latest docs.',
    // Bare-doc request (no "you consulted/read/opened/used").
    'Add clickable links to the documentation pages.',
    'Provide links to the official documentation.',
  ];
  for (const request of blocked) {
    assert.equal(ownerAuthorizesPriorSourceReuse(request), false, request);

  }
});

test('a previously attribution-incomplete initial answer still stores genuine read receipts', () => {
  // First turn: model reads the CSV page but answers without citing it, so
  // attribution is incomplete. The read receipt must still be recorded and
  // exported via currentRunSourceReceipts so a later repair turn can reuse it.
  const csvUrl = 'https://docs.python.org/3/library/csv.html';
  const a = createCompletionAssurance({now: () => 100});
  a.begin('Read the official CSV documentation.', {});
  a.observe('web_fetch', {
    params: {url: csvUrl},
    result: {
      details: {status: 200, url: csvUrl, finalUrl: csvUrl, text: 'CSV module documentation.'},
      content: [{type: 'text', text: 'CSV module documentation.'}],
    },
  });
  // Answer omits the URL entirely: attribution incomplete.
  const result = a.finalize('The CSV module handles delimited text.');
  assert.equal(result?.action, 'revise', JSON.stringify(result));
  assert.equal(a.terminalStatus, 'failed');
  // Genuine read receipt is still stored and exported.
  const receipts = a.currentRunSourceReceipts();
  assert.equal(receipts.length, 1, JSON.stringify(receipts));
  assert.equal(receipts[0].url, csvUrl);
  assert.equal(receipts[0].timestamp, 100);
  // And it is a real read page, not a prior import.
  assert.deepEqual(a.readPages.map(p => p.url), [csvUrl]);
});

test('an attribution-incomplete first turn can be repaired through the same-chat guard', () => {
  const csvUrl='https://docs.python.org/3/library/csv.html';
  const f=fixture();
  const c1=f.begin('first','Read the official CSV documentation.');
  f.read(c1,csvUrl);
  failed(f.finish(c1,'The CSV module can parse quoted category names into a dictionary.'));
  f.guard.observeAgentEnd({success:false},c1);
  const c2=f.begin('second','Please add clickable links to the official documentation you actually consulted. Keep the observed stdout unchanged.');
  const result=f.finish(c2,`Observed stdout: 1.01. CSV documentation: ${csvUrl}`);
  passed(result);
});
