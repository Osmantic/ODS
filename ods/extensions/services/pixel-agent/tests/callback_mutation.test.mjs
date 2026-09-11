import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

const path = 'callback-demo/index.html';
const initial = '<!doctype html><p>Garden</p>';
const edit = {path, oldText:'</p>', newText:'<i>tree</i></p>'};
const changed = count => initial.replace('</p>', '<i>tree</i>'.repeat(count)+'</p>');
const context = (toolName, toolCallId, runId='run') => ({agentId:'pixel',sessionId:'session',runId,toolName,toolCallId});
const envelope = (name, result) => ({details:{tool:{id:`openclaw:core:${name}`,source:'openclaw',sourceName:'core',name},result}});
const success = {content:[{type:'text',text:'Edited successfully'}]};
function before(g,name,id,params,run='run') {
  return g.beforeToolCall({toolName:name,toolCallId:id,params},context(name,id,run));
}
function after(g,name,id,params,result=success,run='run') {
  g.afterToolCall({toolName:name,toolCallId:id,params,result},context(name,id,run));
}
function seed() {
  const g=createToolLoopGuard();
  g.observeRun(context('write','initial'),'pixel',{prompt:'Build and publish the website in /workspace/callback-demo/index.html.'});
  before(g,'write','initial',{path,content:initial});
  after(g,'write','initial',{path,content:initial});
  return g;
}
function publish(g,content,{authored=true}={}) {
  const name=Buffer.from('index.html'),bytes=Buffer.from(content),n=Buffer.alloc(4),b=Buffer.alloc(8);
  n.writeUInt32BE(name.length);b.writeBigUInt64BE(BigInt(bytes.length));
  const sha256=createHash('sha256').update(n).update(name).update(b).update(bytes).digest('hex');
  const siteId='site-'+sha256.slice(0,24),params={relativeDirectory:'callback-demo'};
  before(g,'pixel_ods_workspace_preview','preview',params);
  after(g,'pixel_ods_workspace_preview','preview',params,{details:{schemaVersion:1,kind:'ods-pixel-workspace-preview',status:'succeeded',relativeDirectory:'callback-demo',files:1,bytes:bytes.length,sha256,siteId,port:9437,url:`http://${siteId}.localhost:9437/${siteId}/`,entryFile:'index.html',entrySha256:createHash('sha256').update(bytes).digest('hex'),httpStatus:200,readbackVerified:true,executable:false,overwritten:false}});
  const verification=g.verificationForRun('run');
  assert.match(verification.text,authored?/Created by Pixel\./:/Published from your workspace\./);
  if(!authored)assert.doesNotMatch(verification.text,/Created by Pixel\./);
  return verification.status;
}
function wrapped(g,parent,{inner=true,result=envelope('edit',success)}={}) {
  const requested={id:'edit',args:edit};
  const decision=before(g,'tool_call',parent,requested);
  assert.notEqual(decision?.block,true);
  // The runtime executes the parameters returned by before_tool_call.
  const params=decision?.params??requested;
  if(inner){
    const safe=parent.trim().replace(/[^A-Za-z0-9_.:-]+/g,'_').slice(0,120)||'call';
    const child=`tool_search_code:${safe}:edit:1`;
    const selected=before(g,'edit',child,params.args);
    assert.notEqual(selected?.block,true);
    after(g,'edit',child,selected?.params??params.args);
  }
  after(g,'tool_call',parent,params,result);
}

for(const parent of ['outer-1','outer | call','x'.repeat(135)]){
  test('paired callbacks apply one physical edit: '+parent,()=>{
    const g=seed();wrapped(g,parent);assert.equal(publish(g,changed(1)),'passed');
  });
}
test('separate physical edits with identical arguments both apply',()=>{
  const g=seed();wrapped(g,'first');wrapped(g,'second');assert.equal(publish(g,changed(2)),'passed');
});
test('standalone wrapper callback still applies its edit',()=>{
  const g=seed();wrapped(g,'outer',{inner:false});assert.equal(publish(g,changed(1)),'passed');
});
for(const variant of ['direct','wrong-run','wrong-params','wrong-child-id','ambiguous-parent']){
  test('independent inner edit is not suppressed: '+variant,()=>{
    const g=seed();let id='direct';
    if(variant!=='direct'){
      const parent=variant==='ambiguous-parent'?'p?x':'outer';
      before(g,'tool_call',parent,{id:'edit',args:variant==='wrong-params'?{...edit,newText:'different'}:edit},variant==='wrong-run'?'other':'run');
      if(variant==='ambiguous-parent')before(g,'tool_call','p x',{id:'edit',args:edit});
      id=variant==='wrong-child-id'?'tool_search_code:outer:edit:invalid':`tool_search_code:${variant==='ambiguous-parent'?'p_x':'outer'}:edit:1`;
    }
    const decision=before(g,'edit',id,edit);
    assert.notEqual(decision?.block,true);
    after(g,'edit',id,decision?.params??edit);assert.equal(publish(g,changed(1)),'passed');
  });
}
for(const malformed of [{isError:true,content:[{type:'text',text:'Wrapper failed'}]},envelope('write',success)]){
  test('unverified outer result cannot prove authorship of a host-verified snapshot: '+JSON.stringify(malformed),()=>{
    const g=seed();wrapped(g,'outer',{result:malformed});assert.equal(publish(g,changed(1),{authored:false}),'passed');
  });
}
