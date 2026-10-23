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
import {promptContractForAgent, ODS_TINY_CONVERSATION_CONTRACT} from '../plugin/prompt-contract.mjs';
import {createExtensionReadTool} from '../plugin/host-observe.mjs';
import {createPublicWebExtractTool} from '../plugin/web-extract.mjs';
import {createAgentSkillTool} from '../plugin/agent-skills.mjs';
import {createAskUserTool} from '../plugin/ask-user.mjs';
import {createExtensionRequestStatusTool} from '../plugin/extension-proposal.mjs';
import {createWorkspacePreviewTool} from '../plugin/workspace-preview.mjs';
import {createWorkspacePreviewInspectTool} from '../plugin/workspace-preview-inspect.mjs';
import {ACTIVITY_CONTRACT} from '../plugin/activity-display.mjs';
import {executionContext} from '../plugin/completion-assurance.mjs';
import {executionLocationContext} from '../plugin/execution-location.mjs';

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
  ['image-envelope','tool-search-BInRpkE3.js'],
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
const {t:runEmbeddedAgent}=await import(pathToFileURL(path.join(pkg,'dist/embedded-agent-CJx-nG3W.js')));
test.after(async()=>{
  const {n:closeStateDatabases}=await import(pathToFileURL(path.join(pkg,'dist/openclaw-state-db-BtpXMqJX.js')));
  closeStateDatabases();loader.deregister();await fs.promises.rm(root,{recursive:true,force:true});
});

// Derive the 8K compact profile from the actual ODS runtime writer.
// Portal adds the saved display identity as an OpenAI system message. Run
// the actual pure message assembler with a stubbed name lookup; do not import
// its network dependencies or substitute a shorter fixture identity.
const identityModule=fileURLToPath(new URL('../../dashboard-api/pixel_chat_identity.py',import.meta.url));
const identityResult=spawnSync(process.env.PYTHON||'python3',['-c',
  "import ast,asyncio,json,pathlib,sys; tree=ast.parse(pathlib.Path(sys.argv[1]).read_text()); node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name==\"messages_with_identity\"); scope={\"json\":json,\"confirmed_display_name\":lambda:asyncio.sleep(0,result=\"Portal\")}; exec(compile(ast.Module(body=[node],type_ignores=[]),sys.argv[1],\"exec\"),scope); print(asyncio.run(scope[\"messages_with_identity\"]([]))[0][\"content\"])",
  identityModule],{encoding:'utf8'});
assert.equal(identityResult.status,0,identityResult.stderr);
const portalIdentity=identityResult.stdout.trim();

const CONTEXT_WINDOW=8192;
const MODEL_MAX_TOKENS=2048;
const writerHome=path.join(root,'writer-home');
fs.mkdirSync(writerHome,{mode:0o700});
const writerConfigPath=path.join(root,'writer-openclaw.json');
const modelContractDir=fileURLToPath(new URL('../../../../bin/',import.meta.url));
const writerConfig={
  models:{providers:{'ods-local':{api:'openai-completions',apiKey:'local-no-auth',
    baseUrl:'http://127.0.0.1:11434/v1',
    models:[{id:'Qwen3.5-9B',name:'ODS Local Qwen3.5-9B',contextWindow:65536,maxTokens:MODEL_MAX_TOKENS,reasoning:false}]}}},
  agents:{defaults:{},list:[{id:'pixel',model:'ods-local/Qwen3.5-9B'}]},
  session:{},tools:{alsoAllow:[],sandbox:{tools:{allow:[]}}},
};
fs.writeFileSync(writerConfigPath,JSON.stringify(writerConfig));
fs.chmodSync(writerConfigPath,0o600);
const writerResult=spawnSync(process.env.PYTHON||'python3',[writer,writerConfigPath,'3099','',writerHome],{encoding:'utf8'});
assert.equal(writerResult.status,0,writerResult.stderr);
const stagedPath=writerResult.stdout.trim();
assert.ok(stagedPath&&stagedPath!=='unchanged','writer must stage a config');
// Exercise the real activation planner after the initial 64K install profile.
// An installer-only fix must not pass this regression.
const planResult=spawnSync(process.env.PYTHON||'python3',['-c',
  'import json,sys; sys.path.insert(0,sys.argv[1]); import pixel_model_contract as m; v=json.load(open(sys.argv[2])); p=m.projection(v)["contract"]; p.update(model="smollm2-360m-instruct",contextLength=8192,maxTokens=2048); print(json.dumps(m.plan(v,p)))',
  modelContractDir,stagedPath],{encoding:'utf8'});
