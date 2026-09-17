// Durable delivery acknowledgement. The visible transcript is never rewritten
// by compaction; only new, not-yet-delivered messages go to the native session.
import fs from 'node:fs';
import path from 'node:path';
import {createHash,randomBytes} from 'node:crypto';

export const HISTORY_BYTES = 4 * 1024 * 1024;
export const HISTORY_MESSAGES = 2000;
const FILE_BYTES = 12 * 1024 * 1024;
const USER = /^ods-[a-f0-9]{64}$/;
const REQUEST = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
export class HistoryError extends Error {
  constructor(code,status=409) { super(code.replaceAll('_','-')); this.code=this.message; this.status=status; }
}
export function validateHistorySnapshot(value) {
  if (!value || Object.keys(value).sort().join() !== 'messages,schemaVersion' || value.schemaVersion !== 1 || !Array.isArray(value.messages) || !value.messages.length || value.messages.length > HISTORY_MESSAGES) throw new HistoryError('invalid_history_snapshot',400);
  let bytes=0;
  const messages=value.messages.map(message => {
    if (!message || Object.keys(message).sort().join() !== 'content,role' || !['user','assistant'].includes(message.role) || typeof message.content !== 'string' || /\u0000/.test(message.content)) throw new HistoryError('invalid_history_snapshot',400);
    bytes+=Buffer.byteLength(message.content,'utf8');
    if (bytes>HISTORY_BYTES) throw new HistoryError('history_too_large',413);
    return {role:message.role,content:message.content};
  });
  if (messages.at(-1).role !== 'user' || !messages.at(-1).content.trim()) throw new HistoryError('invalid_history_snapshot',400);
  return messages;
}
function privateStat(file,directory=false) {
  const stat=fs.lstatSync(file);
  if (stat.isSymbolicLink() || (directory ? !stat.isDirectory() : !stat.isFile()) || (!directory && stat.nlink !== 1) || (process.platform !== 'win32' && (stat.mode & 0o077 || stat.uid !== process.getuid()))) throw new HistoryError('history_storage_unavailable',503);
  return stat;
}
function atomicWrite(file,value) {
  const bytes=Buffer.from(JSON.stringify(value));
  if (bytes.length>FILE_BYTES) throw new HistoryError('history_storage_limit',413);
  const temporary=`${file}.tmp-${process.pid}-${randomBytes(8).toString('hex')}`;
  let fd;
  try {
    fd=fs.openSync(temporary,'wx',0o600);
    fs.writeFileSync(fd,bytes);fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;
    fs.renameSync(temporary,file);
    try {const directory=fs.openSync(path.dirname(file),'r');try {fs.fsyncSync(directory)} finally {fs.closeSync(directory)}} catch(error) {if (!['EINVAL','ENOTSUP','EBADF','EISDIR','EPERM','EACCES'].includes(error.code)) throw error;}
  } finally {
    if (fd!==undefined) fs.closeSync(fd);
    try {fs.unlinkSync(temporary)} catch(error) {if(error.code!=='ENOENT') throw error;}
  }
}

