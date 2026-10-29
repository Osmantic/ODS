import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createConnectionsStatusTool,readConnectionStatus,validateConnectionStatus} from '../plugin/connections-status.mjs';

const fixture = () => ({schemaVersion:1,kind:'ods-connection-agent-status',scope:'reviewed-google',
  observedAt:'2026-10-29T20:00:00Z',available:true,reason:null,recordConfigured:true,localSavedSignIn:true,
  connectionState:'connected',ownerEnabled:{gmailRead:true,calendarRead:false},
  providerPermissionObserved:{gmailRead:true,calendarRead:true},sourceCheckedAt:'2026-10-29T19:59:00Z',
  reads:{email:{lastObservedState:'ok',recent:true,lastSuccessAt:'2026-10-29T19:59:00Z',complete:false,error:null},
    calendar:{lastObservedState:'disabled',recent:null,lastSuccessAt:null,complete:null,error:null}},
  providerCheckedNow:false});

test('status uses the fixed local helper without shell, credential environment or model arguments',async()=>{
  const calls=[];
  const value=await readConnectionStatus({platform:'linux',execute:(file,args,options,callback)=>{
    calls.push({file,args,options});callback(null,JSON.stringify(fixture())+'\n');
  }});
  assert.deepEqual(value,fixture());
  assert.equal(calls.length,1);
  assert.equal(calls[0].file,'/usr/bin/python3');
  assert.deepEqual(calls[0].args,['-I','/usr/local/libexec/ods-pixel-access/pixel_connection_agent_status.py']);
  assert.deepEqual(calls[0].options.env,{PATH:'/usr/bin:/bin'});
  assert.equal(calls[0].options.cwd,'/');
  assert.equal(calls[0].options.maxBuffer,8192);
  assert.equal(calls[0].options.timeout,22000);
  assert.equal(calls[0].options.shell,undefined);
});

test('connection, owner permission, provider observation and sync coverage stay separate',async()=>{
  const original=fixture();
  const result=await createConnectionsStatusTool({read:async()=>original}).execute('status',{});
  assert.equal(result.isError,undefined);
  const value=JSON.parse(result.content[0].text);
  assert.deepEqual(value,original);
  assert.equal(value.connectionState,'connected');
  assert.equal(value.ownerEnabled.calendarRead,false);
  assert.equal(value.providerPermissionObserved.calendarRead,true);
  assert.equal(value.reads.email.complete,false);
  assert.equal(value.providerCheckedNow,false);
  const unknown=fixture();
  Object.assign(unknown,{available:true,reason:'record-unusable',recordConfigured:null,
    localSavedSignIn:null,connectionState:'unknown'});
  assert.equal(validateConnectionStatus(unknown).recordConfigured,null);
});

test('reject owner payloads, extra content and malformed status without reflecting them',async()=>{
  for(const mutate of [
    value=>{value.account='private-account@example.invalid';},
    value=>{value.reads.email.message='private-source-record';},
    value=>{value.googleAuth={token:'private-token'};},
    value=>{value.providerCheckedNow=true;},
    value=>{value.ownerEnabled.gmailRead=1;},
    value=>{value.providerPermissionObserved.gmailRead='yes';},
    value=>{value.reason='private-source-record';},
    value=>{value.reads.email.error='private-source-record';},
    value=>{value.reads.email.recent='true';},
    value=>{value.observedAt='not-a-time';},
    value=>{value.observedAt='2026-02-30T00:00:00Z';},
    value=>{value.observedAt='0000-01-01T00:00:00Z';},
    value=>{value.recordConfigured=false;},
    value=>{value.connectionState='not-connected';},
    value=>{value.available=false;},
    value=>{delete value.localSavedSignIn;},
  ]) {
    const value=fixture();mutate(value);
    assert.throws(()=>validateConnectionStatus(value));
    const result=await createConnectionsStatusTool({read:async()=>value}).execute('status',{});
    assert.equal(result.isError,true);
    assert.match(result.content[0].text,/unknown.*does not prove.*disconnected/);
    assert.doesNotMatch(result.content[0].text,/private-/);
  }
});

test('unsupported platform, helper failure and malformed framing yield unknown without retry',async()=>{
  let calls=0;
  await assert.rejects(readConnectionStatus({platform:'darwin',execute:()=>{calls++;}}));
  assert.equal(calls,0);
  for(const [error,stdout] of [[Error('private-error'),''],[null,'{}\n'],
    [null,JSON.stringify(fixture())],[null,JSON.stringify(fixture())+'\n{}\n']]) {
    await assert.rejects(readConnectionStatus({platform:'linux',execute:(_f,_a,_o,callback)=>{
      calls++;callback(error,stdout);
    }}),/Connection status unavailable/);
  }
  assert.equal(calls,4);
});

test('invalid arguments and cancellation never invoke a connection read',async()=>{
  let reads=0;
  const tool=createConnectionsStatusTool({read:async()=>{reads++;return fixture();}});
  for(const args of [{account:'other'},null,[],{operation:'connect'}]) {
    assert.equal((await tool.execute('status',args)).isError,true);
  }
  const controller=new AbortController();controller.abort();
  assert.equal((await tool.execute('status',{},controller.signal)).isError,true);
  assert.equal(reads,0);
});

test('native discovery leads with the capability distinction in its compact directory',()=>{
  const description=createConnectionsStatusTool().description.slice(0,180);
  assert.match(description,/Google\/Gmail and Calendar connection, read permissions and recent sync health/);
  assert.match(description,/No emails or events/);
  const manifest=JSON.parse(readFileSync(new URL('../plugin/openclaw.plugin.json',import.meta.url)));
  assert.ok(manifest.contracts.tools.includes('pixel_ods_connections_status'));
  assert.equal(manifest.toolMetadata.pixel_ods_connections_status.replaySafe,true);
});
