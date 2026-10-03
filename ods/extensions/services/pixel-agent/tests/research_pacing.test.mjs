import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard, FREE_CORRECTIONS_PER_KIND} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';
import {SEARCH_SOURCE_EVIDENCE_GUIDANCE} from '../plugin/web-result-projection.mjs';
import {SEARCH_PACING_REASON, SEARCH_PACING_STREAK, searchTerms, nearDuplicateSearch, searchLeadUrls,
  ownerResearchDate, staleSearchDate} from '../plugin/research-pacing.mjs';

const tool={id:'openclaw:core:web_search',source:'openclaw',sourceName:'core',name:'web_search'};
const base={agentId:'pixel',runId:'pacing-run',sessionId:'pacing-session',sessionKey:'agent:pixel:openai-user:ods-pacing-test'};
const text=(message,prefix)=>message?.content?.find(block=>block.text?.startsWith(prefix))?.text;
const budget=message=>text(message,'ODS research budget');

function guardFor(prompt='Research the requested sources.',limits){
 const guard=createToolLoopGuard(limits?{limits}:{});
 guard.observeRun(base,'pixel',{prompt});
 return guard;
}
// One web_search through the real hooks. Returns the refusal, or the persisted result.
function search(guard,transport,id,query,urls=[`https://source.example/${id}`]){
 const context={...base,toolName:transport,toolCallId:id};
 const params=transport==='web_search'?{query}:{id:tool.id,args:{query}};
 const before=guard.beforeToolCall({toolName:transport,params},context);
 if(before?.block){
  // The runtime persists the refusal as an error receipt for the same call.
  guard.toolResultPersist({message:{role:'toolResult',toolName:transport,toolCallId:id,isError:true,
   content:[{type:'text',text:before.blockReason}]}},context);
  return {refusal:before.blockReason};
 }
 if(transport==='tool_call')assert.notEqual(guard.beforeToolCall({toolName:'web_search',params:{query}},
  {...context,toolName:'web_search',toolCallId:'child-'+id})?.block,true);
 const details={provider:'test',results:urls.map(url=>({title:'Source',url,description:'Source excerpt'})),externalContent:{untrusted:true}};
 const inner={content:[{type:'text',text:JSON.stringify(details)}],details};
 const envelope={tool,result:inner};
 guard.afterToolCall({toolName:transport,params,result:transport==='web_search'?inner:
  {content:[{type:'text',text:JSON.stringify(envelope)}],details:envelope}},context);
 return {out:guard.toolResultPersist({message:{role:'toolResult',toolName:transport,toolCallId:id}},context)?.message};
}
function read(guard,id,url=`https://source.example/read/${id}`){
 const result=guard.beforeToolCall({toolName:'web_fetch',params:{url}},{...base,toolName:'web_fetch',toolCallId:id});
 assert.notEqual(result?.block,true,result?.blockReason);
}

test('search terms ignore order, case and filler; model numbers and years must match',()=>{
 const same=[
  // tower3 7ef5c80b: the same specification search before and after compaction.
  ['RTX 5070 official specs NVIDIA 12 GB VRAM board power wattage TGP','NVIDIA RTX 5070 official specs 12 GB VRAM board power TGP wattage'],
  // tower2 r054: the product-page search repeated after compaction.
  ['NVIDIA GeForce RTX 5070 official product page nvidia.com','RTX 5070 official NVIDIA product page nvidia.com'],
  // tower2 r053: the same site-restricted events search.
  ['Philadelphia events calendar September October 2026 official site:visitphilly.com','Philadelphia events calendar 2026 September October site:visitphilly.com'],
 ];
 for(const [a,b] of same)assert.equal(nearDuplicateSearch(searchTerms(b),searchTerms(a)),true,`${a} / ${b}`);
 const different=[
  ['RTX 5070 price Newegg','RTX 5080 price Newegg'],
  ['Philadelphia events September 2025','Philadelphia events September 2026'],
  ['RTX 5070 price','RTX 5070 price Newegg in stock'],
  ['entity 0','entity 1'],
  ['AMD Radeon RX 9070 official specifications','AMD Radeon RX 9070 XT official specifications benchmark'],
  ['events','events'],
  // tower2 r053: a narrower venue class is a refinement, not a repeat.
  ['Philadelphia events September 2026 official','Philadelphia parks events September 2026 official'],
  ['Philadelphia events September October 2026','Philadelphia events calendar September October 2026'],
  ['RTX 5070 price Newegg Amazon Best Buy in stock','RTX 5070 price'],
 ];
 for(const [a,b] of different)assert.equal(nearDuplicateSearch(searchTerms(b),searchTerms(a)),false,`${a} / ${b}`);
});

