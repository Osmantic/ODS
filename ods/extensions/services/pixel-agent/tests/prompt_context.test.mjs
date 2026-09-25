import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {prepareOdsTurnContext, admitOdsTurnContext, restoreOdsTurnContext} from '../host/prompt-context.mjs';
import {promptContractForAgent,requireDurableTurnContext} from '../plugin/prompt-contract.mjs';
import {executionPolicy,executionClock} from '../plugin/completion-assurance.mjs';
const config={plugins:{allow:['pixel-ods'],entries:{'pixel-ods':{enabled:true}}}};
const args={config,agentId:'pixel',runId:'run-a',sessionId:'session-a',sessionKey:'key-a',prompt:'Owner EXACT',entries:[],now:()=> '2026-09-25T02:00:00.123Z'};
test('reviewable context helper is exactly the source inserted by the SDK recipe',()=>{
 const source=readFileSync(new URL('../host/prompt-context.mjs',import.meta.url),'utf8').replaceAll('\r\n','\n')
  .replace("import {createHash} from 'node:crypto';\n",'').replaceAll('export ','');
 const manifest=JSON.parse(readFileSync(new URL('../host/openclaw-compaction-budget.json',import.meta.url),'utf8'));
 const injection=manifest.replacements.find(([old])=>old==='function installModelPromptTransform(params) {');
 assert.equal(injection[1],source+'\nfunction installModelPromptTransform(params) {');
});
test('missing runtime capability blocks Pixel before model admission without affecting other agents',()=>{
 assert.equal(requireDurableTurnContext({agentId:'other'},'pixel'),undefined);
 assert.equal(requireDurableTurnContext(args,'pixel').outcome,'block');
 const admitted={...args,odsTurnContextAdmission:prepareOdsTurnContext(args)};
 assert.equal(requireDurableTurnContext(admitted,'pixel'),undefined);
 assert.equal(requireDurableTurnContext({...admitted,sessionId:'changed'},'pixel').outcome,'block');
 assert.equal(requireDurableTurnContext({...admitted,odsTurnContextAdmission:{...admitted.odsTurnContextAdmission}},'pixel').outcome,'block');
});
function make(){const admission=prepareOdsTurnContext(args);return {admission,message:admitOdsTurnContext(admission,{...admission,content:'Historical host fact'})};}
test('clock and content are immutable across retry and restored process',()=>{
 const {admission,message}=make();const entry={type:'custom_message',...message};
 const resumed=prepareOdsTurnContext({...args,entries:[entry],now:()=>{throw Error('clock must not recapture');}});
 assert.throws(()=>{resumed.startedAt='forged';},TypeError);
 assert.throws(()=>{resumed.previous.content='forged';},TypeError);
 assert.throws(()=>{resumed.previous.details.promptSha256='forged';},TypeError);
 assert.equal(resumed.startedAt,admission.startedAt);
 assert.throws(()=>admitOdsTurnContext(resumed,{...resumed,content:'Changed'}),/changed/);
 assert.equal(admitOdsTurnContext(resumed,{...resumed,content:message.content}).content,message.content);
});
test('real identity, exact owner prompt, and source receipt reject stale or altered context',()=>{
 const {message}=make();const entries=[{type:'custom_message',...message}];
 for(const patch of [{sessionKey:'other'},{sessionId:'other'},{prompt:'Owner changed'}])assert.throws(()=>prepareOdsTurnContext({...args,...patch,entries}),/binding mismatch/);
 assert.throws(()=>prepareOdsTurnContext({...args,entries:[...entries,...entries]}),/Duplicate/);
 assert.throws(()=>prepareOdsTurnContext({...args,entries:[{...entries[0],content:'forged'}]}),/binding mismatch/);
});
test('other agents and disabled or denied plugins cannot opt in',()=>{
 for(const patch of [{agentId:'other'},{config:{plugins:{enabled:false}}},{config:{plugins:{...config.plugins,deny:['pixel-ods']}}}]){
  const admission=prepareOdsTurnContext({...args,...patch});assert.equal(admission,undefined);
  assert.throws(()=>admitOdsTurnContext(admission,{content:'forged'}),/outside/);
 }
});
test('cancel before admission or publication never returns a context',()=>{
 const controller=new AbortController();controller.abort();
 assert.throws(()=>prepareOdsTurnContext({...args,signal:controller.signal}),/cancelled/);
 const {admission}=make();assert.throws(()=>admitOdsTurnContext(admission,{...admission,content:'fact'},controller.signal),/cancelled/);
});
test('compaction restores exact current context without duplicate transcript or owner modification',()=>{
 const {message}=make();const owner={role:'user',content:[{type:'text',text:args.prompt}]};const tool={role:'toolResult',content:'untrusted'};
 const history=[{role:'assistant',content:'summary'},owner,tool];const result=restoreOdsTurnContext(history,message,args.prompt);
 assert.deepEqual(result,[history[0],message,owner,tool]);assert.equal(result[2],owner);assert.equal(history.length,3);
 assert.equal(restoreOdsTurnContext(result,message,args.prompt),result);
 assert.throws(()=>restoreOdsTurnContext([...result,message],message,args.prompt),/differs/);
 for(const field of ['source','sessionId','sessionKey','promptSha256','startedAt','contentSha256']){
  const altered={...message,details:{...message.details,[field]:'changed'}};
  assert.throws(()=>restoreOdsTurnContext([altered,owner],message,args.prompt),/differs/);
 }
});
test('production stable profiles retain managed role authority in the system policy',()=>{
 const context={agentId:'pixel',contextTokenBudget:32768};
 const get=role=>promptContractForAgent(context,'pixel',{prompt:`Identity: Portal\n\nYou are the ${role} in the owner's Portal team.\nOwner request: build and publish a website.`},{stableContext:true});
 assert.match(get('Coordinator').appendSystemContext,/Return only JSON/);
 assert.match(get('Coordinator').appendSystemContext,/Do not perform the task/);
 assert.match(get('Reviewer').appendSystemContext,/read-only Reviewer/);
 assert.match(get('Reviewer').appendSystemContext,/Do not create files, run commands/);
 assert.equal(get('Reviewer').turnContext,'');assert.equal(get('Coordinator').turnContext,'');
 assert.match(get('Builder').appendSystemContext,/private ODS assistant/);
 assert.notEqual(get('Builder').appendSystemContext,get('Reviewer').appendSystemContext);
 assert.equal(promptContractForAgent({agentId:'other'},'pixel',{}, {stableContext:true}),undefined);
 assert.match(executionPolicy(),/matching native verified readback receipt/);
 assert.match(executionPolicy(),/Read fresh bytes when the receipt is missing, truncated, stale/);
 const receiptPolicy=promptContractForAgent(context,'pixel',{}, {stableContext:true,fileVersionAdmissionAvailable:true}).appendSystemContext;
 assert.match(receiptPolicy,/current model-visible bytes backed by a matching native file receipt/);
 assert.doesNotMatch(receiptPolicy,/read before edit\/apply_patch/);
 assert.notEqual(receiptPolicy,promptContractForAgent(context,'pixel',{}, {stableContext:true}).appendSystemContext);
});
test('same capabilities keep policy stable across task wording, verification and clock',()=>{
 const context={agentId:'pixel',contextTokenBudget:32768};const options={stableContext:true,executionHost:'sandbox',privateBrowserAccess:false};
 const a=promptContractForAgent(context,'pixel',{prompt:'Build a website'},options);
 const b=promptContractForAgent(context,'pixel',{prompt:'Search current news'}, {...options,verificationStatus:'failed'});
 assert.equal(a.appendSystemContext,b.appendSystemContext);assert.notEqual(a.turnContext,b.turnContext);
 assert.notEqual(a.appendSystemContext,promptContractForAgent(context,'pixel',{}, {...options,privateBrowserAccess:true}).appendSystemContext);
 assert.notEqual(a.appendSystemContext,promptContractForAgent(context,'pixel',{}, {...options,executionHost:'gateway'}).appendSystemContext);
 assert.ok(Buffer.byteLength(a.appendSystemContext+' '+executionPolicy())<7500,'selected policy has a bounded byte budget');assert.doesNotMatch(a.appendSystemContext,/2026-|HOST FACT/);
 assert.doesNotMatch(executionPolicy(),/2026-|Current time/);assert.match(executionClock(args.now()),/2026-09-25T02:00:00.123Z/);
});