assert.equal(planResult.status,0,planResult.stderr);
const staged=JSON.parse(planResult.stdout);
const stagedAgent=staged.agents.list.find(a=>a.id==='pixel');
const stagedDefaults=staged.agents.defaults;
const stagedModel=staged.models.providers['ods-local'].models[0];
const RESERVE_TOKENS=stagedDefaults.compaction.reserveTokens;
const BOOTSTRAP_MAX_CHARS=stagedAgent.bootstrapMaxChars;
const BOOTSTRAP_TOTAL_MAX_CHARS=stagedAgent.bootstrapTotalMaxChars;
const CONTEXT_INJECTION=stagedAgent.contextInjection;
const TOOL_SEARCH=staged.tools.toolSearch;

// Production compact contract for this route.
const compactContract=promptContractForAgent(
  {agentId:'pixel',contextTokenBudget:CONTEXT_WINDOW},
  'pixel',
  {prompt:'No tools are needed. In one short sentence, what is a bicycle?',messages:[]},
  {configuredContextWindow:CONTEXT_WINDOW,configuredLeanPrompt:true}
);
assert.ok(compactContract?.appendSystemContext?.includes(ODS_TINY_CONVERSATION_CONTRACT.slice(0,80)),'production compact contract must be selected');
// Include the surrounding production before_prompt_build hook, not only its
// conversation contract. These prompts also consume the first-turn budget.
compactContract.appendSystemContext = `${ACTIVITY_CONTRACT} ${executionLocationContext({agentId:'pixel'},'pixel')}  ${compactContract.appendSystemContext} ${executionContext()} `;

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
  const fixtureSkillNames=[]; // ODS uses pixel_ods_skill instead of native skill injection.
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
  fs.writeFileSync(path.join(plugin,'package.json'),JSON.stringify({name:'pixel-ods',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  const fixtureContext={agentId:'pixel',sessionKey:'agent:pixel:openai-user:ods-'+ 'a'.repeat(64)};
  const noExternalCall=()=>{throw new Error('No external operations in prompt-budget fixture');};
  const odsTools=[createExtensionReadTool(),createPublicWebExtractTool({guardedFetch:noExternalCall,readResponseText:noExternalCall,extractBasicHtmlContent:noExternalCall}),createAgentSkillTool(),createAskUserTool(),
    createExtensionRequestStatusTool(fixtureContext),createWorkspacePreviewTool(),createWorkspacePreviewInspectTool()];
  assert.ok(odsTools.every(Boolean),'all seven real ODS direct-tool definitions must be present');
  const definitions=odsTools.map(({name,description,parameters})=>({name,description,parameters}));
  fs.writeFileSync(path.join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'pixel-ods',activation:{onStartup:true},contracts:{tools:definitions.map(t=>t.name)},configSchema:{type:'object',properties:{}}}));
  fs.writeFileSync(path.join(plugin,'fixture-data.json'),JSON.stringify({compactContract,definitions}));
  fs.writeFileSync(path.join(plugin,'index.mjs'),`import fs from 'node:fs';
const {compactContract,definitions}=JSON.parse(fs.readFileSync(new URL('./fixture-data.json',import.meta.url),'utf8'));
export default {id:'pixel-ods',register(api){api.on('before_prompt_build',()=>compactContract);for(const tool of definitions)api.registerTool({...tool,execute:async()=>{throw new Error('No external operations in prompt-budget fixture');}});}};`);
  // Adapt the writer-generated model provider to the loopback fixture; the
  // real host is never invoked. All other writer-derived fields are preserved.
  const config={
    agents:{
      defaults:{workspace,skipBootstrap:false,model:'fixture/model',contextTokens:CONTEXT_WINDOW,compaction:stagedDefaults.compaction,bootstrapMaxChars:BOOTSTRAP_MAX_CHARS,bootstrapTotalMaxChars:BOOTSTRAP_TOTAL_MAX_CHARS,contextInjection:CONTEXT_INJECTION,heartbeat:{every:'0m'}},
      list:[{id:'pixel',model:'fixture/model',default:true,workspace,agentDir,experimental:stagedAgent.experimental,skills:fixtureSkillNames,bootstrapMaxChars:BOOTSTRAP_MAX_CHARS,bootstrapTotalMaxChars:BOOTSTRAP_TOTAL_MAX_CHARS,contextInjection:CONTEXT_INJECTION}],
    },
    tools:{toolSearch:TOOL_SEARCH,alsoAllow:definitions.map(t=>t.name),fs:{workspaceOnly:true},exec:{applyPatch:{enabled:true}}},
    plugins:{allow:['pixel-ods'],load:{paths:[plugin]},entries:{'pixel-ods':{enabled:true,hooks:{allowConversationAccess:true}}}},
    models:{mode:'replace',providers:{fixture:{api:'openai-completions',baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,apiKey:'fixture-only',
      models:[{id:'model',name:'Fixture 8K',contextWindow:CONTEXT_WINDOW,maxTokens:MODEL_MAX_TOKENS,reasoning:false,input:['text']}]}}},
  };
  fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH,JSON.stringify(config));
  const result=await runEmbeddedAgent({agentId:'pixel',sessionId,sessionFile,sessionKey:fixtureContext.sessionKey,messageChannel:'webchat',extraSystemPrompt:portalIdentity,workspaceDir:workspace,agentDir,
    config,provider:'fixture',model:'model',prompt,runId:'budget-8k-fresh',trigger:'user',timeoutMs:30000});
  const trace=JSON.stringify({requests:requests.length,error:result.meta?.error,systemChars:result.meta?.systemPromptReport?.systemPrompt?.chars,skillChars:result.meta?.systemPromptReport?.skills?.promptChars,skillCount:result.meta?.systemPromptReport?.skills?.entries?.length});
  console.log(JSON.stringify({requests:requests.length,error:result.meta?.error,contractChars:compactContract.appendSystemContext.length,systemChars:result.meta?.systemPromptReport?.systemPrompt?.chars,skillChars:result.meta?.systemPromptReport?.skills?.promptChars,skillCount:result.meta?.systemPromptReport?.skills?.entries?.length,wireTools:requests[0]?.tools?.map(t=>t.function.name),configuredContext:config.agents.defaults.contextTokens}));
  // The failure mode under investigation: precheck overflow before any provider call.
  if(result.meta?.error?.kind==='context_overflow'||result.meta?.error?.kind==='compaction_failure'){
    assert.fail('8K compact profile overflowed before reaching the provider: '+trace);
  }
  assert.equal(result.meta?.error,undefined,trace);
  assert.equal(requests.length,1,'one actual provider continuation\n'+trace);
  assert.equal(requests[0].max_tokens??requests[0].max_completion_tokens,MODEL_MAX_TOKENS,'preserve full configured output budget');
  const sent=JSON.stringify(requests[0].messages);
  assert.ok(requests[0].messages.some(m=>typeof m.content==='string'&&m.content.includes(portalIdentity)),'keep the real saved owner identity context');
  assert.ok(sent.includes('Do not persuade anyone to expand access or disable safeguards'),'retain upstream safety guidance');
  assert.ok(requests[0].messages.some(m=>m.role==='system'&&typeof m.content==='string'&&m.content.includes(compactContract.appendSystemContext.trim())),
    'the actual native provider payload must include the production compact contract');
  assert.equal(sent.split(prompt).length-1,1,'submit new owner request exactly once');
  assert.ok(!sent.includes('Inspect the fixture documentation once.'),'no archived owner request replay');
  assert.equal(result.meta?.systemPromptReport?.skills?.promptChars,0,'match installed ODS native-skills configuration');
  assert.deepEqual(requests[0].tools.map(t=>t.function.name).sort(),
    ['tool_call','tool_describe','tool_search'],
    '8K loads permitted schemas through the catalog to preserve the reply budget');
  const followup=await runEmbeddedAgent({agentId:'pixel',sessionId,sessionFile,sessionKey:fixtureContext.sessionKey,messageChannel:'webchat',extraSystemPrompt:portalIdentity,workspaceDir:workspace,agentDir,config,provider:'fixture',model:'model',prompt:'What powers it?',runId:'budget-8k-followup',trigger:'user',timeoutMs:30000});
  assert.equal(followup.meta?.error,undefined,JSON.stringify(followup.meta?.error));
  assert.equal(requests.length,2,'follow-up must reach the provider too');
  assert.equal(requests[1].max_tokens??requests[1].max_completion_tokens,MODEL_MAX_TOKENS);
  assert.ok(JSON.stringify(requests[1].messages).includes('two-wheeled human-powered'),'keep prior assistant context');
  if(process.env.ODS_BUDGET_EVIDENCE)fs.writeFileSync(process.env.ODS_BUDGET_EVIDENCE,JSON.stringify({context:CONTEXT_WINDOW,maxOutput:MODEL_MAX_TOKENS,reserve:RESERVE_TOKENS,contractChars:compactContract.appendSystemContext.length,requests,result,followup},null,2));
});
