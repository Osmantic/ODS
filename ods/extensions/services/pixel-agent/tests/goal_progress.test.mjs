import test from 'node:test';
import assert from 'node:assert/strict';
import {createGoalProgress, createGoalProgressTool, GOAL_CONTRACT, parseGoalProgress} from '../plugin/goal-progress.mjs';
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

// Step IDs follow one existing rule. These argument objects reproduce a real
// failed round: three plan updates with hyphenated IDs, then a valid fourth.
const STEP_ID = /^[a-z][a-z0-9_]{0,31}$/;
const RAW_BAD_IDS = ['copy-edit-1', 'copy-title', 'remaining-edits', 'publish-checks'];
const planOf = (ids, second) => ({status:'active', summary:'Working', steps:[
  {id:ids[0], title:'Copy and first title edit', status:'completed'},
  {id:ids[1], title:'Remaining five edits', status:second},
  {id:ids[2], title:'Publication and real browser checks', status:'pending'}]});
const HYPHENATED = [planOf(['copy-edit-1', 'remaining-edits', 'publish-checks'], 'pending'),
  planOf(['copy-title', 'remaining-edits', 'publish-checks'], 'running'),
  planOf(['copy-title', 'remaining-edits', 'publish-checks'], 'running')];
const VALID_FOURTH = planOf(['work', 'verify', 'publish'], 'running');
const SCHEMA = {type:'object', additionalProperties:false, required:['status', 'summary', 'steps'], properties:{
  status:{type:'string', enum:['active', 'completed', 'blocked']}, summary:{type:'string', minLength:1, maxLength:300},
  steps:{type:'array', minItems:1, maxItems:8, items:{type:'object', additionalProperties:false, required:['id', 'title', 'status'], properties:{
    id:{type:'string', pattern:'^[a-z][a-z0-9_]{0,31}$'}, title:{type:'string', minLength:1, maxLength:160},
    status:{type:'string', enum:['pending', 'running', 'completed', 'blocked']}}}}}};
const direct = (g, args) => g.before({toolName:'pixel_ods_goal', params:args}, {...ctx, toolName:'pixel_ods_goal'});
const wrapped = (g, args) => g.before({params:{id:'openclaw:pixel-ods:pixel_ods_goal', args}}, {...ctx, toolName:'tool_call'});
const errorText = result => {
  assert.equal(result.isError, true);
  assert.equal(result.details, undefined);
  return result.content[0].text;
};

test('the first-call contract and tool description state the existing step ID rule with a valid example', () => {
  const description = createGoalProgressTool().description;
  for (const text of [GOAL_CONTRACT, description]) {
    assert.match(text, /lowercase letter/);
    assert.match(text, /lowercase letters, digits or underscores/);
    assert.match(text, /at most 32 characters/);
    assert.match(text, /no hyphens/);
    const example = text.match(/for example "([^"]+)"/);
    assert.ok(example && STEP_ID.test(example[1]), 'one short valid example');
    for (const [, id] of text.matchAll(/"id":"([^"]*)"/g)) assert.match(id, STEP_ID, 'every quoted ID is valid');
  }
  for (const kept of ['Preserve its exact IDs and titles', 'continue it instead of publishing a new active plan',
    'There is no completed boolean or questions field', 'Write new titles in the reply language',
    'report status="blocked" and a concrete summary', 'Do not invent progress, evidence, or private reasoning'])
    assert.ok(GOAL_CONTRACT.includes(kept), kept);
  for (const kept of ['Keep step IDs and titles unchanged after the initial plan', 'Completed requires every step completed',
    'it does not execute work or prove success']) assert.ok(description.includes(kept), kept);
});

test('the published parameters are unchanged', () => {
  assert.deepEqual(createGoalProgressTool().parameters, SCHEMA);
  assert.notEqual(createGoalProgressTool().parameters, createGoalProgressTool().parameters);
});

