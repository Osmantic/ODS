// Explicit manual qualification: real local chat model, pinned SDK and Laya.
// Not an installed-Portal test: activation is injected; file tools only touch this fixture.
// Requires the environment documented in docs/PORTAL-LAYA.md. Leaves evidence for review.
// Test-only report tools cannot access any path supplied by the model.
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,existsSync} from 'node:fs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {randomBytes} from 'node:crypto';
import {createServer} from 'node:net';
import {join,resolve} from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';
const scenario=process.argv[2] || 'success';
const chatModel=process.env.LAYA_CHAT_MODEL;
assert.ok(chatModel,'Set LAYA_CHAT_MODEL to an installed Ollama model.');
assert.ok(['success','unavailable','baseline','ordinary'].includes(scenario));
const required=name=>{assert.ok(process.env[name],`Set ${name}.`);return process.env[name];};
const artifacts=resolve(required('LAYA_QUALIFICATION_OUTPUT'));
mkdirSync(artifacts,{recursive:true,mode:0o700});
const keyFile=resolve(required('LAYA_TEST_KEY_FILE'));
const layaPort=Number(required('LAYA_TEST_PORT'));
assert.ok(Number.isInteger(layaPort)&&layaPort>0&&layaPort<65536);
const ollamaUrl=new URL(required('LAYA_OLLAMA_URL'));
assert.equal(ollamaUrl.protocol,'http:');assert.equal(ollamaUrl.hostname,'127.0.0.1');
assert.equal(ollamaUrl.pathname,'/');assert.equal(ollamaUrl.username+ollamaUrl.password+ollamaUrl.search+ollamaUrl.hash,'');
const gatewayToken=randomBytes(32).toString('hex');
const root=mkdtempSync(join(artifacts,'laya-chat-journey-'));
const workspace=join(root,'workspace');mkdirSync(workspace);
const plugin=join(root,'plugin');mkdirSync(plugin);
const core=fileURLToPath(new URL('../plugin/',import.meta.url));
const pkg=resolve(required('OPENCLAW_PACKAGE_DIR'));
const pinned=JSON.parse(readFileSync(new URL('../runtime-source/source-lock.json',import.meta.url),'utf8')).runtimeVersion;
assert.equal(JSON.parse(readFileSync(join(pkg,'package.json'),'utf8')).version,pinned,'Use the pinned Portal runtime.');
const reservation=createServer();
await new Promise((ready,reject)=>{reservation.once('error',reject);reservation.listen(0,'127.0.0.1',ready);});
const port=reservation.address().port;
// Keep the unavailable endpoint reserved by this process without an HTTP handler.
const outage=createServer(socket=>socket.destroy());
await new Promise((ready,reject)=>{outage.once('error',reject);outage.listen(0,'127.0.0.1',ready);});
const unavailablePort=outage.address().port;
console.log(JSON.stringify({root,scenario,chatModel,scope:'isolated-model-journey',phase:'started'}));
const report=join(workspace,'report.csv');
const evidence=join(root,'events.jsonl');
writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'laya-journey',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'pixel-ods',activation:{onStartup:true},contracts:{tools:['pixel_ods_laya','pixel_ods_skill','save_report','read_report']},configSchema:{type:'object',properties:{}}}));
writeFileSync(join(plugin,'index.mjs'),`
import {readFileSync,writeFileSync,appendFileSync} from 'node:fs';
import {createLayaClient} from ${JSON.stringify(pathToFileURL(join(core,'laya-client.mjs')).href)};
import {createAgentSkillTool} from ${JSON.stringify(pathToFileURL(join(core,'agent-skills.mjs')).href)};
import {createLayaRuntime} from ${JSON.stringify(pathToFileURL(join(core,'laya-runtime.mjs')).href)};
const save=(value)=>appendFileSync(${JSON.stringify(evidence)},JSON.stringify(value)+'\\n');
const client=createLayaClient({port:${scenario==='unavailable'?unavailablePort:layaPort},token:readFileSync(${JSON.stringify(keyFile)},'utf8').trim()});
export default {id:'pixel-ods',register(api){
  const runtime=createLayaRuntime({readConnection:()=>({port:${scenario==='unavailable'?unavailablePort:layaPort},token:'unused-by-injected-client'}),clientFactory:()=>client});
  api.on('before_prompt_build',()=>({prependSystemContext:runtime.promptHint()}));
  const skill=createAgentSkillTool();const skillRun=skill.execute;skill.execute=async(...args)=>{const result=await skillRun(...args);save({tool:'skill',topic:args[1]?.topic});return result;};api.registerTool(skill);
  const laya=runtime.offered();
  const run=laya.execute;
  laya.execute=async(...args)=>{const result=await run(...args);save({tool:'laya',args:args[1],result});return result;};
  api.registerTool(laya);
  api.registerTool({name:'save_report',label:'Save report',description:'Save the requested UTF-8 CSV to report.csv in the test workspace. Overwrites only that test file.',parameters:{type:'object',required:['content'],additionalProperties:false,properties:{content:{type:'string'}}},async execute(id,args){
    if(typeof args.content!=='string'||args.content.length>8000)throw new Error('Invalid report');
    writeFileSync(${JSON.stringify(report)},args.content);save({tool:'save',length:args.content.length});return {content:[{type:'text',text:'Saved report.csv.'}]};
  }});
  api.registerTool({name:'read_report',label:'Read report',description:'Read report.csv from the test workspace to verify the saved report.',parameters:{type:'object',properties:{},additionalProperties:false},async execute(){const content=readFileSync(${JSON.stringify(report)},'utf8');save({tool:'read'});return {content:[{type:'text',text:content}]};}});
}};
`);

