import {createHash,randomBytes} from 'node:crypto';
import {isDeepStrictEqual} from 'node:util';
import {performance} from 'node:perf_hooks';

const TOOL='pixel_ods_workspace_preview_inspect';
const exact=(value,keys)=>value&&typeof value==='object'&&!Array.isArray(value)&&
  Object.keys(value).sort().join(',')===[...keys].sort().join(',');
const hex=(value,length)=>typeof value==='string'&&new RegExp(`^[a-f0-9]{${length}}$`).test(value);

export function normalizePreviewLeaseInput(value, normalizeInspection) {
  const base=['mode','siteId','sha256','viewport'];
  if(value?.mode==='snapshot') {
    if(!exact(value,base))throw Error('invalid snapshot request');
    normalizeInspection({siteId:value.siteId,sha256:value.sha256,viewport:value.viewport,
      steps:[{action:'assert-visible',locator:{selector:'html'}}]});
  } else if(value?.mode==='continue') {
    if(!exact(value,[...base,'leaseId','steps'])||!hex(value.leaseId,32))throw Error('invalid lease continuation');
    normalizeInspection({siteId:value.siteId,sha256:value.sha256,viewport:value.viewport,steps:value.steps});
  } else if(value?.mode==='close') {
    if(!exact(value,[...base,'leaseId'])||!hex(value.leaseId,32))throw Error('invalid lease close');
    normalizeInspection({siteId:value.siteId,sha256:value.sha256,viewport:value.viewport,
      steps:[{action:'assert-visible',locator:{selector:'html'}}]});
  } else throw Error('invalid lease mode');
  return structuredClone(value);
}

