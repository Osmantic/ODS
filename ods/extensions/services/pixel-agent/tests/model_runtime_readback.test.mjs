import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {readRuntimeModel} from '../plugin/model-runtime-readback.mjs';
const config=()=>({agents:{defaults:{compaction:{reserveTokens:6554,reserveTokensFloor:8192,keepRecentTokens:1024}},list:[{id:'pixel',model:'ods-gateway/ods/current',contextTokens:16384,params:{maxTokens:4096}}]},
  models:{providers:{'ods-gateway':{apiKey:'PRIVATE',baseUrl:'http://private',models:[{id:'ods/current',name:'ODS Current (Qwen-27B)',contextWindow:16384,maxTokens:4096,reasoning:false}]}}},
  plugins:{entries:{'pixel-ods':{enabled:true,config:{modelContextWindow:16384,modelRouteFingerprint:'a'.repeat(64)}}}}});
test('current model projection includes exact provider identity and active budgets without secrets',()=>{
  const result=readRuntimeModel(config(),{pid:1,revision:'b'.repeat(64),observedAt:'2026-09-16T00:00:00.000Z'});
  assert.deepEqual(result.contract,{model:'Qwen-27B',contextLength:16384,maxTokens:4096,reasoning:false,routeFingerprint:'a'.repeat(64)});
  assert.equal(result.limits.contextTokens,16384);assert.equal(result.limits.maxOutputTokens,4096);
  assert.equal(JSON.stringify(result).includes('PRIVATE'),false);assert.equal(JSON.stringify(result).includes('http'),false);
});
test('unknown routes, malformed identity and invalid capacity fail closed',()=>{
  for(const change of [c=>c.agents.list[0].model='other/model',c=>c.models.providers['ods-gateway'].models[0].maxTokens=20000,
    c=>c.plugins.entries['pixel-ods'].config.modelRouteFingerprint+='\n',c=>c.plugins.entries['pixel-ods'].config.managedProvider={}]){
    const c=config();change(c);assert.throws(()=>readRuntimeModel(c,{}));
  }
});
test('Python owner projection and current-process JavaScript projection agree',()=>{
  const cases=[config()];
  let c=config();delete c.plugins.entries['pixel-ods'].config.modelRouteFingerprint;delete c.agents.list[0].contextTokens;delete c.agents.list[0].params;cases.push(c);
  c=config();c.agents.list[0].params={max_completion_tokens:2048,temperature:0.7};cases.push(c);
  c=config();c.agents.list[0].params.maxTokens=null;cases.push(c);
  c=config();c.agents.defaults.params={max_tokens:3072};delete c.agents.list[0].params;cases.push(c);
  c=config();c.agents.defaults.params={maxTokens:2048};c.agents.defaults.models={'ods-gateway/ods/current':{params:{max_completion_tokens:3072}}};delete c.agents.list[0].params;cases.push(c);
  c=config();c.agents.defaults.params={maxTokens:2048};c.agents.defaults.models={'ods-gateway/ods/current':{params:{max_tokens:3072}}};cases.push(c);
  c=config();c.agents.list[0].model='ods-local/local-model';c.models.providers['ods-local']={models:[{id:'local-model',name:'ODS Local local-model',contextWindow:32768,maxTokens:4096,reasoning:true}]};delete c.plugins.entries['pixel-ods'].config.modelRouteFingerprint;cases.push(c);
  const script=`import json,sys\nsys.path.insert(0,sys.argv[1])\nfrom pixel_model_contract import projection\nfor config in json.load(sys.stdin):\n try: print(json.dumps(projection(config)))\n except Exception: print('null')\n`;
  const result=spawnSync(process.platform==='win32'?'python':'python3',['-c',script,fileURLToPath(new URL('../../../../bin',import.meta.url))],{input:JSON.stringify(cases),encoding:'utf8',windowsHide:true});
  assert.equal(result.status,0,result.stderr);
  const expected=result.stdout.trim().split('\n').map(JSON.parse);
  cases.forEach((value,index)=>{
    if(expected[index]===null)return assert.throws(()=>readRuntimeModel(value,{}));
    const {contract,limits}=readRuntimeModel(value,{});
    assert.deepEqual({contract,limits},expected[index]);
  });
});
