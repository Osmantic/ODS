import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {PassThrough} from 'node:stream';
import {readConversationImage,readConversationImagePolicy} from '../plugin/chat-image-transport.mjs';

const user='ods-'+'c'.repeat(64),id='a'.repeat(64),image='sha256:'+'b'.repeat(64);
const reference={id:'img-'+'d'.repeat(32),sha256:'e'.repeat(64)};
const envelope={schemaVersion:1,image:{...reference,mimeType:'image/png',bytes:3,data:'YWJj'}};
const policy={schemaVersion:1,policy:{imageInput:'supported',routeFingerprint:'f'.repeat(64),unknownConsent:false}};
function docker(fault) {
  const calls=[],inputs=[],kills=[];
  const execute=(binary,args,options,callback)=>{
    calls.push({binary,args,options});const stdin=new PassThrough();let input='';
    stdin.on('data',part=>{input+=part;});
    stdin.on('finish',()=>{
      inputs.push(input);
      if(fault==='hang' && args[0]==='exec')return;
      const identity=[id,image,true,'ods-test','pixel-native-ingress','501:20'];
      if(fault==='id')identity[0]='f'.repeat(64);
      if(fault==='image')identity[1]='sha256:'+'f'.repeat(64);
      if(fault==='project')identity[3]='foreign';
      if(fault==='service')identity[4]='foreign';
      if(fault==='user')identity[5]='0:0';
      if(fault==='stopped')identity[2]=false;
      callback(null,args[0]==='ps'?(fault==='duplicate'?id+'\n'+id:id):args[0]==='inspect'?identity.map(JSON.stringify).join('\n'):
        fault==='oversize'?'x'.repeat(12*1024*1024+1):JSON.stringify(JSON.parse(input).path.endsWith('image-policy')?policy:envelope));
    });
    return {stdin,kill:signal=>kills.push(signal)};
  };
  return {calls,inputs,kills,options:{transport:'docker-exec',dockerPath:'/qualified/docker',project:'ods-test',image,containerUser:'501:20',execute}};
}

test('image and policy Docker reads use fixed paths and inspected immutable identity',async()=>{
  const f=docker();
  assert.deepEqual(await readConversationImage(user,reference,f.options),envelope);
  assert.deepEqual(f.calls.map(c=>c.args[0]),['ps','inspect','exec']);
  assert.deepEqual(f.calls[2].args.slice(0,8),['exec','-i','--env','NODE_OPTIONS=',id,'node','--input-type=module','-e']);
  assert.deepEqual(JSON.parse(f.inputs[2]),{path:'/v1/chat/image',body:{user,...reference}});
  assert.ok(Buffer.byteLength(f.inputs[2])<=1024);
  assert.ok(f.calls.every(c=>!c.args.includes(reference.id) && c.options.timeout<=5000));
  assert.equal(f.calls[2].options.maxBuffer,12*1024*1024);
  assert.deepEqual(await readConversationImagePolicy(user,f.options),policy);
  assert.equal(f.calls.at(-1).options.maxBuffer,2048);
  assert.equal(f.kills.length,0);
});
for(const fault of ['id','image','project','service','user','stopped','duplicate','oversize'])test(`Docker refuses ${fault}`,async()=>{
  const f=docker(fault);
  await assert.rejects(readConversationImage(user,reference,f.options),{code:'image-read-unavailable'});
  if(fault!=='oversize')assert.ok(!f.calls.some(c=>c.args[0]==='exec'));
});
test('Docker cancellation kills current child and prevents late success',async()=>{
  const f=docker('hang'),control=new AbortController();
  const pending=readConversationImage(user,reference,{...f.options,signal:control.signal});
  await new Promise(resolve=>setImmediate(resolve));control.abort();
  await assert.rejects(pending,{code:'image-read-unavailable'});
  assert.deepEqual(f.kills,['SIGKILL']);
  const early=docker();await assert.rejects(readConversationImage(user,reference,{...early.options,signal:control.signal}));
  assert.equal(early.calls.length,0);
});
test('invalid references and shared deadline stop before exec',async()=>{
  for(const value of [{...reference,url:'http://foreign'},{...reference,id:reference.id+'\n'},{...reference,sha256:reference.sha256+'\n'}]){
    const f=docker();await assert.rejects(readConversationImage(user,value,f.options));assert.equal(f.calls.length,0);
  }
  const f=docker();let tick=0;
  await assert.rejects(readConversationImage(user,reference,{...f.options,now:()=>tick++*3000}));
  assert.deepEqual(f.calls.map(c=>c.args[0]),['ps']);
});

test('concurrent image reads are bounded and cancellation releases their slots',async()=>{
  const first=docker('hang'),second=docker('hang'),a=new AbortController(),b=new AbortController();
  const pending=[readConversationImage(user,reference,{...first.options,signal:a.signal}),
    readConversationImage(user,reference,{...second.options,signal:b.signal})];
  await assert.rejects(readConversationImage(user,reference,docker().options));
  a.abort();b.abort();await Promise.all(pending.map(value=>assert.rejects(value)));
  assert.deepEqual(await readConversationImage(user,reference,docker().options),envelope);
});

test('real Unix transport preserves envelope, rejects excess bytes, and aborts a stalled response',
  {skip:process.platform==='win32'},async()=>{
    const root=await mkdtemp(join(tmpdir(),'ods-image-read-')),socketPath=join(root,'socket');
    let mode='normal';const seen=[];
    const server=http.createServer((req,res)=>{
      const chunks=[];req.on('data',chunk=>chunks.push(chunk));req.on('end',()=>{
        seen.push({path:req.url,headers:req.headers,body:JSON.parse(Buffer.concat(chunks))});
        if(mode==='hang')return;
        res.writeHead(200,{'content-type':'application/json'});
        const large={schemaVersion:1,image:{...envelope.image,bytes:8*1024*1024,data:Buffer.alloc(8*1024*1024).toString('base64')}};
        res.end(mode==='oversize'?'x'.repeat(2049):mode==='image-oversize'?'x'.repeat(12*1024*1024+1):
          JSON.stringify(mode==='large'?large:req.url.endsWith('image-policy')?policy:envelope));
      });
    });
    await new Promise(resolve=>server.listen(socketPath,resolve));
    try {
      assert.deepEqual(await readConversationImage(user,reference,{socketPath}),envelope);
      assert.deepEqual(await readConversationImagePolicy(user,{socketPath}),policy);
      assert.deepEqual(seen[0].body,{user,...reference});
      assert.equal(seen[0].headers.authorization,undefined);
      assert.equal(seen[0].headers.cookie,undefined);
      mode='large';assert.equal((await readConversationImage(user,reference,{socketPath})).image.bytes,8*1024*1024);
      mode='image-oversize';await assert.rejects(readConversationImage(user,reference,{socketPath}));
      mode='oversize';await assert.rejects(readConversationImagePolicy(user,{socketPath}));
      mode='hang';const control=new AbortController();
      const pending=readConversationImage(user,reference,{socketPath,signal:control.signal});
      setTimeout(()=>control.abort(),20);
      await assert.rejects(pending,{code:'image-read-unavailable'});
      let ticks=0;
      await assert.rejects(readConversationImagePolicy(user,{socketPath,now:()=>ticks++===0?0:4950}),{code:'image-read-unavailable'});
    }finally{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));await rm(root,{recursive:true,force:true});}
  });