export function createPreviewDocumentLeases({now=()=>performance.now()}={}) {
  const runtime=randomBytes(32).toString('hex'),pending=new Map(),leases=new Map();
  let opening=false;
  const scopeFor=context=>createHash('sha256').update(JSON.stringify([
    runtime,context.agentId,context.runId,context.sessionId,context.sessionKey])).digest('hex');
  const before=(event,context,decision)=>{
    if(decision?.block||context?.agentId!=='pixel')return;
    const params=decision?.params??event?.params;
    const deferred=event?.toolName==='tool_call'&&[TOOL,'openclaw:pixel-ods:'+TOOL].includes(params?.id);
    if(event?.toolName!==TOOL&&!deferred)return;
    const args=deferred?params.args:params,id=context?.toolCallId??event?.toolCallId;
    if(!args?.mode||!id||!context.runId||!context.sessionId||!context.sessionKey)return;
    while(pending.size>=128)pending.delete(pending.keys().next().value);
    pending.set(id,{context:{...context},args:structuredClone(args),deferred,used:false});
  };
  const take=(id,args,factory)=>{
    let entry=pending.get(id);
    if(!entry) {
      const matches=[...pending].filter(([parentId,item])=>{
        const parent=parentId.trim().replace(/[^A-Za-z0-9_.:-]+/g,'_').slice(0,120)||'call';
        const prefix=`tool_search_code:${parent}:${TOOL}:`;
        return item.deferred&&id.startsWith(prefix)&&/^[1-9][0-9]*$/.test(id.slice(prefix.length));
      });
      if(matches.length===1)entry=matches[0][1];
    }
    if(!entry||entry.used||!isDeepStrictEqual(entry.args,args)||
      ['agentId','sessionId','sessionKey'].some(key=>entry.context[key]!==factory?.[key]))throw Error('unbound inspection lease call');
    entry.used=true;
    return Object.freeze({...entry.context});
  };
  const closeOwned=async(leaseId,lease)=>{
    const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),5000);
    try {
      const result=await lease.request({schemaVersion:2,action:'lease',operation:'close',scope:lease.scope,
        leaseId,containerId:lease.containerId},{signal:abort.signal});
      if(result?.status==='closed'&&result.scope===lease.scope&&result.leaseId===leaseId)leases.delete(leaseId);
    } catch { /* Retain custody; the capsule independently expires. Never claim settled. */ }
    finally {clearTimeout(timer);}
  };
  return {
    before,
    after(event,context){pending.delete(context?.toolCallId??event?.toolCallId);},
    async finish(event,context) {
      const runId=event?.runId??context?.runId;
      await Promise.all([...leases].filter(([,lease])=>lease.owner.runId===runId&&
        lease.owner.sessionId===context?.sessionId&&lease.owner.sessionKey===context?.sessionKey).map(async([leaseId,lease])=>{
        await closeOwned(leaseId,lease);
      }));
    },
    async abortCall(id,factory) {
      await Promise.all([...leases].filter(([,lease])=>lease.activeCallId===id&&
        ['agentId','sessionId','sessionKey'].every(key=>lease.owner[key]===factory?.[key])).map(([leaseId,lease])=>closeOwned(leaseId,lease)));
    },
    async execute(id,args,factory,request,normalizeInspection,{signal}={}) {
      signal?.throwIfAborted();
      const owner=take(id,args,factory),scope=scopeFor(owner),binding={siteId:args.siteId,sha256:args.sha256,viewport:args.viewport};
      if(args.mode==='snapshot') {
        for(const [id,item]of leases)if(!item.busy&&now()>=Math.min(item.deadline,item.idleDeadline))leases.delete(id);
        if(opening||leases.size>=1)throw Error('close existing document lease first');
        opening=true;
        let result;
        try {result=await request({schemaVersion:2,action:'lease',operation:'open',scope,...binding},{signal});}
        finally {opening=false;}
        if(result?.schemaVersion!==2||result.kind!=='ods-pixel-preview-snapshot'||result.status!=='snapshot'||
          result.scope!==scope||!hex(result.leaseId,32)||!hex(result.containerId,64)||!hex(result.documentGeneration,32)||
          result.siteId!==binding.siteId||result.sha256!==binding.sha256||!isDeepStrictEqual(result.viewport,binding.viewport)||
          result.descriptionsAreUntrusted!==true||result.maximumLifetimeSeconds!==120||result.idleSeconds!==45||
          !Array.isArray(result.elements)||result.elements.length>128||result.elements.some(item=>
            !exact(item,['ref','tag','role','name'])||!hex(item.ref,32)||
            [['tag',32],['role',40],['name',160]].some(([key,max])=>typeof item[key]!=='string'||Array.from(item[key]).length>max))||
          new Set(result.elements.map(item=>item.ref)).size!==result.elements.length)throw Error('invalid document snapshot receipt');
        leases.set(result.leaseId,{scope,containerId:result.containerId,binding,owner,request,activeCallId:id,
          generation:result.documentGeneration,deadline:now()+120000,idleDeadline:now()+45000});
        signal?.throwIfAborted();
        const {containerId:privateContainer,scope:privateScope,...visible}=result;
        return visible;
      }
      const lease=leases.get(args.leaseId);
      if(!lease||lease.scope!==scope||now()>=Math.min(lease.deadline,lease.idleDeadline)||!isDeepStrictEqual(lease.binding,binding))throw Error('stale or foreign document lease');
      if(lease.busy)throw Error('document inspection already active');
      lease.activeCallId=id;
      const operation=args.mode==='close'?'close':'inspect';
      const payload={schemaVersion:2,action:'lease',operation,scope,leaseId:args.leaseId,containerId:lease.containerId};
      if(operation==='inspect')payload.request=normalizeInspection({siteId:args.siteId,sha256:args.sha256,viewport:args.viewport,steps:args.steps});
      lease.busy=true;
      let result;
      try {result=await request(payload,{signal});}
      finally {lease.busy=false;}
      signal?.throwIfAborted();
      if(result?.schemaVersion!==2||result.kind!=='ods-pixel-preview-lease'||result.scope!==scope||
          result.leaseId!==args.leaseId||result.status!==(operation==='close'?'closed':'inspected'))throw Error('invalid document continuation receipt');
      if(operation==='close'){leases.delete(args.leaseId);return {schemaVersion:2,kind:result.kind,status:'closed',leaseId:args.leaseId};}
      if(result.documentGeneration!==lease.generation)throw Error('document generation changed');
      lease.idleDeadline=now()+45000;
      return result.result;
    },
  };
}
