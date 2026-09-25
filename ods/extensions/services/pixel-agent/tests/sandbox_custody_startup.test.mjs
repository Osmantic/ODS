import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const manifest=JSON.parse(fs.readFileSync(new URL('../host/openclaw-sandbox-custody-backend.json',import.meta.url)));
const source=manifest.replacements.find(([,after])=>after.includes('function createDockerSandboxBackendHandle'))[1];
const nonce='ab'.repeat(24);
const frame=`ODS_EXEC_CUSTODY_V1 ${nonce} 42 123456\n`;
async function fixture(){
 const calls=[];
 const context={Buffer,AbortSignal,process,odsExecRandomBytes:()=>Buffer.from(nonce,'hex'),
  buildDockerExecArgs:({command})=>['exec','owned-container','/bin/sh','-lc',command],
  runDockerSandboxShellCommand:async input=>{
   calls.push(input);
   return {stdout:Buffer.from(calls.length===1?JSON.stringify({directory:[1,2],interpreter:'/usr/local/bin/python3.12'}):JSON.stringify({admitted:true,nonce,pid:42,startTicks:'123456'}))};
  }};
 vm.runInNewContext(source+'\nthis.backend=createDockerSandboxBackendHandle({containerName:"owned-container",env:{ODS_EXEC_CUSTODY:"1"}});',context);
 const spec=await context.backend.buildExecSpec({command:'printf hello',workdir:'/workspace',env:{},usePty:false});
 return {spec,calls};
}

test('fragmented startup consumes only header and preserves all following bytes',async()=>{
 const {spec,calls}=await fixture();
 const bytes=Buffer.from([0,255,13,10,128]);
 assert.equal(spec.transformStdout(Buffer.from(frame.slice(0,10))).length,0);
 assert.equal(spec.transformStdout(Buffer.from(frame.slice(10,30))).length,0);
 assert.deepEqual(spec.transformStdout(Buffer.concat([Buffer.from(frame.slice(30)),bytes])),bytes);
 await spec.custodyAdmission;
 assert.equal(calls.length,2);
 assert.ok(calls.every(call=>call.shellPath==="/bin/sh"));
 assert.match(calls[1].script,/^'\/usr\/local\/bin\/python3\.12'/);
 const later=Buffer.from(`ODS_EXEC_CUSTODY_V1 ${nonce} 1 0\n`);
 assert.deepEqual(spec.transformStdout(later),later);
 assert.equal(calls.length,2,'later frames must not trigger another release');
});

test('PTY CRLF startup is accepted without modifying subsequent CRLF',async()=>{
 const {spec}=await fixture();
 const result=spec.transformStdout(Buffer.from(frame.replace('\n','\r\n')+'body\r\n'));
 await spec.custodyAdmission;
 assert.equal(result.toString(),'body\r\n');
});

for(const [label,header] of [['wrong nonce',frame.replace(nonce,'cd'.repeat(24))],['malformed','not a frame\n'],['oversized','x'.repeat(257)],['PID zero',frame.replace(' 42 ',' 0 ')]]){
 test(`${label} cannot release original command`,async()=>{
  const {spec,calls}=await fixture();
  assert.equal(spec.transformStdout(Buffer.from(header)).length,0);
  await assert.rejects(spec.custodyAdmission,/ownership frame invalid/);
  assert.equal(calls.length,1);
  assert.equal(spec.transformStdout(Buffer.from(frame)).length,0);
  assert.equal(calls.length,1,'a later valid frame must not repair invalid admission');
 });
}

test('cancellation before startup identity prevents delayed release',async()=>{
 const {spec,calls}=await fixture();
 spec.custodyCancel();
 spec.transformStdout(Buffer.from(frame));
 await assert.rejects(spec.custodyAdmission,/cancelled before startup/);
 assert.equal(calls.length,1);
});
