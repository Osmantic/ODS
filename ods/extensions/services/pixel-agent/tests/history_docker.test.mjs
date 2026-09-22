import test from 'node:test';
import assert from 'node:assert/strict';
import {PassThrough} from 'node:stream';
import {readHistoryViaDocker} from '../plugin/history-docker.mjs';
import {readArchivedHistory} from '../plugin/history-context.mjs';

const id='a'.repeat(64),image='sha256:'+'b'.repeat(64),user='ods-'+'c'.repeat(64);
const archived={schemaVersion:1,source:'archived-conversation',untrusted:true,messages:[]};
function fixture(fault) {
  const calls=[],inputs=[];
  const execute=(binary,args,options,callback)=>{
    calls.push({binary,args,options});
    const stdin=new PassThrough();let body='';
    stdin.on('data',part=>{body+=part});
    stdin.on('finish',()=>{
      inputs.push(body);
      if(fault==='command') return callback(new Error('private-docker-detail'));
      const identity=[id,image,true,'ods-test','pixel-native-ingress','501:20'];
      if(fault==='image') identity[1]='sha256:'+'d'.repeat(64);
      if(fault==='project') identity[3]='other';
      if(fault==='service') identity[4]='other';
      if(fault==='stopped') identity[2]=false;
      if(fault==='root') identity[5]='0:0';
      if(fault==='id') identity[0]='e'.repeat(64);
      let result=args[0]==='ps' ? (fault==='duplicate'?id+'\n'+id:id) :
        args[0]==='inspect' ? identity.map(value=>JSON.stringify(value)).join('\n') :
        fault==='oversize' ? 'x'.repeat(16385) : JSON.stringify(archived);
      callback(null,result);
    });
    return {stdin};
  };
  return {calls,inputs,options:{dockerPath:'/qualified/docker',project:'ods-test',image,containerUser:'501:20',execute}};
}
test('history uses inspected immutable container and passes query only on stdin',async()=>{
  const f=fixture();
  assert.deepEqual(JSON.parse(await readHistoryViaDocker(user,{query:'$(do not execute)'},f.options)),archived);
  assert.deepEqual(f.calls.map(call=>call.args[0]),['ps','inspect','exec']);
  assert.deepEqual(f.calls[2].args.slice(0,8),['exec','-i','--env','NODE_OPTIONS=',id,'node','--input-type=module','-e']);
  assert.ok(!f.calls.some(call=>call.args.join(' ').includes('$(do not execute)')));
  assert.deepEqual(JSON.parse(f.inputs[2]),{user,query:'$(do not execute)'});
  assert.ok(f.calls.every(call=>call.options.timeout<=5000 && call.options.maxBuffer===65536));
});
for(const fault of ['duplicate','image','project','service','stopped','root','id','command','oversize']) {
  test('history refuses '+fault,async()=>{
    const f=fixture(fault);
    await assert.rejects(readHistoryViaDocker(user,{},f.options),{message:'history-unavailable'});
    if(fault!=='oversize') assert.ok(!f.calls.some(call=>call.args[0]==='exec'));
  });
}
test('history refuses arbitrary keys, user override and invalid identity before Docker',async()=>{
  for(const args of [{user:'ods-'+'f'.repeat(64)},{url:'http://other'},{query:'x'.repeat(201)},{offset:-1},{limit:21}]) {
    const f=fixture();await assert.rejects(readHistoryViaDocker(user,args,f.options));assert.equal(f.calls.length,0);
  }
  const f=fixture();await assert.rejects(readHistoryViaDocker(user,{}, {...f.options,image:'mutable:latest'}));
  assert.equal(f.calls.length,0);
});
test('one deadline spans discovery, inspection and execution',async()=>{
  const f=fixture();let tick=0;
  await assert.rejects(readHistoryViaDocker(user,{}, {...f.options,now:()=>tick++*3000}));
  assert.deepEqual(f.calls.map(call=>call.args[0]),['ps']);
});
test('native transport still requires archived untrusted response schema',async()=>{
  const options={transport:'docker-exec',dockerRead:async()=>JSON.stringify(archived)};
  assert.deepEqual(await readArchivedHistory(user,{},options),archived);
  await assert.rejects(readArchivedHistory(user,{}, {...options,dockerRead:async()=>'{}'}),{message:'history-unavailable'});
  await assert.rejects(readArchivedHistory(user,{}, {transport:'unknown'}),{message:'history-unavailable'});
});
test('concurrent history execs are bounded and slots release after completion',async()=>{
  const f=fixture(),pending=[];
  const execute=(binary,args,options,callback)=>f.options.execute(binary,args,options,(...result)=>{
    if(args[0]==='ps') pending.push(()=>callback(...result));else callback(...result);
  });
  const first=readHistoryViaDocker(user,{}, {...f.options,execute});
  const second=readHistoryViaDocker(user,{}, {...f.options,execute});
  await assert.rejects(readHistoryViaDocker(user,{},f.options),{message:'history-unavailable'});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(pending.length,2);
  for(const release of pending) release();
  await Promise.all([first,second]);
  assert.deepEqual(JSON.parse(await readHistoryViaDocker(user,{},f.options)),archived);
});