const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},
  gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:gatewayToken},http:{endpoints:{chatCompletions:{enabled:true}}}},
  agents:{defaults:{workspace,skipBootstrap:true,sandbox:{mode:'off'},model:{primary:'fixture/'+chatModel},models:{['fixture/'+chatModel]:{params:{temperature:0.6}}},contextTokens:32768,heartbeat:{every:'0m'}},list:[{id:'pixel',default:true,workspace}]},
  models:{mode:'replace',providers:{fixture:{baseUrl:ollamaUrl.origin,api:'ollama',apiKey:'ollama-local',models:[{id:chatModel,name:'Local Qwen qualification',contextWindow:32768,maxTokens:4096,params:{num_ctx:32768},reasoning:false,input:['text']}]}}},
  tools:{allow:['pixel_ods_laya','pixel_ods_skill','save_report','read_report'],toolSearch:{enabled:false}},
  plugins:{allow:['pixel-ods','ollama'],load:{paths:[plugin]},entries:{'pixel-ods':{enabled:true},ollama:{enabled:true}}}};
writeFileSync(join(root,'openclaw.json'),JSON.stringify(config),{mode:0o600});
await new Promise(resolve=>reservation.close(resolve));
let log='';
const child=spawn(process.execPath,[join(pkg,'openclaw.mjs'),'gateway','run'],{cwd:root,windowsHide:true,
  env:{...process.env,HOME:root,USERPROFILE:root,OPENCLAW_STATE_DIR:join(root,'state'),OPENCLAW_CONFIG_PATH:join(root,'openclaw.json'),OPENCLAW_SKIP_CHANNELS:'1'},stdio:['ignore','pipe','pipe']});
child.stdout.on('data',chunk=>{log+=chunk;});child.stderr.on('data',chunk=>{log+=chunk;});
try{
  let ready=false;
  for(let i=0;i<180;i++){
    try {ready=(await fetch('http://127.0.0.1:'+port+'/health',{signal:AbortSignal.timeout(500)})).ok;}
    catch(error){if(!(error instanceof TypeError)&&error.name!=='TimeoutError')throw error;}
    if(ready)break;
    assert.equal(child.exitCode,null,log);await delay(500);
  }
  assert.ok(ready,log);
  const started=performance.now();
  const response=await fetch('http://127.0.0.1:'+port+'/v1/chat/completions',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+gatewayToken},body:JSON.stringify({model:'openclaw:pixel',stream:false,user:'laya-real-model-qa',messages:[{role:'user',content:scenario==='ordinary'?'Responda em portugues: quanto e 2 mais 3?':(scenario==='baseline'?'Classifique sem consultar o Laya':'Use o Laya para classificar')+' estes chamados nas categorias billing, technical ou other: ticket1: Fui cobrado duas vezes, quero reembolso. ticket2: O aplicativo trava ao fazer login, aparece um erro de software. ticket3: Qual e o horario da reuniao de amanha? Salve o resultado em CSV com colunas id,categoria usando save_report, confira com read_report e responda em portugues com um resumo curto.'}]}),signal:AbortSignal.timeout(180000)});
  const body=await response.text();writeFileSync(join(root,'response.json'),body);
  const milliseconds=Math.round(performance.now()-started);
  writeFileSync(join(root,'observation.json'),JSON.stringify({scenario,chatModel,milliseconds,httpStatus:response.status,scope:'isolated-model-journey'}));
  assert.equal(response.status,200,body+'\n'+log);
  const events=existsSync(evidence)?readFileSync(evidence,'utf8').trim().split('\n').map(JSON.parse):[];
  const calls=events.filter(x=>x.tool==='laya');
  if(['success','unavailable'].includes(scenario))assert.ok(calls.length>0,'model did not use Laya');
  else assert.equal(calls.length,0,'unnecessary Laya call');
  if(scenario==='success')assert.ok(calls.some(x=>x.result.details.status==='completed'),'no decision accepted');
  if(scenario==='unavailable'){assert.equal(calls.length,1,'more than one failed consultation: inspect events before accepting this model');assert.equal(calls[0].result.isError,true);assert.notEqual(calls[0].result.details.status,'completed');}
  if(scenario!=='ordinary'){
  assert.ok(events.some(x=>x.tool==='save'));
  assert.ok(events.findLastIndex(x=>x.tool==='read')>events.findLastIndex(x=>x.tool==='save'),'final write was not read back');
  const csv=readFileSync(report,'utf8');
  assert.match(csv,/ticket1[^\n]*billing/);assert.match(csv,/ticket2[^\n]*technical/);assert.match(csv,/ticket3[^\n]*other/);
  } else assert.equal(events.length,0,'unnecessary tool use');
  const final=JSON.parse(body).choices[0].message.content;
  if(scenario==='unavailable')assert.match(final,/Laya[\s\S]{0,140}(?:indispon|falh|n[a\u00e3]o|inacess|erro)|(?:indispon|falh|n[a\u00e3]o|inacess)[\s\S]{0,140}Laya/i);
  if(scenario==='ordinary')assert.match(final,/5|cinco/);
  assert.ok(final.trim().length>0);
  console.log(JSON.stringify({root,scenario,chatModel,scope:'isolated-model-journey',verified:true,milliseconds,events:events.map(e=>e.tool),final}));
}finally{
  writeFileSync(join(root,'gateway.log'),log);outage.close();
  if(child.exitCode===null){const ended=once(child,'exit');child.kill();await ended;}
}