test('lead URLs are bounded public http(s) strings in result order',()=>{
 assert.deepEqual(searchLeadUrls([{url:'https://a.example/x'},{url:'https://a.example/x'},{url:'javascript:alert(1)'},
  {url:'https://b.example/ has space'},{url:'http://c.example/y'},{url:42},{url:'https://d.example/'+'x'.repeat(600)},null]),
 ['https://a.example/x','http://c.example/y']);
 assert.deepEqual(searchLeadUrls(undefined),[]);
});

test('only an explicit owner statement anchors the research date',()=>{
 assert.equal(ownerResearchDate('Today is 2026-09-25. Search the live web for events.')?.text,'2026-09-25');
 assert.equal(ownerResearchDate('Research task as of 2026-09-25T07:23:39.277500+00:00: Compare GPUs.')?.text,'2026-09-25');
 assert.equal(ownerResearchDate('Compare the 2026-09-25 release notes.'),undefined);
 assert.equal(ownerResearchDate('Today is 2026-13-40.'),undefined);
 const owner=ownerResearchDate('Today is 2026-09-25.');
 assert.equal(staleSearchDate('RTX 5070 Newegg Amazon Best Buy price USD in stock January 2026',owner),'January 2026');
 assert.equal(staleSearchDate('RX 9070 review Mar. 2025',owner),'March 2025');
 assert.equal(staleSearchDate('Philadelphia events September October 2026',owner),undefined);
 assert.equal(staleSearchDate('Philadelphia events Sept 2026',owner),undefined);
 assert.equal(staleSearchDate('RTX 5070 RX 9070 2025',owner),undefined);
 assert.equal(staleSearchDate('RTX 5070 price January 2026',undefined),undefined);
});

for(const transport of ['web_search','tool_call']){
 test(`${transport}: unread leads pause the next search once, without spending allowance or failures`,()=>{
  const limits={search:20,fetch:10,total:40};
  const guard=guardFor(undefined,limits);
  for(let i=1;i<=SEARCH_PACING_STREAK;i++)
   assert.match(budget(search(guard,transport,`s${i}`,`distinct topic ${'abcdefgh'[i]} lookup`).out),new RegExp(`Remaining this response: ${limits.search-i} search calls`));
  const paused=search(guard,transport,'paused','another distinct subject query');
  assert.equal(paused.refusal,SEARCH_PACING_REASON);
  // The pause is free: the next search reports the unchanged allowance and runs.
  const resumed=search(guard,transport,'resumed','another distinct subject query');
  assert.match(budget(resumed.out),new RegExp(`Remaining this response: ${limits.search-SEARCH_PACING_STREAK-1} search calls`));
  // A page read ends the streak; a new streak pauses again.
  read(guard,'read-1');
  for(let i=1;i<=SEARCH_PACING_STREAK;i++)assert.ok(search(guard,transport,`t${i}`,`second topic ${'pqrstuvw'[i]} lookup`).out);
  assert.equal(search(guard,transport,'paused-2','third distinct subject query').refusal,SEARCH_PACING_REASON);
  // Only FREE_CORRECTIONS_PER_KIND pauses per run: pacing never adds a failure path.
  assert.equal(FREE_CORRECTIONS_PER_KIND,2);
  read(guard,'read-2');
  for(let i=1;i<=SEARCH_PACING_STREAK+1;i++)assert.ok(search(guard,transport,`u${i}`,`fourth topic ${'jklmnopq'[i]} item`).out,
   'a third streak proceeds unpaused');
 });
}

test('empty searches leave nothing to read and never pause discovery',()=>{
 const guard=guardFor();
 for(let i=0;i<5;i++)assert.ok(search(guard,'web_search',`empty-${i}`,`entity ${i}`,[]).out);
});

test('no pause once the page-reading allowance is spent',()=>{
 const guard=guardFor(undefined,{search:8,fetch:1,total:20});
 read(guard,'only-read');
 for(let i=1;i<=SEARCH_PACING_STREAK+1;i++)assert.ok(search(guard,'web_search',`s${i}`,`distinct topic ${'abcdefgh'[i]} lookup`).out);
});

