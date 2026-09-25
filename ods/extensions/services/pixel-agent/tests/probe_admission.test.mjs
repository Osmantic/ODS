import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {createProbeIngressHeader,consumeProbeGatewayAdmission,odsAdmissionCanonical,odsAdmissionMac,odsAdmissionSha} from '../host/probe_admission.mjs';
import {createOwnedProbeFetch} from '../host/probe_fetch.mjs';

const supported=typeof process.getuid==='function';
function fixture(){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'ods-admission-test-'));fs.chmodSync(root,0o700);
 const incoming={user:'owned-chat',request_id:'turn-'+crypto.randomUUID().replaceAll('-',''),history_snapshot:{version:1},messages:[{role:'user',content:'fixture private 😀'}]};
 const user='ods-owned-user',sessionKey='agent:pixel:owned',gatewayBody=JSON.stringify({model:'openclaw:pixel',user,messages:incoming.messages,stream:false});
 const a={schemaVersion:1,agentId:'pixel',ownerUid:process.getuid(),scopeId:crypto.randomUUID(),signingKey:crypto.randomBytes(32).toString('hex'),expiresAtMs:Date.now()+10000,maxAttempts:16,user,requestId:incoming.request_id,sessionKey,sessionId:'owned-session',provider:'ods-local',modelId:'ods/current',baseUrl:'http://127.0.0.1:4102/v1'};
 a.incomingHmac=odsAdmissionMac(a.signingKey,odsAdmissionCanonical(incoming));
 const file=path.join(root,'authorize-'+odsAdmissionSha(user+'\0'+a.requestId)+'.json');fs.writeFileSync(file,JSON.stringify(a),{mode:0o600});
 return {root,incoming,user,sessionKey,gatewayBody,a,file,request:{incoming,user,requestId:a.requestId,gatewayBody},close:()=>fs.rmSync(root,{recursive:true,force:true})};
}
test('exact original request and sanitized gateway bytes bind only one generated run and pre-existing session',{skip:!supported},async()=>{
 const f=fixture();try{
  const h=createProbeIngressHeader(f.request,{root:f.root});assert(h['X-ODS-Probe-Admission']);assert.deepEqual(createProbeIngressHeader(f.request,{root:f.root}),{});
  const runId='chatcmpl_'+crypto.randomUUID();const admitted={header:h['X-ODS-Probe-Admission'],payload:JSON.parse(f.gatewayBody),sessionKey:f.sessionKey,runId,agentId:'pixel'};
  assert(consumeProbeGatewayAdmission(admitted,{root:f.root}));assert(!consumeProbeGatewayAdmission({...admitted,runId:'chatcmpl_'+crypto.randomUUID()},{root:f.root}));
  const lease=path.join(f.root,'run-'+odsAdmissionSha(runId)+'.json');const value=JSON.parse(fs.readFileSync(lease));assert.equal(value.runId,runId);assert.equal(value.sessionId,'owned-session');
  let seen;await createOwnedProbeFetch({...f.a,runId},async(_,init)=>{seen=init;return new Response('ok');},{leasePath:lease})(f.a.baseUrl+'/chat/completions',{method:'POST',body:'{}'});assert(seen.headers.has('x-ods-probe'));
  await createOwnedProbeFetch({...f.a,runId,sessionId:'other'},async(_,init)=>{seen=init;return new Response('ok');},{leasePath:lease})(f.a.baseUrl+'/chat/completions',{method:'POST',body:'{}'});assert(!seen.headers.has('x-ods-probe'));
 }finally{f.close();}
});
test('missing or mismatched request/body/owner rejects capture without consuming authorization',{skip:!supported},()=>{
 const f=fixture();try{
  for(const change of [{requestId:'other'},{user:'other'},{incoming:{...f.incoming,request_id:'other'}},{incoming:{...f.incoming,messages:[{role:'user',content:'other'}]}},{incoming:{...f.incoming,history_snapshot:null}}])assert.deepEqual(createProbeIngressHeader({...f.request,...change},{root:f.root}),{});
  assert(fs.existsSync(f.file));f.a.ownerUid++;fs.writeFileSync(f.file,JSON.stringify(f.a));assert.deepEqual(createProbeIngressHeader(f.request,{root:f.root}),{});assert(fs.existsSync(f.file));
 }finally{f.close();}
});
test('gateway duplicates, wrong body/session/agent and expired authorization are fail closed',{skip:!supported},()=>{
 const f=fixture();try{
  const header=createProbeIngressHeader(f.request,{root:f.root})['X-ODS-Probe-Admission'];
  const a={header,payload:JSON.parse(f.gatewayBody),sessionKey:f.sessionKey,runId:'chatcmpl_'+crypto.randomUUID(),agentId:'pixel'};
  for(const change of [{header:header+', '+header},{payload:{...a.payload,messages:[]}},{sessionKey:'other'},{agentId:'other'}])assert(!consumeProbeGatewayAdmission({...a,...change},{root:f.root}));
  assert(!consumeProbeGatewayAdmission(a,{root:f.root,clock:()=>Date.now()+20000}));assert(consumeProbeGatewayAdmission(a,{root:f.root}));
 }finally{f.close();}
});
test('crash after election and before unlink leaves an unreplayable diagnostic only',{skip:!supported},()=>{
 const f=fixture();try{
  fs.linkSync(f.file,f.file+'.claimed');assert.equal(fs.statSync(f.file).nlink,2);
  assert.deepEqual(createProbeIngressHeader(f.request,{root:f.root}),{});assert(fs.existsSync(f.file));
  fs.unlinkSync(f.file);assert.deepEqual(createProbeIngressHeader(f.request,{root:f.root}),{});
 }finally{f.close();}
});
test('a sanitized-byte versus parsed serialization disagreement never admits a lease',{skip:!supported},()=>{
 const f=fixture();try{
  const header=createProbeIngressHeader({...f.request,gatewayBody:' '+f.gatewayBody},{root:f.root})['X-ODS-Probe-Admission'];assert(header);
  assert(!consumeProbeGatewayAdmission({header,payload:JSON.parse(f.gatewayBody),sessionKey:f.sessionKey,runId:'chatcmpl_'+crypto.randomUUID(),agentId:'pixel'},{root:f.root}));
 }finally{f.close();}
});
