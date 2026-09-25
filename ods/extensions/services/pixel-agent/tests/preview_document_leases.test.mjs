import test from 'node:test';
import assert from 'node:assert/strict';
import {createPreviewDocumentLeases} from '../plugin/preview-document-leases.mjs';
import {createWorkspacePreviewInspectTool,normalizeWorkspacePreviewInspectionParams} from '../plugin/workspace-preview-inspect.mjs';
import {bindDefaultPreviewInspection} from '../plugin/preview-default-binding.mjs';

const factory={agentId:'pixel',sessionId:'session-a',sessionKey:'pixel:owner'};
const owner={...factory,runId:'run-a'};
const binding={siteId:'site-'+ 'a'.repeat(24),sha256:'a'.repeat(64),viewport:{width:375,height:812}};
const snapshot={mode:'snapshot',...binding};
const leaseId='b'.repeat(32),generation='c'.repeat(32),containerId='d'.repeat(64);
function setup(){
  const calls=[],leases=createPreviewDocumentLeases();
  const request=async value=>{
    calls.push(value);
    if(value.operation==='open')return {schemaVersion:2,kind:'ods-pixel-preview-snapshot',status:'snapshot',
      scope:value.scope,leaseId,containerId,...binding,documentGeneration:generation,
      elements:[{ref:'e'.repeat(32),tag:'button',role:'',name:'Reveal'}],limit:128,boundedSnapshot:true,
      truncatedByBytes:false,descriptionsAreUntrusted:true,maximumLifetimeSeconds:120,idleSeconds:45};
    return {schemaVersion:2,kind:'ods-pixel-preview-lease',status:'closed',scope:value.scope,leaseId};
  };
  const admit=(id,args,ctx=owner,decision)=>leases.before({toolName:'pixel_ods_workspace_preview_inspect',params:args},{...ctx,toolCallId:id},decision);
  const tool=createWorkspacePreviewInspectTool({request,leases,context:factory});
  return {calls,leases,request,admit,tool};
}

test('snapshot requires actual admitted call and hides host container/scope',async()=>{
  const {calls,admit,tool}=setup();
  assert.equal((await tool.execute('unknown',snapshot)).isError,true);
  assert.equal(calls.length,0);
  admit('call-1',snapshot);
  const result=await tool.execute('call-1',snapshot);
  assert.equal(result.details.status,'snapshot');
  assert.equal(result.details.containerId,undefined);
  assert.equal(result.details.scope,undefined);
  assert.match(result.content[0].text,/No behavior has been verified/);
  assert.doesNotMatch(result.content[0].text,new RegExp(containerId));
  assert.equal((await tool.execute('call-1',snapshot)).isError,true);
});

test('blocked admission never contacts broker',async()=>{
  const {calls,admit,tool}=setup();admit('call-1',snapshot,owner,{block:true});
  assert.equal((await tool.execute('call-1',snapshot)).isError,true);
  assert.equal(calls.length,0);
});

test('another run cannot close an owned lease',async()=>{
  const {calls,admit,tool}=setup();admit('call-1',snapshot);await tool.execute('call-1',snapshot);
  const close={mode:'close',leaseId,...binding};admit('call-2',close,{...owner,runId:'run-b'});
  assert.equal((await tool.execute('call-2',close)).isError,true);
  assert.equal(calls.length,1);
  admit('call-3',close);assert.equal((await tool.execute('call-3',close)).details.status,'closed');
  assert.equal(calls[1].containerId,containerId);
});

test('owner finish closes only exact run/session',async()=>{
  const {calls,leases,admit,tool}=setup();admit('call-1',snapshot);await tool.execute('call-1',snapshot);
  await leases.finish({runId:'other'},factory);assert.equal(calls.length,1);
  await leases.finish({runId:'run-a'},{...factory,sessionKey:'other'});assert.equal(calls.length,1);
  await leases.finish({runId:'run-a'},factory);assert.equal(calls.length,2);assert.equal(calls[1].operation,'close');
});

test('abort targets only bound active call',async()=>{
  const {calls,leases,admit,tool}=setup();admit('call-1',snapshot);await tool.execute('call-1',snapshot);
  await leases.abortCall('other',factory);assert.equal(calls.length,1);
  await leases.abortCall('call-1',{...factory,sessionId:'other'});assert.equal(calls.length,1);
  await leases.abortCall('call-1',factory);assert.equal(calls.length,2);
});

test('reference plans retain original visibility transition shape and exact site hash',()=>{
  const locator={ref:'e'.repeat(32),documentGeneration:generation};
  const params={mode:'continue',leaseId,...binding,steps:[{action:'assert-hidden',locator},{action:'click',locator},{action:'assert-visible',locator}]};
  const request=normalizeWorkspacePreviewInspectionParams(params);
  assert.equal(request.schemaVersion,1);assert.deepEqual(request.steps,params.steps);
  assert.equal(request.mode,undefined);assert.equal(request.leaseId,undefined);
  assert.throws(()=>normalizeWorkspacePreviewInspectionParams({...params,steps:[{action:'click',locator:{...locator,ref:'invented'}}]}));
});

test('implicit publication binds only exact lease operation shapes',()=>{
  assert.deepEqual(bindDefaultPreviewInspection({mode:'snapshot',viewport:binding.viewport},binding),snapshot);
  assert.equal(bindDefaultPreviewInspection({mode:'snapshot',viewport:binding.viewport,scope:'model'},binding),undefined);
  assert.equal(bindDefaultPreviewInspection({mode:'snapshot',siteId:binding.siteId,viewport:binding.viewport},binding),undefined);
});