export function createChatHistoryLedger(directory) {
  if (typeof directory !== 'string' || !path.isAbsolute(directory)) throw new HistoryError('history_storage_unavailable',503);
  fs.mkdirSync(directory,{recursive:true,mode:0o700});
  privateStat(directory,true);
  const active=new Set(), inflight=new Set();
  function filename(user) {if(!USER.test(user)) throw new HistoryError('invalid_history_user',400);return path.join(directory,`${user}.json`)}
  function read(user) {
    const file=filename(user);
    try {
      const stat=privateStat(file);
      if(stat.size>FILE_BYTES) throw new HistoryError('history_storage_unavailable',503);
      const fd=fs.openSync(file,fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
      let data;
      try {const held=fs.fstatSync(fd);if(held.ino!==stat.ino || held.dev!==stat.dev) throw new HistoryError('history_storage_unavailable',503);data=JSON.parse(fs.readFileSync(fd,'utf8'))} finally {fs.closeSync(fd)}
      if(data.schemaVersion!==1 || data.user!==user || !Array.isArray(data.messages) || !Number.isInteger(data.acknowledgedMessages) || data.acknowledgedMessages<0 || data.acknowledgedMessages>data.messages.length || !['ready','pending','unknown'].includes(data.status)) throw new HistoryError('history_storage_unavailable',503);
      if(data.status==='pending' && !inflight.has(user)) return {...data,status:'unknown',reason:'history-outcome-unknown'};
      return data;
    } catch(error) {if(error.code==='ENOENT') return null;if(error instanceof HistoryError) throw error;throw new HistoryError('history_storage_unavailable',503)}
  }
  function write(user,state) {
    privateStat(directory,true);
    const file=filename(user);
    try {privateStat(file)} catch(error) {if(error.code!=='ENOENT') throw error;}
    // Bound aggregate storage and fail visibly instead of discarding archives.
    const files=fs.readdirSync(directory).filter(name=>/^ods-[a-f0-9]{64}\.json$/.test(name));
    if(files.length>=1024 && !fs.existsSync(file)) throw new HistoryError('history_storage_limit',503);
    const existingBytes=files.reduce((sum,name)=>sum+privateStat(path.join(directory,name)).size,0);
    if(existingBytes+Buffer.byteLength(JSON.stringify(state))>512*1024*1024) throw new HistoryError('history_storage_limit',503);
    atomicWrite(file,state);
  }
  function lock(user) {
    filename(user);
    if(active.has(user)) throw new HistoryError('history_busy',409);
    const file=path.join(directory,`${user}.lock`);
    let fd;
    for(let attempt=0;attempt<2;attempt++) {
      try {fd=fs.openSync(file,'wx',0o600);fs.writeFileSync(fd,JSON.stringify({pid:process.pid}));break;}
      catch(error) {
        if(error.code!=='EEXIST') throw new HistoryError('history_storage_unavailable',503);
        const stat=privateStat(file);
        if(stat.size>128) throw new HistoryError('history_storage_unavailable',503);
        let owner;try {owner=JSON.parse(fs.readFileSync(file,'utf8'))} catch {throw new HistoryError('history_storage_unavailable',503)}
        if(!Number.isInteger(owner.pid) || owner.pid<1) throw new HistoryError('history_storage_unavailable',503);
        try {process.kill(owner.pid,0);throw new HistoryError('history_busy',409)}
        catch(probe) {if(probe.code!=='ESRCH') throw new HistoryError('history_busy',409)}
        const current=privateStat(file);
        if(current.ino!==stat.ino || current.dev!==stat.dev) throw new HistoryError('history_busy',409);
        fs.unlinkSync(file);
      }
    }
    if(fd===undefined) throw new HistoryError('history_busy',409);
    active.add(user);
    let released=false;
    return ()=>{if(released)return;released=true;active.delete(user);fs.closeSync(fd);try {fs.unlinkSync(file)} catch(error) {if(error.code!=='ENOENT') throw new HistoryError('history_storage_unavailable',503)}};
  }
  function projection(user) {
    const state=read(user);
    return {revision:state?.revision || null,acknowledgedMessages:state?.acknowledgedMessages || 0,status:state?.status || 'ready',reason:state?.reason || null};
  }
  function prepare(user,requestId,snapshot,native) {
    if(!REQUEST.test(requestId || '')) throw new HistoryError('invalid_request_id',400);
    const messages=validateHistorySnapshot(snapshot), revision=digest(messages), previous=read(user);
    if(native.status==='busy') throw new HistoryError('history_busy',409);
    if(!['ready','missing'].includes(native.status)) throw new HistoryError('context_unavailable',503);
    const seen=previous?.requests?.find(item=>item.id===requestId);
    if(seen) {
      if(seen.revision!==revision) throw new HistoryError('request_id_conflict',409);
      if(previous.lastResult?.requestId===requestId && previous.lastResult?.revision===revision) return {replay:previous.lastResult};
      throw new HistoryError(seen.status==='completed'?'request_already_delivered':'history_outcome_unknown',409);
    }
    if(previous && previous.status!=='ready') throw new HistoryError(previous.status==='pending'?'history_busy':'history_outcome_unknown',409);
    let acknowledged=previous?.acknowledgedMessages || 0;
    if(previous) {
      if(messages.length<=acknowledged || digest(messages.slice(0,acknowledged))!==digest(previous.messages.slice(0,acknowledged))) throw new HistoryError('history_changed',409);
    }
    const rotated=previous && native.sessionRevision!==previous.sessionRevision;
    const nativeCompactions=Number.isInteger(native.compaction?.count)?native.compaction.count:0;
    const coveredRotation=rotated && nativeCompactions>(previous.compactionCount || 0);
    // Initial adoption and lost sessions import the archived conversation as
    // inert historical data. Never assume a legacy bounded request carried it.
    const hydrate=!previous || acknowledged===0 || native.status==='missing' || (rotated && !coveredRotation);
    let delta=hydrate?messages.slice(-1):messages.slice(acknowledged);
    // This assistant turn was generated and stored by the native session after
    // our last acknowledged owner input. Other new local turns remain intact.
    if(previous && !hydrate && delta[0]?.role==='assistant') delta=delta.slice(1);
    if(!delta.length) throw new HistoryError('history_has_no_new_input',409);
    const state={schemaVersion:1,user,messages,revision,acknowledgedMessages:acknowledged,status:'pending',reason:null,
      sessionRevision:native.sessionRevision,compactionCount:nativeCompactions,requestId,
      requests:[...(previous?.requests || []).slice(-15),{id:requestId,revision,status:'pending'}]};
    write(user,state);
    inflight.add(user);
    return {state,delta,hydrate:hydrate && messages.length>1,archive:messages.slice(0,-1)};
  }
  function complete(user,prepared,completion,verification,native) {
    const current=read(user);
    if(current?.requestId!==prepared.state.requestId || !['pending','unknown'].includes(current.status)) return null;
    const state={...prepared.state,status:'ready',reason:null,acknowledgedMessages:prepared.state.messages.length,
      sessionRevision:native?.sessionRevision || prepared.state.sessionRevision,compactionCount:native?.compaction?.count ?? prepared.state.compactionCount,
      requests:prepared.state.requests.map(item=>item.id===prepared.state.requestId?{...item,status:'completed'}:item),
      lastResult:{requestId:prepared.state.requestId,revision:prepared.state.revision,completion,verification}};
    write(user,state);
    inflight.delete(user);
    return state;
  }
  function uncertain(user,prepared) {const current=read(user);if(prepared?.state && current?.requestId===prepared.state.requestId && ['pending','unknown'].includes(current.status)) write(user,{...prepared.state,status:'unknown',reason:'history-outcome-unknown'});inflight.delete(user);}
  function abandon(user,prepared,reason='history-preparation-failed') {
    const current=read(user);
    if(prepared?.state && current?.requestId===prepared.state.requestId && ['pending','unknown'].includes(current.status)) write(user,{...prepared.state,status:'ready',reason,requests:prepared.state.requests.map(item=>item.id===prepared.state.requestId?{...item,status:'failed'}:item)});
    inflight.delete(user);
  }
  function interrupt(user,requestId) {
    const current=read(user);
    if(!requestId || current?.requestId!==requestId || !['pending','unknown'].includes(current.status)) return false;
    write(user,{...current,status:'ready',reason:'history-interrupted',acknowledgedMessages:current.messages.length,
      requests:current.requests.map(item=>item.id===requestId?{...item,status:'interrupted'}:item)});
    inflight.delete(user);
    return true;
  }
  function search(user,{query='',offset=0,limit=5}={}) {
    if(typeof query!=='string' || query.length>200 || !Number.isInteger(offset) || offset<0 || offset>HISTORY_MESSAGES || !Number.isInteger(limit) || limit<1 || limit>20) throw new HistoryError('invalid_history_query',400);
    const state=read(user), matches=[], needle=query.toLocaleLowerCase();
    let bytes=0,nextOffset=null;
    for(let index=offset;index<(state?.messages.length || 0);index++) {
      const message=state.messages[index], position=needle?message.content.toLocaleLowerCase().indexOf(needle):0;
      if(position<0) continue;
      const start=Math.max(0,position-300), excerpt=message.content.slice(start,start+2400);
      if(matches.length>=limit || bytes+Buffer.byteLength(excerpt)>10000) {nextOffset=index;break;}
      matches.push({index,role:message.role,content:excerpt,truncated:start>0 || start+excerpt.length<message.content.length});bytes+=Buffer.byteLength(excerpt);
    }
    return {schemaVersion:1,source:'archived-conversation',untrusted:true,revision:state?.revision || null,totalMessages:state?.messages.length || 0,messages:matches,nextOffset};
  }
  return {read,projection,lock,prepare,complete,uncertain,abandon,interrupt,search};
}
