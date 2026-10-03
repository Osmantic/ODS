import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {randomUUID} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {readArchivedHistory} from '../plugin/history-context.mjs';

const run=promisify(execFile);
test('native history reads the actual Unix-only ingress inside an isolated container',{
  skip:process.env.ODS_TEST_HISTORY_DOCKER_LIVE!=='1',timeout:60000},async t=>{
  const docker=fs.realpathSync(process.env.ODS_TEST_PIXEL_DOCKER);
  const image=process.env.ODS_TEST_HISTORY_IMAGE;
  assert.ok(docker?.startsWith('/'));assert.match(image || '',/^sha256:[a-f0-9]{64}$/);
  const project='ods-history-test-'+randomUUID().replaceAll('-','');
  const name=project+'-ingress',user='ods-'+'a'.repeat(64);
  const source=fileURLToPath(new URL('../host',import.meta.url));
  const script=`
    import {createIngressServer} from '/source/pixel_ingress.mjs';
    import {createChatHistoryLedger} from '/source/chat_history_ledger.mjs';
    const ledger=createChatHistoryLedger('/runtime/history');
    const state={status:'ready',sessionRevision:'fixture',compaction:{count:0}};
    for(const [user,text] of [['${user}','alpha archived fixture'],['ods-'+ 'b'.repeat(64),'beta separate conversation']]) {
      const prepared=ledger.prepare(user,'fixture',{schemaVersion:1,messages:[{role:'user',content:text}]},state);
      ledger.complete(user,prepared,{},null,state);
    }
    createIngressServer({token:'fixture-not-live',gatewayPort:1,historyLedger:ledger}).listen('/runtime/pixel-ingress.sock');
  `;
  t.after(async()=>{await run(docker,['rm','-f',name],{timeout:10000}).catch(()=>{});});
  await run(docker,['run','-d','--pull','never','--name',name,'--network','none','--read-only',
    '--cap-drop','ALL','--security-opt','no-new-privileges:true','--user','501:20','--pids-limit','32',
    '--memory','256m','--cpus','0.5','--label','com.docker.compose.project='+project,
    '--label','com.docker.compose.service=pixel-native-ingress',
    '--tmpfs','/runtime:rw,noexec,nosuid,size=16m,uid=501,gid=20,mode=0700',
    '--mount','type=bind,src='+source+',dst=/source,readonly','--entrypoint','node',image,'--input-type=module','-e',script],{timeout:20000});
  const options={transport:'docker-exec',dockerPath:docker,project,image,containerUser:'501:20'};
  let response;
  const deadline=Date.now()+15000;
  while(Date.now()<deadline) {
    try {response=await readArchivedHistory(user,{query:'alpha',limit:5},options);break;}
    catch {await new Promise(resolve=>setTimeout(resolve,250));}
  }
  assert.ok(response,'fixture ingress did not become ready');
  assert.equal(response.untrusted,true);
  assert.equal(response.messages.length,1);
  assert.ok(JSON.stringify(response).includes('alpha archived fixture'));
  assert.ok(!JSON.stringify(response).includes('beta separate conversation'));
  await assert.rejects(readArchivedHistory(user,{}, {...options,image:'sha256:'+'0'.repeat(64)}));
  if(process.platform==='darwin') {
    const socket=fs.realpathSync(process.env.ODS_TEST_PIXEL_DOCKER_SOCKET);
    const home=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'ods-history-policy-')));
    t.after(()=>fs.rmSync(home,{recursive:true,force:true}));
    const plugin=fileURLToPath(new URL('../plugin',import.meta.url));
    const bin=fileURLToPath(new URL('../../../../bin',import.meta.url));
    const policy=await run('/usr/bin/python3',['-I','-c',
      'import sys,json; sys.path.insert(0,sys.argv[1]); from pixel_macos_policy import render_policy; sys.stdout.buffer.write(render_policy(**json.loads(sys.argv[2])))',bin,
      JSON.stringify({mode:'sandboxed',writable:[home],protected:[plugin,process.execPath],
        readable:[home],sockets:[socket],probe:path.join(home,'probe')})],{timeout:10000});
    const profile=path.join(home,'gateway.sb');fs.writeFileSync(profile,policy.stdout,{mode:0o600});
    fs.mkdirSync(path.join(home,'docker-config'),{mode:0o700});
    const probe=`import {readArchivedHistory} from ${JSON.stringify(new URL('../plugin/history-context.mjs',import.meta.url).href)};
      const value=await readArchivedHistory(${JSON.stringify(user)},{query:'alpha'},${JSON.stringify(options)});
      process.stdout.write(JSON.stringify(value));`;
    const result=await run('/usr/bin/sandbox-exec',['-f',profile,process.execPath,'--input-type=module','-e',probe],
      {timeout:15000,cwd:home,env:{HOME:home,TMPDIR:home,DOCKER_CONFIG:path.join(home,'docker-config'),
        DOCKER_HOST:'unix://'+socket,PATH:'/usr/bin:/bin:/usr/sbin:/sbin'}});
    assert.ok(JSON.parse(result.stdout).messages.length===1);
  }
});
