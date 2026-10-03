// Transcript custody for Portal image deletion. HTTP never supplies a path or
// session ID. The native SDK supplies the binding; inode + header proof follows
// native archive renames without treating a filename prefix as authority.
import fs from 'node:fs';
import path from 'node:path';
import {createHash,randomBytes,randomUUID} from 'node:crypto';

const PREFIX='agent:pixel:openai-user:';
const USER=/^ods-[a-f0-9]{64}$/;
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const fail=()=>{throw new Error('conversation image deletion unavailable');};
function stat(file,directory=false,privateMode=false) {
  const value=fs.lstatSync(file);
  if(value.isSymbolicLink() || (directory?!value.isDirectory():!value.isFile() || value.nlink!==1)
    || process.platform!=='win32' && (value.uid!==process.getuid() || value.mode & (privateMode?0o077:0o022)))fail();
  return value;
}
function header(file,expected) {
  const fd=fs.openSync(file,fs.constants.O_RDONLY|(fs.constants.O_NOFOLLOW||0));
  try {
    const value=fs.fstatSync(fd), buffer=Buffer.alloc(8192), count=fs.readSync(fd,buffer,0,buffer.length,0);
    if(expected && (value.ino!==expected.ino || value.dev!==expected.dev))fail();
    const newline=buffer.subarray(0,count).indexOf(10);if(newline<0)fail();
    const line=buffer.subarray(0,newline), parsed=JSON.parse(line.toString('utf8'));
    if(parsed.type!=='session' || !UUID.test(parsed.id??''))fail();
    const identity=fs.fstatSync(fd,{bigint:true});
    return {sessionId:parsed.id,ino:String(identity.ino),dev:String(identity.dev),header:createHash('sha256').update(line).digest('hex')};
  } finally {fs.closeSync(fd);}
}

