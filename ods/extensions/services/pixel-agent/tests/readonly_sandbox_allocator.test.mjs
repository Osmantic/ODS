import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {createRequire} from 'node:module';
import {createReadonlySandboxAllocator} from '../plugin/readonly-sandbox-allocator.mjs';

let DatabaseSync;
try {({DatabaseSync}=createRequire(import.meta.url)('node:sqlite'));} catch {}
const skip=process.platform==='win32'||!DatabaseSync;
const imageId='sha256:'+'a'.repeat(64);
const failure=code=>Object.assign(new Error(code),{code});

function fixture(t){
  const root=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'ods-allocator-test-')));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const stateRoot=path.join(root,'state'),workspace=path.join(root,'workspace');
  for(const dir of [stateRoot,workspace,path.join(stateRoot,'state')])fs.mkdirSync(dir,{mode:0o700});
  const databasePath=path.join(stateRoot,'state','openclaw.sqlite');
  const db=()=>new DatabaseSync(databasePath);
  const connection=db();connection.exec('CREATE TABLE sandbox_registry_entries (registry_kind TEXT,container_name TEXT,session_key TEXT,backend_id TEXT,image TEXT,created_at_ms INTEGER)');connection.close();
  const containers=new Map(),removed=[],cliCalls=[],leaseState={owner:null,sequence:0};
  let sequence=0;
  const fx={root,stateRoot,workspace,databasePath,containers,removed,cliCalls,leaseState,
    imageMissing:false,cliFails:false,createdAt:Date.now(),
    config:{agents:{list:[{id:'pixel',workspace,sandbox:{docker:{image:'sandbox:fixture'}}}]}}};
  fx.rows=key=>{const c=db();try{return c.prepare('SELECT * FROM sandbox_registry_entries WHERE session_key=?').all(key);}finally{c.close();}};
  fx.insert=(allocation,name)=>{const c=db();try{c.prepare('INSERT INTO sandbox_registry_entries VALUES (?,?,?,?,?,?)').run('container',name,allocation.sandboxSessionKey,'docker',imageId,allocation.createdAt);}finally{c.close();}};
  fx.docker=async args=>{
    if(args[0]==='image'){assert.deepEqual(args,['image','inspect','sandbox:fixture']);if(fx.imageMissing)throw failure('IMAGE_UNAVAILABLE');return JSON.stringify([{Id:imageId}]);}
    if(args[0]==='inspect'){
      assert.deepEqual(args.slice(0,3),['inspect','--type','container']);
      const row=[...containers.values()].find(c=>c.Id===args[3]||c.Name==='/'+args[3]);
      if(!row)throw failure('DOCKER_NOT_FOUND');return JSON.stringify([row]);
    }
    if(args[0]==='ps'){
      assert.deepEqual(args.slice(0,4),['ps','-aq','--no-trunc','--filter']);
      const filter=args[4];
      assert(filter.startsWith('name=^/')||filter.startsWith('id='));
      return [...containers.values()].filter(c=>filter.startsWith('id=')?c.Id===filter.slice(3):c.Name.startsWith(filter.slice(6))).map(c=>c.Id).join('\n');
    }
    if(args[0]==='rm'){assert.equal(args[1],'-f');assert.match(args[2],/^[a-f0-9]{64}$/);assert(containers.delete(args[2]));removed.push(args[2]);return args[2];}
    throw new Error('unexpected Docker argv '+JSON.stringify(args));
  };
  fx.cli=async args=>{
    assert.deepEqual(args.slice(0,3),['sandbox','recreate','--session']);assert.equal(args[4],'--force');
    cliCalls.push(args);if(fx.cliFails)throw failure('CLI_UNAVAILABLE');
    const rows=fx.rows(args[3]);assert.equal(rows.length,1);
    const c=db();try{c.prepare('DELETE FROM sandbox_registry_entries WHERE session_key=?').run(args[3]);}finally{c.close();}
    return 'Removed '+rows[0].container_name+'\nDone: 1 removed, 0 failed\n';
  };
  fx.acquireLease=async()=>{
    if(leaseState.owner!==null)throw failure('LEASE_BUSY');
    const owner=++leaseState.sequence;leaseState.owner=owner;
    return {release:async()=>{assert.equal(leaseState.owner,owner);leaseState.owner=null;}};
  };
  fx.make=()=>createReadonlySandboxAllocator({stateRoot,runDocker:fx.docker,runCli:fx.cli,
    acquireLease:fx.acquireLease,clock:()=>fx.createdAt,randomNonce:()=> (++sequence).toString(16).padStart(32,'0')});
  fx.allocator=fx.make();
  fx.allocate=()=>fx.allocator.allocate({workspaceRoot:workspace},fx.config);
  fx.job=allocation=>path.join(stateRoot,'ods-readonly',allocation.sandboxSessionKey.split(':').at(-1));
  fx.simulate=allocation=>{
    const sandbox=allocation.config.agents.list[0].sandbox;
    const bootstrap=path.join(sandbox.workspaceRoot,'sdk-bootstrap');fs.mkdirSync(bootstrap,{recursive:true,mode:0o700});
    const name=sandbox.docker.containerPrefix+'fixture';
    const id=(++sequence).toString(16).padStart(64,'0');
    const container={Id:id,Name:'/'+name,Image:imageId,Created:new Date(allocation.createdAt).toISOString(),
      Config:{Labels:{'openclaw.sandbox':'1'},User:'sandbox',WorkingDir:'/workspace'},State:{Running:true},
      HostConfig:{ReadonlyRootfs:true,NetworkMode:'none',CapDrop:['ALL'],CapAdd:[],Privileged:false,
        Devices:[],DeviceRequests:[],SecurityOpt:['no-new-privileges'],Memory:536870912,MemorySwap:536870912,NanoCpus:1e9,PidsLimit:128},
      Mounts:[{Type:'bind',Source:workspace,Destination:'/agent',RW:false},{Type:'bind',Source:bootstrap,Destination:'/workspace',RW:false}]};
    containers.set(id,container);fx.insert(allocation,name);
    return {container,sandbox:{enabled:true,workspaceAccess:'ro',agentWorkspaceDir:workspace,workspaceDir:bootstrap,runtimeId:name,containerName:name}};
  };
  return fx;
}

