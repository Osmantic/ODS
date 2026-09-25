import test from 'node:test';
import assert from 'node:assert/strict';
import {boundedPreviewDelivery, previewRecoveryAllowed} from '../plugin/preview-delivery-recovery.mjs';

const tool = 'pixel_ods_workspace_preview';
const config = () => ({agents:{defaults:{sandbox:{mode:'all'}},list:[{id:'pixel',tools:{deny:['message','pixel_web_browse']}}]},
  tools:{profile:'coding',alsoAllow:[tool],sandbox:{tools:{allow:[tool,'write']}}}});
test('standard ODS preview policy permits recovery; custom restrictions fail closed', () => {
  assert.equal(previewRecoveryAllowed(config()),true);
  for (const change of [c=>c.tools.deny=[tool], c=>c.tools.deny=['pixel_*'],
    c=>c.tools.deny=['group:plugins'], c=>c.tools.profile='minimal',
    c=>c.tools.byProvider={}, c=>c.agents.list[0].tools.allow=['read'],
    c=>c.tools.sandbox.tools.allow=['write'], c=>delete c.tools.sandbox, c=>c.agents.list=[]]) {
    const c=config(); change(c); assert.equal(previewRecoveryAllowed(c),false);
  }
  assert.equal(previewRecoveryAllowed({}),false);
});
test('delivery is bounded even when transport ignores abort',async()=>{
  let signal;
  assert.equal(await boundedPreviewDelivery((_p,options)=>{
    signal=options.signal;return new Promise(()=>{});
  },{},()=>true,{timeoutMs:10}),undefined);
  assert.equal(signal.aborted,true);
});
test('cancellation interrupts receipt wait and rejects late success',async()=>{
  let active=true,signal,resolve;
  const pending=boundedPreviewDelivery((_p,options)=>{
    signal=options.signal;active=false;return new Promise(r=>resolve=r);
  },{},()=>active,{timeoutMs:1000});
  assert.equal(await pending,undefined);
  assert.equal(signal.aborted,true);resolve({details:{status:'succeeded'}});
});
test('transport failures and cancellation before dispatch never produce receipts',async()=>{
  let calls=0;
  assert.equal(await boundedPreviewDelivery(()=>{calls++;throw Error('unavailable');},{},()=>false),undefined);
  assert.equal(calls,0);
  assert.equal(await boundedPreviewDelivery(()=>{throw Error('unavailable');},{},()=>true),undefined);
});