test('the actual hyphenated plans are refused with the step ID rule, never the questions guidance', async () => {
  const tool = createGoalProgressTool();
  for (const args of HYPHENATED) {
    const sent = structuredClone(args);
    assert.equal(parseGoalProgress(args), null);
    const g = start();
    const decisions = [direct(g, args), wrapped(g, args)];
    for (const decision of decisions) {
      assert.deepEqual(Object.keys(decision).sort(), ['block', 'blockReason'], 'refused, never rewritten');
      assert.equal(decision.block, true);
      assert.match(decision.blockReason, /steps\[0\]\.id/);
      assert.match(decision.blockReason, /no hyphens/);
      assert.doesNotMatch(decision.blockReason, /questions/);
    }
    const text = errorText(await tool.execute('bad', args));
    assert.match(text, /steps\[0\]\.id/);
    assert.match(text, /no hyphens/);
    for (const reason of [...decisions.map(decision => decision.blockReason), text])
      for (const raw of RAW_BAD_IDS) assert.ok(!reason.includes(raw), 'the rejected value is not echoed');
    assert.deepEqual(g.projection('one').steps, [], 'no plan is recorded');
    assert.deepEqual(args, sent, 'the arguments are not normalized');
  }
  const g = start();
  assert.equal(direct(g, VALID_FOURTH), undefined);
  assert.equal(wrapped(g, VALID_FOURTH), undefined);
  const result = await tool.execute('good', VALID_FOURTH);
  assert.deepEqual(result.details.goal, VALID_FOURTH);
  assert.notEqual(result.details.goal, VALID_FOURTH);
  g.update({result}, {...ctx, toolName:'pixel_ods_goal'});
  assert.deepEqual(g.projection('one'), VALID_FOURTH);
});

test('each refusal names its field and rule, bounded and without the rejected value', async () => {
  const S = 'zSENTINELz';
  const step = {id:'work', title:'Build', status:'running'};
  const second = {id:'check', title:'Check', status:'pending'};
  const plan2 = change => ({status:'active', summary:'Working', steps:[{...step}, {...second}], ...change});
  const cases = [
    [{...plan2(), questions:[S]}, /exactly the keys status, summary and steps/],
    [{status:'active', summary:'Working'}, /exactly the keys status, summary and steps/],
    [[S], /exactly the keys status, summary and steps/],
    [null, /exactly the keys status, summary and steps/],
    [plan2({status:S}), /status must be "active", "completed" or "blocked"/],
    [plan2({summary:''}), /summary must be non-empty text of at most 300 characters/],
    [plan2({summary:S + 'x'.repeat(300)}), /summary must be non-empty text of at most 300 characters/],
    [plan2({summary:S + '\u0007'}), /summary must be non-empty text of at most 300 characters/],
    [plan2({steps:S}), /steps must be a list of 1 to 8 steps/],
    [plan2({steps:Array.from({length:9}, (_, i) => ({...step, id:'s' + i}))}), /steps must be a list of 1 to 8 steps/],
    [plan2({steps:[step, {...second, [S]:1}]}), /steps\[1\] must have exactly the keys id, title and status/],
    [plan2({steps:[step, S]}), /steps\[1\] must have exactly the keys id, title and status/],
    [plan2({steps:[step, {...second, id:7}]}), /steps\[1\]\.id/],
    [plan2({steps:[step, {...second, id:S}]}), /steps\[1\]\.id/],
    [plan2({steps:[step, {...second, id:'a' + S.toLowerCase() + 'x'.repeat(30)}]}), /steps\[1\]\.id/],
    [plan2({steps:[step, {...second, id:'_' + S.toLowerCase()}]}), /steps\[1\]\.id/],
    [plan2({steps:[step, {...second, id:'work'}]}), /steps\[1\]\.id repeats an earlier step ID/],
    [plan2({steps:[step, {...second, title:' '}]}), /steps\[1\]\.title must be non-empty text of at most 160 characters/],
    [plan2({steps:[step, {...second, title:S + 'x'.repeat(160)}]}), /steps\[1\]\.title/],
    [plan2({steps:[step, {...second, title:S + '\n'}]}), /steps\[1\]\.title/],
    [plan2({steps:[step, {...second, status:S}]}), /steps\[1\]\.status must be "pending", "running", "completed" or "blocked"/],
    [plan2({status:'completed'}), /status "completed" requires every step status "completed"/],
  ];
  const tool = createGoalProgressTool();
  for (const [args, rule] of cases) {
    const label = String(JSON.stringify(args)).slice(0, 80);
    assert.equal(parseGoalProgress(args), null, label);
    const g = start();
    const reasons = [direct(g, args).blockReason, wrapped(g, args).blockReason, errorText(await tool.execute('x', args))];
    for (const reason of reasons) {
      assert.match(reason, rule, label);
      assert.doesNotMatch(reason, /sentinel/i, label);
      assert.ok(reason.length <= 600, label);
      assert.doesNotMatch(reason, /takes a plan, not questions/, label);
    }
    assert.deepEqual(g.projection('one').steps, [], label);
  }
});

