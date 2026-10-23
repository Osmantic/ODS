// Real pinned runner + deterministic loopback provider; no installed state/model.
// Regression for the fresh 8K compact-profile chat overflow.
// This is a runtime budgeting regression, not a model-quality or tokenizer test.
//
// The 8K profile is derived from the actual ODS runtime writer
// (ods/installers/lib/pixel-runtime-budget.py) via subprocess using a minimal
// ODS config, then the generated model provider is adapted to a loopback
// fixture. The real host is never invoked.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {once} from 'node:events';
import {createHash} from 'node:crypto';
import {registerHooks} from 'node:module';
import {pathToFileURL, fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {promptContractForAgent, ODS_COMPACT_CONVERSATION_CONTRACT} from '../plugin/prompt-contract.mjs';

const pkg=process.env.OPENCLAW_PACKAGE_DIR;
assert.ok(pkg,'provide the pinned reviewed OpenClaw package');
const writer=process.env.ODS_RUNTIME_BUDGET_WRITER || fileURLToPath(new URL('../../../../installers/lib/pixel-runtime-budget.py',import.meta.url));
const root=fs.mkdtempSync(path.join(os.tmpdir(),'ods-8k-budget-'));
process.env.OPENCLAW_STATE_DIR=path.join(root,'state');
process.env.OPENCLAW_CONFIG_PATH=path.join(root,'openclaw.json');
const sha=value=>createHash('sha256').update(value).digest('hex');
const sources=new Map();
for(const [name,module] of [
  ['compaction-empty','proxy-Bsfwfsp-.js'],
  ['compaction-no-work','compact-DuWIsaq_.js'],
  ['compaction-resume','sessions-CZbwb3_c.js'],
  ['compaction-budget','selection-BEwSQKM-.js'],
]) {
  const manifest=JSON.parse(fs.readFileSync(new URL(`../host/openclaw-${name}.json`,import.meta.url)));
  const filename=path.join(pkg,'dist',module);
  let source=fs.readFileSync(filename,'utf8');
  const digest=sha(source);
  const applied=digest===manifest.patchedSha256?manifest.replacements:manifest.previousReplacements?.[digest];
  if(applied)for(const [before,after] of [...applied].reverse()){
    assert.equal(source.split(after).length,2);source=source.replace(after,before);
  }
  assert.equal(sha(source),manifest.sourceSha256,'reject unreviewed native bytes');
  for(const [before,after] of manifest.replacements){assert.equal(source.split(before).length,2);source=source.replace(before,after);}
  assert.equal(sha(source),manifest.patchedSha256);
  sources.set(pathToFileURL(filename).href,source);
}
const loader=registerHooks({load(url,context,next){return sources.has(url)
  ? {format:'module',source:sources.get(url),shortCircuit:true}:next(url,context);}});
const {t:buildWorkspaceSkillSnapshot}=await import(pathToFileURL(path.join(pkg,'dist/workspace-BPpGiOak.js')));
const {t:runEmbeddedAgent}=await import(pathToFileURL(path.join(pkg,'dist/embedded-agent-CJx-nG3W.js')));
test.after(async()=>{
  const {n:closeStateDatabases}=await import(pathToFileURL(path.join(pkg,'dist/openclaw-state-db-BtpXMqJX.js')));
  closeStateDatabases();loader.deregister();await fs.promises.rm(root,{recursive:true,force:true});
});

// Derive the 8K compact profile from the actual ODS runtime writer.
const CONTEXT_WINDOW=8192;
const MODEL_MAX_TOKENS=2048;
const writerHome=path.join(root,'writer-home');
fs.mkdirSync(writerHome,{mode:0o700});
const writerConfigPath=path.join(root,'writer-openclaw.json');
const writerConfig={
  models:{providers:{'ods-local':{api:'openai-completions',apiKey:'local-no-auth',
    baseUrl:'http://127.0.0.1:11434/v1',
    models:[{id:'Qwen3.5-9B',name:'ODS Local Qwen3.5-9B',contextWindow:CONTEXT_WINDOW,maxTokens:MODEL_MAX_TOKENS,reasoning:false}]}}},
  agents:{defaults:{},list:[{id:'pixel',model:'ods-local/Qwen3.5-9B'}]},
  session:{},tools:{alsoAllow:[],sandbox:{tools:{allow:[]}}},
};
fs.writeFileSync(writerConfigPath,JSON.stringify(writerConfig));
fs.chmodSync(writerConfigPath,0o600);
const writerResult=spawnSync(process.env.PYTHON||'python3',[writer,writerConfigPath,'3099','',writerHome],{encoding:'utf8'});
assert.equal(writerResult.status,0,writerResult.stderr);
const stagedPath=writerResult.stdout.trim();
assert.ok(stagedPath&&stagedPath!=='unchanged','writer must stage a config');
const staged=JSON.parse(fs.readFileSync(stagedPath,'utf8'));
const stagedAgent=staged.agents.list.find(a=>a.id==='pixel');
const stagedDefaults=staged.agents.defaults;
const stagedModel=staged.models.providers['ods-local'].models[0];
const RESERVE_TOKENS=stagedDefaults.compaction.reserveTokens;
const BOOTSTRAP_MAX_CHARS=stagedAgent.bootstrapMaxChars;
const BOOTSTRAP_TOTAL_MAX_CHARS=stagedAgent.bootstrapTotalMaxChars;
const CONTEXT_INJECTION=stagedAgent.contextInjection;
const TOOL_SEARCH=staged.tools.toolSearch;
const SKILLS_LIMITS=stagedAgent.skillsLimits;

// Production compact contract for this route.
const compactContract=promptContractForAgent(
  {agentId:'pixel',contextTokenBudget:CONTEXT_WINDOW},
  'pixel',
  {prompt:'No tools are needed. In one short sentence, what is a bicycle?',messages:[]},
  {configuredContextWindow:CONTEXT_WINDOW,configuredLeanPrompt:true}
);
assert.ok(compactContract?.appendSystemContext?.includes(ODS_COMPACT_CONVERSATION_CONTRACT.slice(0,80)),'production compact contract must be selected');

// Production bootstrap shape: a small BOOTSTRAP.md and a small AGENTS.md,
// bounded by the per-agent 2000/6000 budget and contextInjection 'never'.
const bootstrapText='# BOOTSTRAP\n\n' + 'Owner workspace bootstrap context. '.repeat(40);
const agentsText='# AGENTS\n\n' + 'Agent operating notes. '.repeat(40);

test('8K compact-profile fresh chat reaches provider with compact contract and full output budget', {timeout:60000}, async t=>{
  const requests=[];
  const upstream=http.createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    requests.push(JSON.parse(Buffer.concat(chunks)));
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{role:'assistant',content:'A bicycle is a two-wheeled human-powered vehicle.'},finish_reason:null}]})+'\n\n');
    res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:4000,completion_tokens:12,total_tokens:4012}})+'\n\ndata: [DONE]\n\n');
  });
  upstream.listen(0,'127.0.0.1');await once(upstream,'listening');
  t.after(async()=>{upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));});
  const workspace=path.join(root,'workspace'),agentDir=path.join(root,'agent');
  fs.mkdirSync(workspace);fs.mkdirSync(agentDir);
  // A representative 15-skill catalog, independent of CI-installed binaries
  // and credentials. The native loader, compact renderer and limits are real.
  const fixtureSkillNames=Array.from({length:15},(_,i)=>`budget-guide-${String(i).padStart(2,'0')}`);
  for(const name of fixtureSkillNames){
    const dir=path.join(workspace,'skills',name);fs.mkdirSync(dir,{recursive:true});
    const description='Specialized instructions for this fixture workflow. Read the guide before performing its task, inspect the relevant inputs and results, and retain the owner requirements throughout the work.';
    fs.writeFileSync(path.join(dir,'SKILL.md'),`---\nname: ${name}\ndescription: ${description}\n---\n\nPreserve the owner instructions and verify the result.\n`);
  }
  fs.writeFileSync(path.join(workspace,'BOOTSTRAP.md'),bootstrapText);
  fs.writeFileSync(path.join(workspace,'AGENTS.md'),agentsText);
  const sessionId='1bbbbbbb-2222-4333-8444-555555555555',sessionFile=path.join(root,sessionId+'.jsonl');
  const now=Date.now();
  const rows=[
    {type:'session',version:3,id:sessionId,timestamp:new Date(now).toISOString(),cwd:workspace},
  ];
  const before=rows.map(JSON.stringify).join('\n')+'\n';fs.writeFileSync(sessionFile,before);
  const prompt='No tools are needed. In one short sentence, what is a bicycle?';
  const plugin=path.join(root,'plugin');fs.mkdirSync(plugin);
  fs.writeFileSync(path.join(plugin,'package.json'),JSON.stringify({name:'ods-8k-budget-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  fs.writeFileSync(path.join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'ods-8k-budget-fixture',activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
  fs.writeFileSync(path.join(plugin,'index.mjs'),`export default {id:'ods-8k-budget-fixture',register(api){api.on('before_prompt_build',()=>(${JSON.stringify(compactContract)}));}};`);
  // Adapt the writer-generated model provider to the loopback fixture; the
  // real host is never invoked. All other writer-derived fields are preserved.
  const config={
    agents:{
      defaults:{workspace,skipBootstrap:false,model:'fixture/model',contextTokens:CONTEXT_WINDOW,compaction:stagedDefaults.compaction,bootstrapMaxChars:BOOTSTRAP_MAX_CHARS,bootstrapTotalMaxChars:BOOTSTRAP_TOTAL_MAX_CHARS,contextInjection:CONTEXT_INJECTION,heartbeat:{every:'0m'}},
      list:[{id:'pixel',model:'fixture/model',default:true,workspace,agentDir,experimental:stagedAgent.experimental,skills:fixtureSkillNames,skillsLimits:SKILLS_LIMITS,bootstrapMaxChars:BOOTSTRAP_MAX_CHARS,bootstrapTotalMaxChars:BOOTSTRAP_TOTAL_MAX_CHARS,contextInjection:CONTEXT_INJECTION}],
    },
    tools:{toolSearch:TOOL_SEARCH},
    plugins:{allow:['ods-8k-budget-fixture'],load:{paths:[plugin]},entries:{'ods-8k-budget-fixture':{enabled:true,hooks:{allowConversationAccess:true}}}},
    models:{mode:'replace',providers:{fixture:{api:'openai-completions',baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,apiKey:'fixture-only',
      models:[{id:'model',name:'Fixture 8K',contextWindow:CONTEXT_WINDOW,maxTokens:MODEL_MAX_TOKENS,reasoning:false,input:['text']}]}}},
  };
  const fullSkillsConfig=structuredClone(config);
  delete fullSkillsConfig.agents.list[0].skillsLimits;
  const fullSkills=buildWorkspaceSkillSnapshot(workspace,{config:fullSkillsConfig,agentId:'pixel'});
  fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH,JSON.stringify(config));
  const result=await runEmbeddedAgent({agentId:'pixel',sessionId,sessionFile,sessionKey:'agent:pixel:budget-8k',workspaceDir:workspace,agentDir,
    config,provider:'fixture',model:'model',prompt,runId:'budget-8k-fresh',trigger:'user',timeoutMs:30000});
  const trace=JSON.stringify({requests:requests.length,error:result.meta?.error,systemChars:result.meta?.systemPromptReport?.systemPrompt?.chars,skillChars:result.meta?.systemPromptReport?.skills?.promptChars,skillCount:result.meta?.systemPromptReport?.skills?.entries?.length});
  console.log(JSON.stringify({requests:requests.length,error:result.meta?.error,contractChars:compactContract.appendSystemContext.length,systemChars:result.meta?.systemPromptReport?.systemPrompt?.chars,skillChars:result.meta?.systemPromptReport?.skills?.promptChars,skillCount:result.meta?.systemPromptReport?.skills?.entries?.length}));
  // The failure mode under investigation: precheck overflow before any provider call.
  if(result.meta?.error?.kind==='context_overflow'||result.meta?.error?.kind==='compaction_failure'){
    assert.fail('8K compact profile overflowed before reaching the provider: '+trace);
  }
  assert.equal(result.meta?.error,undefined,trace);
  assert.equal(requests.length,1,'one actual provider continuation\n'+trace);
  assert.equal(requests[0].max_tokens??requests[0].max_completion_tokens,MODEL_MAX_TOKENS,'preserve full configured output budget');
  const sent=JSON.stringify(requests[0].messages);
  assert.ok(requests[0].messages.some(m=>m.role==='system'&&typeof m.content==='string'&&m.content.includes(compactContract.appendSystemContext)),
    'the actual native provider payload must include the production compact contract');
  assert.equal(sent.split(prompt).length-1,1,'submit new owner request exactly once');
  assert.ok(!sent.includes('Inspect the fixture documentation once.'),'no archived owner request replay');
  // Compare the native full and compact catalogs, not a list with itself.
  const fullReport=result.meta?.systemPromptReport?.skills;
  assert.ok(fullReport,'system prompt report must include skills section');
  const fullNames=fullSkills.resolvedSkills.map(e=>e.name).sort();
  const compactNames=(fullReport.entries||[]).map(e=>e.name).sort();
  assert.deepEqual(compactNames,fullNames,'compact profile must not drop fixture skills');
  assert.deepEqual(fullNames,fixtureSkillNames,'exercise every fixture skill');
  assert.deepEqual(requests[0].tools.map(t=>t.function.name).sort(),['tool_call','tool_describe','tool_search'],'retain native tool discovery and invocation');
  assert.ok(fullReport.promptChars<=SKILLS_LIMITS.maxSkillsPromptChars,
    `skills prompt chars ${fullReport.promptChars} must fit managed cap ${SKILLS_LIMITS.maxSkillsPromptChars}`);
  const followup=await runEmbeddedAgent({agentId:'pixel',sessionId,sessionFile,sessionKey:'agent:pixel:budget-8k',workspaceDir:workspace,agentDir,config,provider:'fixture',model:'model',prompt:'What powers it?',runId:'budget-8k-followup',trigger:'user',timeoutMs:30000});
  assert.equal(followup.meta?.error,undefined,JSON.stringify(followup.meta?.error));
  assert.equal(requests.length,2,'follow-up must reach the provider too');
  assert.equal(requests[1].max_tokens??requests[1].max_completion_tokens,MODEL_MAX_TOKENS);
  assert.ok(JSON.stringify(requests[1].messages).includes('two-wheeled human-powered'),'keep prior assistant context');
  if(process.env.ODS_BUDGET_EVIDENCE)fs.writeFileSync(process.env.ODS_BUDGET_EVIDENCE,JSON.stringify({context:CONTEXT_WINDOW,maxOutput:MODEL_MAX_TOKENS,reserve:RESERVE_TOKENS,contractChars:compactContract.appendSystemContext.length,requests,result,followup},null,2));
});
