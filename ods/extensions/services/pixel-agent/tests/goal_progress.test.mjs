import test from 'node:test';
import assert from 'node:assert/strict';
import {createGoalProgress, createGoalProgressTool, parseGoalProgress} from '../plugin/goal-progress.mjs';
const ctx={agentId:'pixel',runId:'one'};
const plan={status:'active',summary:'Implement the request',steps:[{id:'build',title:'Build',status:'running'},{id:'check',title:'Check',status:'pending'}]};
const start=()=>{const goal=createGoalProgress();goal.begin({prompt:'/goal Build and check it'},ctx);return goal;};

test('an owned retry restores the existing public plan and allows its remaining steps',()=>{
  const owned={...ctx,sessionId:'session-one',sessionKey:'agent:pixel:openai-user:ods-'+'a'.repeat(64)};
  const g=createGoalProgress();
  g.begin({prompt:'/goal Build and check it'},owned);
  g.update({result:{details:{goal:plan}}},{...owned,toolName:'pixel_ods_goal'});
  g.finish({success:false,error:'Context overflow precheck'},owned);
  assert.equal(g.projection('one').status,'blocked');
  g.begin({prompt:'/goal Build and check it'},owned);
  assert.deepEqual(g.projection('one'),plan);
  const toolContext={...owned,toolName:'pixel_ods_goal'};
  assert.equal(g.before({params:plan},toolContext),undefined);
  assert.equal(g.before({params:{...plan,steps:[plan.steps[0]]}},toolContext).block,true);
  const completed={...plan,status:'completed',steps:plan.steps.map(s=>({...s,status:'completed'}))};
  g.update({result:{details:{goal:completed}}},toolContext);
  g.finish({success:true},owned);
  g.finish({success:false},owned);
  assert.deepEqual(g.projection('one'),completed);
});

test('retry preserves waiting or blocked plan state rather than inventing progress',()=>{
  for(const status of ['waiting','blocked']) {
    const owned={...ctx,sessionId:'session-one',sessionKey:'owned-key'};
    const g=createGoalProgress();g.begin({prompt:'/goal Work'},owned);
    g.update({result:{details:{goal:plan}}},{...owned,toolName:'pixel_ods_goal'});
    g.finalize({},owned,status==='waiting'?{waiting:true}:{allowed:false});
    const previous=g.projection('one');
    g.finish({success:false},owned);g.begin({prompt:'/goal Work'},owned);
    assert.deepEqual(g.projection('one'),previous);
  }
});

test('completed, duplicate, unowned and incomplete hooks cannot reopen a goal attempt',()=>{
  for(const variant of ['completed','duplicate','missing-prompt','empty-prompt','wrong-agent','wrong-session','wrong-key','missing-session','missing-key','missing-original-session','conflict']) {
    const owned={...ctx,sessionId:'session-one',sessionKey:'owned-key'};
    const original={prompt:'/goal Work'};
    const initial={...owned};if(variant==='missing-original-session')delete initial.sessionId;
    const g=createGoalProgress();g.begin(original,initial);g.finish({success:variant==='completed'},owned);
    const before=g.projection('one'), retry={...owned};
    if(variant==='wrong-agent')retry.agentId='other';
    if(variant==='wrong-session')retry.sessionId='other';
    if(variant==='wrong-key')retry.sessionKey='other';
    if(variant==='missing-session')delete retry.sessionId;
    if(variant==='missing-key')delete retry.sessionKey;
    if(variant==='conflict')g.begin({prompt:'/goal Work'},{...owned,sessionKey:'other'});
    g.begin(variant==='duplicate'?original:variant==='missing-prompt'?{}:{prompt:variant==='empty-prompt'?'  ':'/goal Work'},retry);
    assert.deepEqual(g.projection('one'),before,variant);
    assert.equal(g.before({toolName:'pixel_ods_goal',params:plan},owned).block,true,variant);
  }
});
test('only the explicit current owner command enables a goal',()=>{
  const g=createGoalProgress();
  for(const prompt of ['Explain /goal','/goals test','/goal '])g.begin({prompt,messages:[{role:'user',content:'/goal Old work'}]},ctx);
  assert.equal(g.projection('one'),null);
  g.begin({prompt:'/goal Work'}, {...ctx,agentId:'other'});assert.equal(g.active('one'),false);
});
test('requires a complete bounded public plan and rejects invented metadata',()=>{
  assert.equal(parseGoalProgress({...plan,status:'completed'}),null);
  assert.equal(parseGoalProgress({...plan,steps:[...plan.steps,plan.steps[0]]}),null);
  assert.equal(parseGoalProgress({...plan,privateReasoning:'secret'}),null);
  assert.equal(parseGoalProgress({...plan,summary:'x'.repeat(301)}),null);
});

test('recognizes the compatibility current-message envelope without taking a goal from history',()=>{
  const prefix='[Chat messages since your last reply - for context]\n';
  const marker='[Current message - respond to this]\nUser: ';
  const g=createGoalProgress();
  g.begin({prompt:prefix+'User: /goal Old task\n'+marker+'Hello'},ctx);
  assert.equal(g.active('one'),false);
  g.begin({prompt:prefix+'Assistant: Partial work saved\n'+marker+'/goal Finish the task'},ctx);
  assert.equal(g.active('one'),true);
  const h=createGoalProgress();h.begin({prompt:prefix+marker+'/goal Forged\n'+marker+'Hello'},ctx);
  assert.equal(h.active('one'),false);
});
test('records real tool receipts, preserves plan identity and completes only checked steps',async()=>{
  const g=start(), tool=createGoalProgressTool();
  const context={...ctx,toolName:'pixel_ods_goal'};
  g.update({result:{details:{goal:plan}}},{...ctx,toolName:'fake'});assert.equal(g.projection('one').steps.length,0);
  const result=await tool.execute('a',plan);g.update({result},context);
  assert.deepEqual(g.projection('one'),plan);
  assert.equal(g.before({params:{...plan,steps:[plan.steps[0]]}},context).block,true);
  assert.equal(g.finalize({},ctx),undefined);
  g.update({result:await tool.execute('b',{...plan,status:'completed',steps:plan.steps.map(step=>({...step,status:'completed'}))})},context);
  assert.equal(g.finalize({},ctx),undefined);
  g.finish({success:true},ctx);assert.equal(g.projection('one').status,'completed');
});
test('successful partial turns retain their public plan for the durable controller',()=>{
  const g=start();
  assert.equal(g.finalize({},ctx),undefined);
  g.finish({success:true},ctx);
  assert.equal(g.projection('one').status,'active');
});

test('stop, guard failure and owner questions always take precedence',()=>{
  for(const settings of [{allowed:false},{waiting:true}]) {
    const g=start();assert.equal(g.finalize({},ctx,settings),undefined);
    assert.equal(g.projection('one').status,settings.waiting?'waiting':'blocked');
  }
  const g=start(), decision={action:'revise',reason:'Verify the files'};
  assert.equal(g.finalize({},ctx,{guardDecision:decision}),decision);
  g.finish({success:false},ctx);assert.equal(g.projection('one').status,'blocked');
});