test('real registry, scoped config and owned cleanup preserve unrelated state',{skip},async t=>{
  const f=fixture(t),a=await f.allocate(),s=f.simulate(a);
  const unrelated={sandboxSessionKey:'agent:pixel:ods-readonly:'+'f'.repeat(32),createdAt:f.createdAt};
  f.insert(unrelated,'unrelated-container');await a.attach(s.sandbox);
  assert.equal(a.ownerWorkspace,f.workspace);assert.notEqual(a.config,f.config);
  await a.cleanup();await a.cleanup();
  assert.deepEqual(f.removed,[s.container.Id]);assert.deepEqual(f.rows(a.sandboxSessionKey),[]);
  assert.equal(f.rows(unrelated.sandboxSessionKey).length,1);assert(!fs.existsSync(f.job(a)));
  assert.equal(f.leaseState.owner,null);assert.equal(f.cliCalls.length,1);
});

test('partial SDK creation is cleaned before attach',{skip},async t=>{
  const f=fixture(t),a=await f.allocate(),s=f.simulate(a);await a.cleanup();
  assert.deepEqual(f.removed,[s.container.Id]);assert.deepEqual(f.rows(a.sandboxSessionKey),[]);
});

test('restriction failure still permits cleanup of verified owned runtime',{skip},async t=>{
  const f=fixture(t),a=await f.allocate(),s=f.simulate(a);s.container.HostConfig.NetworkMode='bridge';
  await assert.rejects(a.attach(s.sandbox),/restriction: network/);await a.cleanup();assert.deepEqual(f.removed,[s.container.Id]);
});

test('registry-only crash cleanup uses exact session',{skip},async t=>{
  const f=fixture(t),a=await f.allocate(),s=f.simulate(a);f.containers.delete(s.container.Id);
  await a.cleanup();assert.deepEqual(f.removed,[]);assert.equal(f.cliCalls[0][3],a.sandboxSessionKey);assert(!fs.existsSync(f.job(a)));
});

test('overlapping instance cannot recover live job; restart recovers after owner death',{skip},async t=>{
  const f=fixture(t),a=await f.allocate(),s=f.simulate(a);await a.attach(s.sandbox);
  const second=f.make();await assert.rejects(second.recover(),{code:'LEASE_BUSY'});
  assert(f.containers.has(s.container.Id));assert(fs.existsSync(f.job(a)));
  f.leaseState.owner=null; // The test models process death, not an allocator unlock API.
  await second.recover();assert.deepEqual(f.removed,[s.container.Id]);assert(!fs.existsSync(f.job(a)));
});

test('replacement container is never removed',{skip},async t=>{
  const f=fixture(t),a=await f.allocate(),s=f.simulate(a);await a.attach(s.sandbox);
  f.containers.delete(s.container.Id);s.container.Id='d'.repeat(64);f.containers.set(s.container.Id,s.container);
  await assert.rejects(a.cleanup(),{code:'readonly-runtime-ownership'});
  assert(f.containers.has(s.container.Id));assert.deepEqual(f.removed,[]);assert(fs.existsSync(f.job(a)));
});