test('legacy completed:true and unowned or finished runs keep their own refusals first', () => {
  for (const args of [{completed:true}, {...plan, completed:true}, {completed:true, steps:[{id:'bad-id'}]}]) {
    const g = start();
    for (const decision of [direct(g, args), wrapped(g, args)])
      assert.match(decision.blockReason, /^Replace the invalid field "completed":true with "status":"completed"/);
  }
  const unowned = createGoalProgress();
  for (const args of [HYPHENATED[0], VALID_FOURTH, {completed:true}])
    assert.equal(direct(unowned, args).blockReason, 'The owner has not enabled /goal for this turn.');
  const finished = start(); finished.finish({success:true}, ctx);
  for (const args of [HYPHENATED[0], VALID_FOURTH, {completed:true}])
    assert.equal(wrapped(finished, args).blockReason, 'The owner has not enabled /goal for this turn.');
});

test('the parser still projects waiting and empty plans that the runtime tool refuses', async () => {
  const empty = {status:'active', summary:'Preparing the plan', steps:[]};
  const waiting = {...plan, status:'waiting'};
  assert.deepEqual(parseGoalProgress(empty), empty);
  assert.deepEqual(parseGoalProgress(waiting), waiting);
  assert.deepEqual(parseGoalProgress({...plan, status:'blocked'}), {...plan, status:'blocked'});
  const g = start();
  for (const call of [direct, wrapped]) {
    assert.match(call(g, empty).blockReason, /steps must be a list of 1 to 8 steps/);
    assert.match(call(g, waiting).blockReason, /status must be "active", "completed" or "blocked"/);
  }
  const tool = createGoalProgressTool();
  assert.match(errorText(await tool.execute('e', empty)), /steps must be a list of 1 to 8 steps/);
  assert.match(errorText(await tool.execute('w', waiting)), /status must be "active", "completed" or "blocked"/);
});

test('a saved plan keeps its identity: renamed or invalid IDs are refused and nothing is relabeled', async () => {
  const g = start(), tool = createGoalProgressTool(), context = {...ctx, toolName:'pixel_ods_goal'};
  g.update({result:await tool.execute('a', plan)}, context);
  const saved = g.projection('one');
  const renamed = {...plan, steps:[{...plan.steps[0], id:'build_step'}, plan.steps[1]]};
  assert.match(direct(g, renamed).blockReason, /^Update the original plan/);
  const invalid = {...plan, steps:[{...plan.steps[0], id:'build-step'}, plan.steps[1]]};
  for (const reason of [direct(g, invalid).blockReason, wrapped(g, invalid).blockReason]) {
    assert.match(reason, /steps\[0\]\.id/);
    assert.match(reason, /keep its exact step IDs and titles/);
    assert.ok(!reason.includes('build-step'));
  }
  g.update({result:await tool.execute('b', renamed)}, context);
  g.update({result:{details:{goal:invalid}}}, context);
  assert.deepEqual(g.projection('one'), saved);
});

test('parsed plans, tool receipts and projections are isolated copies', async () => {
  const input = structuredClone(plan);
  const parsed = parseGoalProgress(input);
  assert.deepEqual(parsed, plan);
  input.steps[0].title = 'Changed';
  input.steps.push({id:'later', title:'Later', status:'pending'});
  assert.deepEqual(parsed, plan);
  const params = structuredClone(plan), result = await createGoalProgressTool().execute('c', params);
  params.steps[0].status = 'blocked';
  assert.deepEqual(result.details.goal, plan);
  const g = start();
  g.update({result}, {...ctx, toolName:'pixel_ods_goal'});
  g.projection('one').steps[0].id = 'changed';
  assert.deepEqual(g.projection('one'), plan);
});
