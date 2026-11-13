import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const recipe=JSON.parse(readFileSync(new URL('../host/openclaw-compaction-budget.json',import.meta.url)));
const pressureRepair=recipe.replacements.find(([before])=>before.startsWith('\t\t\t\t\tconst llmBoundaryTokenPressure ='));
assert.ok(pressureRepair,'exercise the shipped precheck replacement');
const run=(patched,overrides={})=>{
 const converted={type:'function',function:{name:'fixture_read',parameters:{type:'object',properties:{}}}};
 const tool={name:'fixture_read',parameters:{type:'object',properties:{}}};
 const history=[{role:'user',content:'retained owner history'}];
 const state={conversions:0,pressureCalls:[],model:{api:'openai-completions'},tools:[tool],history};
 const values={sessionAgentId:'pixel',params:{model:state.model},contextTokenBudget:65536,effectiveTools:state.tools,
  hookMessagesForCurrentPrompt:history,systemPromptForHook:'native core',llmBoundaryPromptForPrecheck:'owner',
  estimateLlmBoundaryTokenPressure(input){state.pressureCalls.push(input);return input.prompt===JSON.stringify([converted])?12000:44000;},
  buildOpenAICompletionsParams(model,context){state.conversions++;assert.equal(model,state.model);
   assert.equal(context.tools,state.tools,'use original final post-policy objects');assert.deepEqual(context.messages,[]);return {tools:[converted]};},...overrides};
 const fn=new Function(...Object.keys(values),pressureRepair[patched?1:0]+'\nreturn llmBoundaryTokenPressure;');
 const pressure=fn(...Object.values(values));
 return {pressure,state};
};

test('64K completions counts actual converted visible schemas at the existing precheck',()=>{
 const old=run(false),current=run(true);
 assert.equal(old.pressure,44000);assert.equal(old.state.conversions,0);
 assert.equal(current.pressure,56000);assert.equal(current.state.conversions,1);
 assert.ok(old.pressure<45875&&current.pressure>45875,'same reserve now sends the unsafe request to native recovery');
 assert.equal(current.state.pressureCalls[1].messages,current.state.history);
 assert.equal(current.state.pressureCalls[1].systemPrompt,'native core');
 assert.equal(current.state.pressureCalls[1].prompt,'owner');
});

test('8K/32K, sub-64K, other agents and APIs retain exact prior precheck behavior',()=>{
 for(const override of [{contextTokenBudget:8192},{contextTokenBudget:32768},{contextTokenBudget:65535},
  {sessionAgentId:'other'},{params:{model:{api:'anthropic-messages'}}}]){
  const current=run(true,override);assert.equal(current.pressure,run(false,override).pressure);
  assert.equal(current.state.conversions,0);
 }
});

test('an explicit empty tool surface adds no schemas and cannot regain a capability',()=>{
 const current=run(true,{effectiveTools:[]});assert.equal(current.pressure,44000);assert.equal(current.state.conversions,0);
});

test('the pinned transport conversion remains read-only and empty converted tools add no pressure',()=>{
 const current=run(true,{buildOpenAICompletionsParams:()=>({})});
 assert.equal(current.pressure,44000);assert.equal(current.state.pressureCalls.length,1);
 const imports=recipe.replacements.find(([,after])=>after.includes('t as buildOpenAICompletionsParams'));
 assert.ok(imports?.[1].includes('./openai-transport-stream-P3cLoEh2.js'));
 assert.equal(recipe.reviewedDependencies['openai-transport-stream-P3cLoEh2.js'],
  '3518e7a4cbcd178eaca8c8f1f3a1d552602a2a05349249f150520871aadea3d6');
});

test('the previous exact installed NONE-cap recipe remains an explicit migration source',()=>{
 assert.ok(recipe.previousReplacements['e07ec6e2c0fed3fca043b731896d3738ec5673ca1641084216f71fa4192076fd']);
});