for(const transport of ['web_search','tool_call'])test(`${transport}: a repeated search recalls the earlier result URLs instead of searching`,()=>{
 const guard=guardFor();
 const leads=['https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5070-family/','https://www.asus.com/techspec'];
 assert.ok(search(guard,transport,'first','RTX 5070 official specs NVIDIA 12 GB VRAM board power wattage TGP',leads).out);
 read(guard,'read');
 const repeated=search(guard,transport,'repeat','NVIDIA RTX 5070 official specs 12 GB VRAM board power TGP wattage');
 assert.match(repeated.refusal,/^Pixel did not repeat this search/);
 for(const url of leads)assert.ok(repeated.refusal.includes(url));
 assert.match(repeated.refusal,/untrusted leads and not verified evidence/);
 assert.match(repeated.refusal,/did not use the search allowance/);
 // A deliberate second repeat proceeds, and the recall did not spend a search.
 assert.match(budget(search(guard,transport,'repeat-2','NVIDIA RTX 5070 official specs 12 GB VRAM board power TGP wattage').out),
  /Remaining this response: 6 search calls/);
});

test('recall still answers after the search allowance is spent, while pages remain readable',()=>{
 const guard=guardFor(undefined,{search:2,fetch:4,total:8});
 assert.ok(search(guard,'web_search','a','Philadelphia events calendar September October 2026 official site:visitphilly.com',['https://www.visitphilly.com/events/']).out);
 assert.ok(search(guard,'web_search','b','Philadelphia parks events September 2026',['https://myphillypark.org/events/']).out);
 const recalled=search(guard,'web_search','c','Philadelphia events calendar 2026 September October site:visitphilly.com');
 assert.match(recalled.refusal,/https:\/\/www\.visitphilly\.com\/events\//);
 // An unrelated search still meets the ordinary exhausted-allowance denial.
 assert.match(search(guard,'web_search','d','Kimmel Center concerts').refusal,/search-call allowance is exhausted/);
 read(guard,'read-recalled','https://www.visitphilly.com/events/');
});

test('refusals are not charged as tool failures',()=>{
 const guard=guardFor();
 assert.ok(search(guard,'web_search','first','RTX 5070 official specs NVIDIA',['https://www.nvidia.com/rtx-5070']).out);
 // Three ordinary failed reads: one more charged failure would stop the run.
 for(let i=0;i<3;i++)guard.toolResultPersist({message:{role:'toolResult',toolName:'web_fetch',toolCallId:`bad-${i}`,isError:true,
  content:[{type:'text',text:'Web fetch failed (403)'}]}},{...base,toolName:'web_fetch',toolCallId:`bad-${i}`});
 assert.match(search(guard,'web_search','repeat','NVIDIA RTX 5070 official specs').refusal,/Pixel did not repeat this search/);
 const next=guard.beforeToolCall({toolName:'web_fetch',params:{url:'https://www.nvidia.com/rtx-5070'}},{...base,toolName:'web_fetch',toolCallId:'next'});
 assert.notEqual(next?.block,true,next?.blockReason);
 assert.notEqual(next?.blockReason,RUN_PROGRESS_STOP_REASON);
});

test('a stale month in a search is flagged against the owner-stated date',()=>{
 const guard=guardFor('Research task as of 2026-09-25T07:23:39.277500+00:00: Compare RTX 5070 and RX 9070 prices.');
 const stale=search(guard,'web_search','stale','RTX 5070 Newegg Amazon Best Buy price USD in stock January 2026').out;
 assert.match(text(stale,'ODS date check'),/owner gave the date 2026-09-25, but this search named January 2026/);
 assert.equal(text(search(guard,'web_search','current','RX 9070 Newegg price in stock').out,'ODS date check'),undefined);
 const undated=guardFor('Compare RTX 5070 and RX 9070 prices.');
 assert.equal(text(search(undated,'web_search','stale','RTX 5070 price January 2026').out,'ODS date check'),undefined);
});

for(const transport of ['web_search','tool_call'])test(`${transport}: fixed search guidance is not repeated on every result`,()=>{
 const guard=guardFor();
 const first=search(guard,transport,'g1','first distinct lookup').out;
 const second=search(guard,transport,'g2','second separate question').out;
 assert.ok(first.content.some(block=>block.text===SEARCH_SOURCE_EVIDENCE_GUIDANCE));
 assert.ok(!second.content.some(block=>block.text===SEARCH_SOURCE_EVIDENCE_GUIDANCE));
 assert.match(budget(second),/Remaining this response: 6 search calls/);
});
