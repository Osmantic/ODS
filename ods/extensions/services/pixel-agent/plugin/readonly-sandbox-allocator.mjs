import fs from 'node:fs';
import path from 'node:path';
import {randomBytes} from 'node:crypto';
import {buildReadonlyExecutionConfig} from './readonly-execution-config.mjs';
import {createReadonlyDockerLifecycle} from './readonly-docker-lifecycle.mjs';
import {createReadonlySandboxRegistry} from './readonly-sandbox-registry.mjs';

const NONCE=/^[0-9a-f]{32}$/, ID=/^[0-9a-f]{64}$/, IMAGE=/^sha256:[0-9a-f]{64}$/;
const fail=(code,cause)=>Object.assign(new Error(code),{code,...(cause?{cause}:{})});
const inside=(parent,child)=>child===parent||child.startsWith(parent+'/');
const absolute=value=>typeof value==='string'&&value!=='/'&&path.posix.isAbsolute(value)&&
  path.posix.normalize(value)===value&&!value.endsWith('/')&&!/[\\\x00-\x1f\x7f]/.test(value);
const sessionFor=nonce=>'agent:pixel:ods-readonly:'+nonce;
const prefixFor=nonce=>'ods-pixel-ro-'+nonce.slice(0,16)+'-';
const validName=(name,prefix)=>typeof name==='string'&&name.startsWith(prefix)&&name.length<=63&&/^[a-z0-9._-]+$/.test(name);

function privatePath(file,directory){
  const stat=fs.lstatSync(file);
  if(stat.isSymbolicLink()||!(directory?stat.isDirectory():stat.isFile())||
      (!directory&&stat.nlink!==1)||(stat.mode&0o777)!==(directory?0o700:0o600)||
      (typeof process.getuid==='function'&&stat.uid!==process.getuid())||fs.realpathSync(file)!==file)
    throw fail('readonly-state-ownership');
}
function syncDirectory(directory){const fd=fs.openSync(directory,'r');try{fs.fsyncSync(fd);}finally{fs.closeSync(fd);}}
function writeJournal(job){
  privatePath(job.dir,true);
  const temporary=path.join(job.dir,'.journal-'+randomBytes(8).toString('hex')+'.tmp');
  let fd;
  try{
    fd=fs.openSync(temporary,'wx',0o600);fs.writeFileSync(fd,JSON.stringify(job.record));fs.fsyncSync(fd);
    fs.closeSync(fd);fd=undefined;fs.renameSync(temporary,path.join(job.dir,'journal.json'));syncDirectory(job.dir);
  }finally{
    if(fd!==undefined)fs.closeSync(fd);
    try{fs.unlinkSync(temporary);}catch(error){if(error.code!=='ENOENT')throw error;}
  }
}

