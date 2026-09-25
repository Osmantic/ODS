import test from 'node:test';
import assert from 'node:assert/strict';
import {createTaskActivity} from '../plugin/task-activity.mjs';
import {createActivityTool,displayForActivity} from '../plugin/activity-display.mjs';
import {parseTaskActivity} from '../host/task_activity_schema.mjs';
const runId='chatcmpl_11111111-2222-4333-8444-555555555555';
const context={agentId:'pixel',runId,toolCallId:'call-1',toolName:'tool_call'};
test('public updates and search sources survive the complete projection without raw output',async()=>{
 const tracker=createTaskActivity();tracker.begin({},context);
 const event={params:{id:'pixel_ods_research',args:{query:'Documentação oficial'}}};
 tracker.before(event,context);
 tracker.after({...event,result:{details:{sources:[{title:'Reference',url:'https://example.com/docs'},{title:'Secret',url:'https://example.com/?token=secret'},{title:'Unsafe',url:'javascript:alert(1)'}],private:'hidden'},content:[{text:'secret body'}]}},context);
 const row=tracker.projection(runId);assert.equal(row.schemaVersion,4);assert.ok(parseTaskActivity(row,runId));
 assert.equal(row.events[0].display.type,'search');assert.equal(row.events[0].display.sources.length,1);
 assert.equal(row.events[0].display.label,'Documentação oficial');assert.ok(!JSON.stringify(row).includes('secret'));
 const tool=createActivityTool();assert.equal((await tool.execute('x',{message:'Conferindo as fontes.'})).isError,undefined);
 assert.equal((await tool.execute('x',{message:'x',private:'hidden'})).isError,true);
});
test('blocked calls cannot masquerade as public progress, file details exclude directories and commands',()=>{
 const tracker=createTaskActivity();tracker.begin({},context);
 tracker.before({params:{id:'pixel_ods_activity',args:{message:'Done'}}},context,true);
 assert.equal(tracker.projection(runId).events[0].display,null);
 assert.ok(parseTaskActivity(tracker.projection(runId),runId));
 assert.equal(displayForActivity({params:{path:'C:\\Users\\Private\\project\\app.js'}},{toolName:'read'}).detail,'app.js');
 assert.ok(!JSON.stringify(displayForActivity({params:{command:'curl -H "Authorization: Bearer secret"'}},{toolName:'exec'})).includes('secret'));
 assert.equal(displayForActivity({params:{command:'npm test'}},{toolName:'exec'}).detail,'npm test');
});
test('projects bounded edit receipts while excluding credential files and sensitive lines',()=>{
 const value=displayForActivity({params:{path:'src/main.js',oldText:'const n = 1;',newText:'const n = 2;'}},{toolName:'edit'});
 assert.equal(value.change.file,'main.js');assert.equal(value.change.before,'const n = 1;');assert.equal(value.change.after,'const n = 2;');
 assert.equal(displayForActivity({params:{path:'.env',content:'secret'}},{toolName:'write'}).change,null);
 assert.ok(!displayForActivity({params:{path:'config.js',content:'const api_key = "secret";'}},{toolName:'write'}).change.after.includes('secret'));
 assert.equal(displayForActivity({params:{path:'long.js',content:'x'.repeat(2000)}},{toolName:'write'}).change.truncated,true);
});
test('schema rejects extra content, unsafe sources and contradictory typed displays',()=>{
 const tracker=createTaskActivity();tracker.begin({},context);tracker.before({params:{id:'pixel_ods_activity',args:{message:'Checking'}}},context);
 const row=tracker.projection(runId);
 for(const mutate of [d=>d.private='hidden',d=>d.sources=[{title:'x',url:'javascript:alert(1)'}],d=>d.label='x'.repeat(161),d=>d.steps=[{id:'a',title:'A',status:'completed'}]]){
  const copy=structuredClone(row);mutate(copy.events[0].display);assert.equal(parseTaskActivity(copy,runId),null);
 }
});

test('current runtime edits arrays retain bounded separate hunks through wrapped hooks',()=>{
 const tracker=createTaskActivity();tracker.begin({},context);
 const event={params:{id:'openclaw:core:edit',args:{path:'notes.txt',edits:[{oldText:'before',newText:'after'},{oldText:'one',newText:'two'}]}}};
 tracker.before(event,context);tracker.after({...event,result:{isError:false}},context);
 const projected=tracker.projection(runId);assert.ok(parseTaskActivity(projected,runId));
 const change=projected.events[0].display.change;
 assert.equal(change.kind,'patch');assert.equal(change.file,'notes.txt');
 assert.equal(change.after,'@@ Replacement @@\n-before\n+after\n@@ Replacement @@\n-one\n+two');assert.equal(change.truncated,false);
 const long=displayForActivity({params:{path:'file.txt',edits:Array.from({length:10},()=>({oldText:'x',newText:'y'}))}},{toolName:'edit'});
 assert.equal(long.change.truncated,true);assert.ok(long.change.after.length<=1000);
 assert.equal(displayForActivity({params:{path:'.env.local',edits:[{oldText:'a',newText:'b'}]}},{toolName:'edit'}).change,null);
});

test('source titles remove the core web envelope without changing original evidence',()=>{
 const title='<<<EXTERNAL_UNTRUSTED_CONTENT id="abc">>>\nSource: Web Search\n---\nuseState\n<<<END_EXTERNAL_UNTRUSTED_CONTENT id="abc">>>';
 const event={params:{query:'React'},result:{details:{results:[{title,url:'https://react.dev/reference/react/useState'}]}}};
 const display=displayForActivity(event,{toolName:'web_search'});
 assert.equal(display.sources[0].title,'useState');assert.equal(event.result.details.results[0].title,title);
});