export function createConversationImageLifecycle({readConfig,getSessionEntry,patchSessionEntry,resolveStorePath,callGateway,compactor}) {
  function locations() {
    const store=resolveStorePath(readConfig()?.session?.store,{agentId:'pixel'});
    const sessions=path.dirname(store);
    if(!path.isAbsolute(store) || fs.realpathSync(sessions)!==sessions)fail();
    stat(sessions,true);
    const directory=path.join(sessions,'.ods-image-custody');
    fs.mkdirSync(directory,{mode:0o700});
    return {sessions,directory};
  }
  function scoped(user) {
    if(typeof user!=='string' || user.length!==68 || !USER.test(user))fail();
    let result;
    try {result=locations();} catch(error) {if(error.code!=='EEXIST')throw error;
      const sessions=path.dirname(resolveStorePath(readConfig()?.session?.store,{agentId:'pixel'}));
      if(fs.realpathSync(sessions)!==sessions)fail();stat(sessions,true);
      result={sessions,directory:path.join(sessions,'.ods-image-custody')};}
    stat(result.directory,true,true);
    return {...result,file:path.join(result.directory,`${user}.json`)};
  }
  function read(user,location) {
    try {
      if(stat(location.file,false,true).size>65536)fail();
      const value=JSON.parse(fs.readFileSync(location.file,'utf8'));
      if(value.schemaVersion!==1 || value.user!==user || !['open','deleting','deleted'].includes(value.status)
        || !Array.isArray(value.files) || value.files.length>128 || !Array.isArray(value.sessions) || value.sessions.length>128)fail();
      for(const id of value.sessions)if(typeof id!=='string' || !UUID.test(id))fail();
      for(const file of value.files)if(!value.sessions.includes(file.sessionId) || typeof file.ino!=='string' || !/^[0-9]{1,32}$/.test(file.ino)
        || typeof file.dev!=='string' || !/^[0-9]{1,32}$/.test(file.dev) || typeof file.header!=='string' || !/^[a-f0-9]{64}$/.test(file.header))fail();
      return value;
    } catch(error) {if(error.code==='ENOENT')return {schemaVersion:1,user,status:'open',sessions:[],files:[]};throw error;}
  }
  function save(location,value) {
    if(value.files.length>128 || value.sessions.length>128)fail();
    // Tombstones cannot be evicted without reopening stale conversation IDs.
    // Refuse new custody entries at the bound; existing deletion retries work.
    if(!fs.existsSync(location.file) && fs.readdirSync(location.directory).length>=8192)fail();
    const temporary=`${location.file}.tmp-${randomBytes(12).toString('hex')}`;
    let fd;
    try {fd=fs.openSync(temporary,'wx',0o600);fs.writeFileSync(fd,JSON.stringify(value));fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;fs.renameSync(temporary,location.file);
      let directory;try {directory=fs.openSync(location.directory,'r');fs.fsyncSync(directory);}catch(error){if(process.platform!=='win32')throw error;}finally{if(directory!==undefined)fs.closeSync(directory);}
    } finally {if(fd!==undefined)fs.closeSync(fd);try{fs.unlinkSync(temporary);}catch(error){if(error.code!=='ENOENT')throw error;}}
  }
  function capture(user,location,value) {
    const entry=getSessionEntry({agentId:'pixel',sessionKey:PREFIX+user,storePath:resolveStorePath(readConfig()?.session?.store,{agentId:'pixel'})});
    if(!entry)return null;
    if(!UUID.test(entry.sessionId??''))fail();
    // This first implementation supports the native standard transcript path.
    // Custom/database backends fail closed rather than deleting arbitrary paths.
    const file=path.join(location.sessions,`${entry.sessionId}.jsonl`);
    if(entry.sessionFile && path.resolve(entry.sessionFile)!==file)fail();
    if(!value.sessions.includes(entry.sessionId))value.sessions.push(entry.sessionId);
    try {const proof=header(file,stat(file));if(proof.sessionId!==entry.sessionId)fail();
      if(!value.files.some(old=>old.ino===proof.ino && old.dev===proof.dev && old.header===proof.header))value.files.push(proof);
    }catch(error){if(error.code!=='ENOENT')throw error;}
    return entry;
  }
  function observe(context) {
    const key=context?.sessionKey;
    if(typeof key!=='string' || !key.startsWith(PREFIX))return;
    const user=key.slice(PREFIX.length);
    if(user.length!==68 || !USER.test(user))fail();
    // Plain text sessions do not acquire an image lifecycle dependency. Only
    // the trusted ingress may enroll a conversation before sending image bytes.
    const sessions=path.dirname(resolveStorePath(readConfig()?.session?.store,{agentId:'pixel'}));
    if(!fs.existsSync(path.join(sessions,'.ods-image-custody',`${user}.json`)))return;
    const location=scoped(user), value=read(user,location);
    if(value.status!=='open')fail();
    const entry=capture(user,location,value);
    if(entry && context.sessionId && context.sessionId!==entry.sessionId)fail();
    save(location,value);
  }
  async function bind(user) {
    return compactor.withMaintenance(user,async()=>{
      const location=scoped(user), value=read(user,location);
      if(value.status!=='open')fail();
      const scope={agentId:'pixel',sessionKey:PREFIX+user,storePath:resolveStorePath(readConfig()?.session?.store,{agentId:'pixel'})};
      if(!getSessionEntry(scope)) {
        const fallbackEntry={sessionId:randomUUID(),updatedAt:Date.now()};
        await patchSessionEntry({...scope,fallbackEntry,update:(_entry,{existingEntry})=>existingEntry?null:fallbackEntry});
      }
      if(!capture(user,location,value))fail();
      save(location,value);return {schemaVersion:1,bound:true};
    });
  }
  async function purge(user) {
    return compactor.withMaintenance(user,async()=>{
      const location=scoped(user), value=read(user,location);
      if(value.status==='deleted')return {schemaVersion:1,deleted:true};
      const entry=getSessionEntry({agentId:'pixel',sessionKey:PREFIX+user,storePath:resolveStorePath(readConfig()?.session?.store,{agentId:'pixel'})});
      if(value.status==='deleting' && entry && !value.sessions.includes(entry.sessionId))fail();
      capture(user,location,value);value.status='deleting';save(location,value);
      if(entry) {
        const receipt=await callGateway('sessions.delete',{timeoutMs:10000},{key:PREFIX+user,agentId:'pixel',deleteTranscript:true,emitLifecycleHooks:false});
        if(receipt?.ok!==true || receipt.key!==PREFIX+user)fail();
      }
      const names=fs.readdirSync(location.sessions);if(names.length>8192)fail();
      for(const name of names) {
        const file=path.join(location.sessions,name);let info;
        try {info=fs.lstatSync(file);}catch(error){if(error.code==='ENOENT')continue;throw error;}
        const identity=fs.lstatSync(file,{bigint:true});
        let proof=value.files.find(old=>old.ino===String(identity.ino) && old.dev===String(identity.dev));
        if(!proof) {
          // A native rotation can finish after the last agent hook. Recover
          // only a registered UUID's standard native transcript/archive and
          // verify its session header before acquiring deletion custody.
          const id=value.sessions.find(id=>name===`${id}.jsonl` || name.startsWith(`${id}.jsonl.deleted.`) || name.startsWith(`${id}.jsonl.reset.`));
          if(!id)continue;
          stat(file);const actual=header(file,info);if(actual.sessionId!==id)fail();
          proof=actual;value.files.push(proof);save(location,value);
        }
        stat(file);const actual=header(file,info);
        if(actual.header!==proof.header || actual.sessionId!==proof.sessionId)fail();
        fs.unlinkSync(file);
      }
      let directory;try {directory=fs.openSync(location.sessions,'r');fs.fsyncSync(directory);}catch(error){if(process.platform!=='win32')throw error;}finally{if(directory!==undefined)fs.closeSync(directory);}
      value.status='deleted';save(location,value);
      return {schemaVersion:1,deleted:true};
    });
  }
  function register(api) {
    for(const operation of ['images-bind','images-delete'])api.registerHttpRoute({path:`/pixel-ods/${operation}`,auth:'gateway',match:'exact',handler:async(req,res)=>{
      const send=(status,value)=>{res.writeHead(status,{'content-type':'application/json','cache-control':'no-store'});res.end(JSON.stringify(value));return true;};
      if(req.url!==`/pixel-ods/${operation}` || req.method!=='POST')return send(405,{error:'method-not-allowed'});
      const timer=setTimeout(()=>req.destroy(),30000);timer.unref?.();
      try {const chunks=[];let bytes=0;for await(const chunk of req){bytes+=chunk.length;if(bytes>256)return send(413,{error:'request-too-large'});chunks.push(chunk);}
        const body=JSON.parse(Buffer.concat(chunks).toString('utf8'));if(!body || Object.keys(body).join()!=='user')return send(400,{error:'invalid-request'});
        return send(200,await (operation==='images-bind'?bind(body.user):purge(body.user)));
      }catch{return send(503,{error:'conversation-image-deletion-unavailable'});}
      finally{clearTimeout(timer);}
    }});
  }
  return {observe,bind,purge,register};
}