// Host control only: image inspection, owned-runtime cleanup and private job
// journals. Model commands enter exclusively through the SDK execution tool.
export function createReadonlySandboxAllocator({stateRoot,runDocker,runCli,acquireLease,clock=Date.now,
  randomNonce=()=>randomBytes(16).toString('hex')}={}){
  if(!absolute(stateRoot)||typeof runDocker!=='function'||typeof runCli!=='function'||typeof acquireLease!=='function')throw fail('readonly-allocator-invalid');
  privatePath(stateRoot,true);
  const root=path.join(stateRoot,'ods-readonly'),active=new Map();
  if(!fs.existsSync(root)){fs.mkdirSync(root,{mode:0o700});syncDirectory(stateRoot);}
  privatePath(root,true);
  let tail=Promise.resolve(),lease;
  const serial=operation=>{
    const result=tail.then(async()=>{
      // The public SDK lock protects startup recovery from another live gateway.
      // Hold it across active jobs, not just across individual filesystem writes.
      lease??=await acquireLease({sessionFile:path.join(root,'controller.jsonl'),timeoutMs:1000,
        staleMs:86400000,maxHoldMs:86400000,allowReentrant:false});
      try{return await operation();}
      finally{if(active.size===0){await lease.release();lease=undefined;}}
    });
    tail=result.catch(()=>{});return result;
  };
  const registry=()=>createReadonlySandboxRegistry({databasePath:path.join(stateRoot,'state','openclaw.sqlite')});
  function removeJob(job){
    privatePath(root,true);privatePath(job.dir,true);
    if(job.dir!==path.join(root,job.record.nonce)||!NONCE.test(job.record.nonce))throw fail('readonly-cleanup-path');
    fs.rmSync(job.dir,{recursive:true});syncDirectory(root);
  }
  async function inspect(name){
    let output;
    try{output=await runDocker(['inspect','--type','container',name]);}
    catch(error){if(error.code==='DOCKER_NOT_FOUND')return null;throw error;}
    const rows=JSON.parse(output);
    if(!Array.isArray(rows)||rows.length!==1||!rows[0]||!ID.test(rows[0].Id))throw fail('readonly-inspect-invalid');
    return rows[0];
  }
  async function discover(prefix){
    // Include malformed/unlabelled runtimes so they cannot masquerade as absence.
    const output=await runDocker(['ps','-aq','--no-trunc','--filter','name=^/'+prefix]);
    if(typeof output!=='string')throw fail('readonly-discovery-invalid');
    const ids=output.trim().split(/\s+/).filter(Boolean);
    if(ids.length>1||ids.some(id=>!ID.test(id)))throw fail('readonly-discovery-ambiguous');
    return ids;
  }
  function validate(job){
    const j=job.record;
    if(!j||j.schemaVersion!==1||!NONCE.test(j.nonce)||job.dir!==path.join(root,j.nonce)||
        j.sessionKey!==sessionFor(j.nonce)||j.containerPrefix!==prefixFor(j.nonce)||!IMAGE.test(j.imageId)||
        !Number.isSafeInteger(j.createdAt)||j.createdAt<0||!absolute(j.workspace)||
        inside(root,j.workspace)||inside(j.workspace,root))throw fail('readonly-journal-invalid');
    if(j.bootstrap!==null&&(!absolute(j.bootstrap)||!j.bootstrap.startsWith(job.dir+'/sandboxes/')))
      throw fail('readonly-journal-bootstrap');
    if(j.containerName!==null&&!validName(j.containerName,j.containerPrefix))throw fail('readonly-journal-name');
    if(j.containerId!==null&&!ID.test(j.containerId))throw fail('readonly-journal-id');
  }
  function lifecycle(job){const j=job.record;return createReadonlyDockerLifecycle({docker:runDocker,expected:{
    containerName:j.containerName,expectedImageId:j.imageId,workspace:j.workspace,bootstrap:j.bootstrap,
    createdAfterEpochMs:j.createdAt,maximumExpectedMemoryBytes:512*1024*1024,requireNoNewPrivileges:true}});}
  async function bind(job,container){
    const j=job.record,name=container?.Name?.slice(1);
    const bootstrap=container?.Mounts?.find(m=>m.Destination==='/workspace')?.Source;
    if(!validName(name,j.containerPrefix)||!absolute(bootstrap)||!bootstrap.startsWith(job.dir+'/sandboxes/')||
        fs.realpathSync(bootstrap)!==bootstrap||
        (j.containerName!==null&&j.containerName!==name)||(j.containerId!==null&&j.containerId!==container.Id)||
        (j.bootstrap!==null&&j.bootstrap!==bootstrap))throw fail('readonly-runtime-ownership');
    const candidate={...job,record:{...j,containerName:name,containerId:container.Id,bootstrap}};
    const control=lifecycle(candidate),observed=await control.inspect();
    if(!observed.exists||observed.id!==container.Id)throw fail('readonly-runtime-identity');
    Object.assign(j,candidate.record);writeJournal(job);return control;
  }
  async function cleanupJob(job){
    validate(job);privatePath(job.dir,true);
    const j=job.record,ids=await discover(j.containerPrefix);
    if(ids.length){
      const container=await inspect(ids[0]);
      if(!container||container.Id!==ids[0])throw fail('readonly-runtime-missing');
      const control=await bind(job,container);await control.cleanup();
    }else if(j.containerId!==null&&await inspect(j.containerId))throw fail('readonly-runtime-replaced');
    const reader=registry(),rows=reader.readSession(j.sessionKey);
    if(rows.length>1)throw fail('readonly-registry-ambiguous');
    if(rows.length){
      const row=rows[0];
      if(row.registry_kind!=='container'||![null,'docker'].includes(row.backend_id)||row.image!==j.imageId||
          row.created_at_ms<j.createdAt-2000||!validName(row.container_name,j.containerPrefix)||
          (j.containerName!==null&&row.container_name!==j.containerName))throw fail('readonly-registry-ownership');
      if(j.containerName===null){j.containerName=row.container_name;writeJournal(job);}
      if(await inspect(j.containerName))throw fail('readonly-runtime-replaced');
      const output=await runCli(['sandbox','recreate','--session',j.sessionKey,'--force']);
      const lines=String(output).replace(/\x1b\[[0-9;]*m/g,'').split(/\r?\n/).map(line=>line.trim());
      if(!lines.some(line=>line==='Removed '+j.containerName||line==='✓ Removed '+j.containerName)||
          !lines.includes('Done: 1 removed, 0 failed'))throw fail('readonly-registry-cleanup-unconfirmed');
    }
    if(reader.readSession(j.sessionKey).length|| (j.containerName!==null&&await inspect(j.containerName))||
        (await discover(j.containerPrefix)).length)throw fail('readonly-cleanup-unconfirmed');
    removeJob(job);active.delete(j.nonce);job.cleaned=true;
  }
  async function recoverEmptyDirectory(dir,nonce){
    // A crash before the durable journal can leave only allocation temp files.
    // No SDK call is made until journal persistence has completed.
    for(const name of fs.readdirSync(dir)){
      if(!/^\.journal-[0-9a-f]{16}\.tmp$/.test(name))throw fail('readonly-journal-missing');
      privatePath(path.join(dir,name),false);
    }
    if(registry().readSession(sessionFor(nonce)).length||(await discover(prefixFor(nonce))).length)
      throw fail('readonly-unrecorded-runtime');
    removeJob({dir,record:{nonce}});
  }
  async function recoverInternal(){
    privatePath(root,true);
    for(const nonce of fs.readdirSync(root)){
      if(nonce==='controller.jsonl.lock')continue; // Owned by acquireSessionWriteLock.
      if(!NONCE.test(nonce))throw fail('readonly-unexpected-job');
      if(active.has(nonce))continue;
      const dir=path.join(root,nonce);privatePath(dir,true);
      const journal=path.join(dir,'journal.json');
      if(!fs.existsSync(journal)){await recoverEmptyDirectory(dir,nonce);continue;}
      privatePath(journal,false);
      const job={dir,record:JSON.parse(fs.readFileSync(journal,'utf8'))};validate(job);await cleanupJob(job);
    }
  }
  async function allocate(scope,config){
    let entered=false;
    return serial(async()=>{
    entered=true;
    let job;
    try{
      await recoverInternal();
      if(active.size>=2)throw fail('readonly-capacity-exhausted');
      const workspace=scope.workspaceRoot;
      if(!absolute(workspace)||fs.realpathSync(workspace)!==workspace||inside(workspace,root)||inside(root,workspace))
        throw fail('readonly-workspace-invalid');
      const image=config?.agents?.list?.find(a=>a?.id==='pixel')?.sandbox?.docker?.image??config?.agents?.defaults?.sandbox?.docker?.image;
      if(typeof image!=='string'||!image.trim()||image.startsWith('-'))throw fail('readonly-image-unconfigured');
      const images=JSON.parse(await runDocker(['image','inspect',image]));
      if(!Array.isArray(images)||images.length!==1||!IMAGE.test(images[0]?.Id))throw fail('readonly-image-invalid');
      const nonce=randomNonce(),createdAt=clock();
      if(!NONCE.test(nonce)||!Number.isSafeInteger(createdAt)||createdAt<0)throw fail('readonly-allocation-invalid');
      if(registry().readSession(sessionFor(nonce)).length)throw fail('readonly-allocation-collision');
      const dir=path.join(root,nonce);
      fs.mkdirSync(dir,{mode:0o700});
      job={dir,record:{schemaVersion:1,nonce,sessionKey:sessionFor(nonce),containerPrefix:prefixFor(nonce),
        workspace,createdAt,imageId:images[0].Id,bootstrap:null,containerName:null,containerId:null},cleaned:false};
      writeJournal(job);syncDirectory(root);
      const built=buildReadonlyExecutionConfig(config,{workspaceRoot:workspace,privateRoot:dir,nonce,imageId:job.record.imageId});
      active.set(nonce,job);
      return {config:built.config,sandboxSessionKey:job.record.sessionKey,ownerWorkspace:workspace,createdAt,
        attach:sandbox=>serial(async()=>{
          if(job.cleaned||active.get(nonce)!==job||sandbox?.enabled!==true||sandbox.workspaceAccess!=='ro'||
              sandbox.agentWorkspaceDir!==workspace||sandbox.runtimeId!==sandbox.containerName||
              !validName(sandbox.containerName,job.record.containerPrefix))throw fail('readonly-sandbox-invalid');
          const observed=await inspect(sandbox.containerName);
          if(!observed)throw fail('readonly-runtime-missing');
          const control=await bind(job,observed);
          if(sandbox.workspaceDir!==job.record.bootstrap)throw fail('readonly-bootstrap-mismatch');
          await control.admit();
        }),
        cleanup:()=>serial(async()=>{if(!job.cleaned)await cleanupJob(job);}),
      };
    }catch(error){
      const wrapped=fail(error?.code??'readonly-allocation-failed',error);
      // allocate never calls the SDK; until it returns only its own job files exist.
      if(job){
        const cleanup=async()=>{if(!job.cleaned){removeJob(job);active.delete(job.record.nonce);job.cleaned=true;}};
        try{await cleanup();wrapped.readonlyCleanupConfirmed=true;}
        catch(cause){wrapped.code='readonly-cleanup-unconfirmed';wrapped.readonlyRecovery={cleanup};wrapped.cause=cause;}
      }else wrapped.readonlyCleanupConfirmed=true;
      throw wrapped;
    }
  }).catch(error=>{
    if(entered)throw error;
    // Lease contention occurred before recovery, image inspection or job creation.
    // It must not latch the runner as an unconfirmed resource cleanup.
    const wrapped=fail(error?.code??'readonly-lease-unavailable',error);
    wrapped.readonlyCleanupConfirmed=true;throw wrapped;
  });}
  return Object.freeze({allocate,recover:()=>serial(recoverInternal)});
}