test('failed CLI cleanup retains journal and can retry',{skip},async t=>{
  const f=fixture(t),a=await f.allocate(),s=f.simulate(a);await a.attach(s.sandbox);f.cliFails=true;
  await assert.rejects(a.cleanup(),{code:'CLI_UNAVAILABLE'});assert(fs.existsSync(f.job(a)));assert(f.leaseState.owner!==null);
  f.cliFails=false;await a.cleanup();assert.deepEqual(f.removed,[s.container.Id]);assert.equal(f.leaseState.owner,null);
});

test('missing registry is not absence; retained cleanup recovers after database restoration',{skip},async t=>{
  const f=fixture(t),a=await f.allocate(),s=f.simulate(a);await a.attach(s.sandbox);
  const bytes=fs.readFileSync(f.databasePath);fs.unlinkSync(f.databasePath);
  await assert.rejects(a.cleanup(),{code:'readonly-registry-unavailable'});assert(fs.existsSync(f.job(a)));
  fs.writeFileSync(f.databasePath,bytes,{mode:0o600});await a.cleanup();assert.deepEqual(f.removed,[s.container.Id]);
});

test('capacity is bounded and lease remains held until last job completes',{skip},async t=>{
  const f=fixture(t),a=await f.allocate(),b=await f.allocate();
  await assert.rejects(f.allocate(),{code:'readonly-capacity-exhausted',readonlyCleanupConfirmed:true});
  await a.cleanup();assert(f.leaseState.owner!==null);const c=await f.allocate();await b.cleanup();await c.cleanup();assert.equal(f.leaseState.owner,null);
});

test('image failure allocates no job and confirms no resources',{skip},async t=>{
  const f=fixture(t);f.imageMissing=true;
  await assert.rejects(f.allocate(),{code:'IMAGE_UNAVAILABLE',readonlyCleanupConfirmed:true});
  assert.deepEqual(fs.readdirSync(path.join(f.stateRoot,'ods-readonly')),[]);assert.equal(f.leaseState.owner,null);
});

test('unlabelled matching runtime never counts as absent and is not removed',{skip},async t=>{
  const f=fixture(t),a=await f.allocate(),s=f.simulate(a);
  delete s.container.Config.Labels['openclaw.sandbox'];
  await assert.rejects(a.cleanup(),/ownership: label missing/);
  assert(f.containers.has(s.container.Id));assert.deepEqual(f.removed,[]);assert(fs.existsSync(f.job(a)));
});

test('empty pre-journal directory needs healthy absence before recovery',{skip},async t=>{
  const f=fixture(t),nonce='b'.repeat(32),dir=path.join(f.stateRoot,'ods-readonly',nonce);
  fs.mkdirSync(dir,{mode:0o700});const bytes=fs.readFileSync(f.databasePath);fs.unlinkSync(f.databasePath);
  await assert.rejects(f.allocator.recover(),{code:'readonly-registry-unavailable'});assert(fs.existsSync(dir));
  fs.writeFileSync(f.databasePath,bytes,{mode:0o600});await f.allocator.recover();assert(!fs.existsSync(dir));
});

test('startup rejects malformed journal and unknown root entries without deleting them',{skip},async t=>{
  const f=fixture(t),nonce='b'.repeat(32),dir=path.join(f.stateRoot,'ods-readonly',nonce);
  fs.mkdirSync(dir,{mode:0o700});fs.writeFileSync(path.join(dir,'journal.json'),'{bad',{mode:0o600});
  await assert.rejects(f.allocator.recover(),SyntaxError);assert(fs.existsSync(path.join(dir,'journal.json')));
  fs.unlinkSync(path.join(dir,'journal.json'));fs.rmdirSync(dir);
  const unknown=path.join(f.stateRoot,'ods-readonly','unknown');fs.mkdirSync(unknown,{mode:0o700});
  await assert.rejects(f.allocator.recover(),{code:'readonly-unexpected-job'});assert(fs.existsSync(unknown));
});

test('lease acquisition failure confirms this call allocated no resources',{skip},async t=>{
  const f=fixture(t);f.leaseState.owner=1;
  const before=fs.readdirSync(path.join(f.stateRoot,'ods-readonly'));
  await assert.rejects(f.allocate(),{code:'LEASE_BUSY',readonlyCleanupConfirmed:true});
  assert.deepEqual(fs.readdirSync(path.join(f.stateRoot,'ods-readonly')),before);
});
