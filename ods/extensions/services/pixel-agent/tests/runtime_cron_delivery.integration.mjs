// Run against the installed pinned SDK, without creating a real cron job.
// OPENCLAW_CRON_NORMALIZE_MODULE identifies its dist/normalize-*.js module.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, mkdtempSync, mkdirSync, writeFileSync, rmSync, realpathSync, lstatSync } from "node:fs";
import { dirname, resolve, join } from "node:path";
import {tmpdir} from 'node:os';
import {createHash} from 'node:crypto';
import {registerHooks} from 'node:module';
import { pathToFileURL } from "node:url";
import { withPixelCronDeliveryDefault, withPixelCronJobAdmission } from "../plugin/cron-delivery-default.mjs";

const modulePath = process.env.OPENCLAW_CRON_NORMALIZE_MODULE ??
  (process.env.OPENCLAW_PACKAGE_DIR && join(process.env.OPENCLAW_PACKAGE_DIR,'dist/normalize-BzS-Bdfc.js'));
assert.ok(modulePath, "OPENCLAW_CRON_NORMALIZE_MODULE is required");
const pkg = JSON.parse(readFileSync(resolve(dirname(modulePath), "../package.json"), "utf8"));
assert.equal(pkg.version, "2026.6.33", "Requalify when the pinned SDK changes");
const toolsPath=resolve(dirname(modulePath),'openclaw-tools-iHHy99PD.js');
const toolsSource=readFileSync(toolsPath,'utf8');
const readRange=JSON.parse(readFileSync(new URL('../host/openclaw-read-range.json',import.meta.url)));
const digest=createHash('sha256').update(toolsSource).digest('hex');
assert.ok([readRange.sourceSha256,readRange.patchedSha256,...Object.keys(readRange.previousReplacements??{})].includes(digest),
  'only exact reviewed pinned native tool bytes');
// Expose the ACTUAL unmodified factory for the fixture, replacing only its
// gateway boundary with a counter. No cron service or real job is created.
const privateParent=realpathSync(tmpdir()),privateRoot=mkdtempSync(join(privateParent,'ods-cron-policy-'));
const privateIdentity=lstatSync(privateRoot,{bigint:true});
let loader,closeStateDatabases,normalize,createCronTool,validateCronAddParams,cleaned=false;
function cleanup(){
  if(cleaned)return;
  try{closeStateDatabases?.();}finally{
    try{loader?.deregister();}finally{
      // Refuse any replaced/linked root or computed target outside the exact
      // canonical temporary parent before recursive cleanup, including Windows.
      const current=lstatSync(privateRoot,{bigint:true});
      assert.equal(dirname(privateRoot),privateParent);
      assert.equal(resolve(realpathSync(privateRoot)),resolve(privateRoot));
      assert.equal(current.isDirectory() && !current.isSymbolicLink(),true);
      assert.equal(current.dev,privateIdentity.dev);assert.equal(current.ino,privateIdentity.ino);
      rmSync(privateRoot,{recursive:true});cleaned=true;
      assert.equal(createHash('sha256').update(readFileSync(toolsPath)).digest('hex'),digest,
        'the source SDK tool module remains unchanged after fixture cleanup');
    }
  }
}
test.after(cleanup); // Register before any native import or private setup can fail.
try{
  mkdirSync(join(privateRoot,'state'));mkdirSync(join(privateRoot,'home'));mkdirSync(join(privateRoot,'config'));
  process.env.HOME=join(privateRoot,'home');process.env.USERPROFILE=process.env.HOME;
  process.env.XDG_CONFIG_HOME=join(privateRoot,'config');
  process.env.OPENCLAW_STATE_DIR=join(privateRoot,'state');
  process.env.OPENCLAW_CONFIG_PATH=join(privateRoot,'config.json');
  writeFileSync(process.env.OPENCLAW_CONFIG_PATH,JSON.stringify({agents:{list:[{id:'pixel',default:true}]}}));
  ({n:closeStateDatabases}=await import(pathToFileURL(resolve(dirname(modulePath),'openclaw-state-db-BtpXMqJX.js')).href));
  const sdk=await import(pathToFileURL(modulePath).href);
  normalize=Object.values(sdk).find(value=>typeof value==='function' && value.name==='normalizeCronJobCreate');
  assert.equal(typeof normalize,'function');
  const toolsUrl=pathToFileURL(toolsPath).href;
  loader=registerHooks({load(url,context,next){return url===toolsUrl
    ?{format:'module',source:toolsSource+'\nexport {createCronTool};',shortCircuit:true}:next(url,context);}});
  ({createCronTool}=await import(toolsUrl));
  ({I:validateCronAddParams}=await import(pathToFileURL(resolve(dirname(modulePath),'src-CSp7rUPX.js')).href));
  assert.equal(typeof createCronTool,'function');assert.equal(typeof validateCronAddParams,'function');
}catch(error){cleanup();throw error;}
const job = {
  name: "delivery-default-qualification",
  schedule: { kind: "at", at: "2035-01-01T00:00:00Z" },
  sessionTarget: "isolated",
  payload: { kind: "agentTurn", message: "Write a timestamp to the workspace." },
};

test("real SDK reproduces announce default and accepts Pixel's replacement", () => {
  assert.equal(normalize(structuredClone(job)).delivery.mode, "announce");
  const params = { action: "add", job: structuredClone(job) };
  const result = withPixelCronDeliveryDefault(undefined, { params },
    { agentId: "pixel", toolName: "openclaw:core:cron" }, "pixel");
  assert.equal(normalize(result.params.job).delivery.mode, "none");
  assert.deepEqual(params.job, job);
});

test("private one-off agent tasks retain their run history after native normalization", () => {
  const scoped = { ...structuredClone(job), delivery: { mode: "none" },
    payload: { ...job.payload, toolsAllow: ["pixel_ods_notes"] } };
  const params = { action: "add", job: scoped };
  const ctx = { agentId: "pixel", toolName: "cron" };
  assert.equal(normalize(structuredClone(scoped)).deleteAfterRun, true);
  const admitted = withPixelCronJobAdmission(undefined, { params }, ctx, "pixel");
  assert.notEqual(admitted?.block, true);
  const effective = withPixelCronDeliveryDefault(admitted, { params }, ctx, "pixel")?.params ?? params;
  assert.equal(normalize(effective.job).deleteAfterRun, false);
  assert.equal(Object.hasOwn(scoped, "deleteAfterRun"), false);
});

test("Tool Search and selected-tool hooks preserve the explicit default", () => {
  const params = { id: "openclaw:core:cron", args: { action: "add", job: structuredClone(job) } };
  const outer = withPixelCronDeliveryDefault(undefined, { params },
    { agentId: "pixel", toolName: "tool_call" }, "pixel");
  const inner = { params: outer.params.args };
  assert.equal(withPixelCronDeliveryDefault(inner, { params: inner.params },
    { agentId: "pixel", toolName: "cron" }, "pixel"), inner);
  assert.equal(normalize(inner.params.job).delivery.mode, "none");
});

test("explicit external delivery survives both adapter and SDK normalization", () => {
  const params = { action: "add", job: { ...structuredClone(job),
    delivery: { mode: "announce", channel: "slack", to: "qualification-only" } } };
  assert.equal(withPixelCronDeliveryDefault(undefined, { params },
    { agentId: "pixel", toolName: "cron" }, "pixel"), undefined);
  const actual = normalize(params.job).delivery;
  assert.equal(actual.mode, "announce");
  assert.equal(actual.channel, "slack");
  assert.equal(actual.to, "qualification-only");
});

test("real SDK defaults current and explicit-session background jobs too", () => {
  for (const sessionTarget of ["current", "session:agent:pixel:qualification-only"]) {
    const params = { action: "add", job: { ...structuredClone(job), sessionTarget } };
    assert.equal(normalize(structuredClone(params.job)).delivery.mode, "announce");
    const result = withPixelCronDeliveryDefault(undefined, { params },
      { agentId: "pixel", toolName: "cron" }, "pixel");
    assert.equal(normalize(result.params.job).delivery.mode, "none");
    assert.equal(result.params.job.sessionTarget, sessionTarget);
  }
});

test('actual pinned cron factory preserves empty future tools and intersects explicit tools with its creator cap',async()=>{
  for(const requested of [[],['read','pixel_gmail_read']])for(const nested of [false,true])for(const wrapped of [false,true]) {
    const declared={...structuredClone(job),payload:{...job.payload,toolsAllow:requested},delivery:{mode:'none'}};
    const args={action:'add',...(nested?{job:declared}:declared)};
    const params=wrapped?{id:'openclaw:core:cron',args}:args;
    const context={agentId:'pixel',toolName:wrapped?'tool_call':'cron'};
    assert.equal(withPixelCronJobAdmission(undefined,{params},context,'pixel'),undefined);
    const calls=[];
    const tool=createCronTool({creatorToolAllowlist:[{name:'read'},{name:'exec'}]},
      {callGatewayTool:async(method,_opts,value)=>{calls.push({method,value});return {id:'fixture-only',...value};}});
    await tool.execute('native-policy-fixture',structuredClone(args));
    assert.equal(calls.length,1);assert.equal(calls[0].method,'cron.add');
    assert.deepEqual(calls[0].value.payload.toolsAllow,requested.length?['read']:[]);
    assert.equal(calls[0].value.delivery.mode,'none');
    assert.equal(validateCronAddParams(calls[0].value),true,JSON.stringify(validateCronAddParams.errors));
  }
});

test('the actual native repairable shorthand cannot bypass the new explicit-policy admission',async()=>{
  const samples=[{action:'add',name:'flat-missing',schedule:job.schedule,message:'Calculate 14 + 8.'},
    {action:'add',job:null,name:'null-job',schedule:job.schedule,message:'Calculate 14 + 8.'},
    {action:'add',job:{...job,delivery:{mode:'announce'}}},
    {action:'add',job:{...job,payload:{...job.payload,toolsAllow:[]},delivery:{mode:'announce'}}}];
  for(const params of samples) {
    const captured=[];const native=createCronTool({creatorToolAllowlist:[{name:'read'},{name:'exec'}]},
      {callGatewayTool:async(method,_opts,value)=>{captured.push(value);return {id:'fixture-only',...value};}});
    // This private RPC counter reproduces the actual native defaults, without
    // scheduling a job. The production admission stops these same inputs first.
    await native.execute('original-default-fixture',structuredClone(params));
    assert.equal(captured.length,1);assert.equal(captured[0].delivery.mode,'announce');
    const denied=withPixelCronJobAdmission(undefined,{params},{agentId:'pixel',toolName:'cron'},'pixel');
    assert.equal(denied.block,true);assert.match(denied.blockReason,/No scheduled task was created/);
    assert.equal(validateCronAddParams(captured[0]),true,JSON.stringify(validateCronAddParams.errors));
  }
});

test('explicit routed external delivery and native system events retain native schema validation',async()=>{
  const implicitLast={action:'add',job:{...structuredClone(job),payload:{...job.payload,toolsAllow:[]},
    delivery:{mode:'announce',channel:' LaSt ',to:'QA_ONLY'}}};
  assert.equal(normalize(implicitLast.job).delivery.channel,'last');
  assert.equal(withPixelCronJobAdmission(undefined,{params:implicitLast},{agentId:'pixel',toolName:'cron'},'pixel').block,true);
  for(const delivery of [{mode:'announce',channel:'slack',to:'QA_ONLY'},
    {mode:'webhook',to:'https://example.invalid/qa'}]) {
    const params={action:'add',job:{...structuredClone(job),payload:{...job.payload,toolsAllow:[]},delivery}};
    assert.equal(withPixelCronJobAdmission(undefined,{params},{agentId:'pixel',toolName:'cron'},'pixel'),undefined);
    const result=normalize(params.job);assert.equal(validateCronAddParams(result),true,JSON.stringify(validateCronAddParams.errors));
    assert.deepEqual(result.delivery,delivery);
  }
  const eventJob={name:'native-event',schedule:job.schedule,sessionTarget:'main',payload:{kind:'systemEvent',text:'QA reminder'}};
  assert.equal(withPixelCronJobAdmission(undefined,{params:{action:'add',job:eventJob}},
    {agentId:'pixel',toolName:'cron'},'pixel'),undefined);
  assert.equal(validateCronAddParams(normalize(eventJob)),true,JSON.stringify(validateCronAddParams.errors));
});

test('other payload types keep the actual native recovery and main-session behavior',async()=>{
  const eventJob={name:'native-event',schedule:job.schedule,sessionTarget:'main',payload:{kind:'systemEvent',text:'QA reminder'}};
  const samples=[{action:'add',job:eventJob},{action:'add',...eventJob},
    {action:'add',name:'flat-text',schedule:job.schedule,text:'QA reminder'},
    {action:'add',job:null,name:'flat-text-null',schedule:job.schedule,text:'QA reminder'},
    {action:'add',job:{...eventJob,payload:{text:'QA reminder'}}},
    {action:'add',job:{name:'alias',schedule:job.schedule,namePayload:{kind:'systemEvent',text:'QA reminder'}}},
    {action:'add',job:{data:eventJob}},
    {action:'add',job:{name:'padded',schedule:job.schedule,'payload ':{kind:'systemEvent',text:'QA reminder'}}}];
  for(const params of samples) {
    const guard={params};assert.equal(withPixelCronJobAdmission(guard,{params},{agentId:'pixel',toolName:'cron'},'pixel'),guard);
    const captured=[];const native=createCronTool({creatorToolAllowlist:[{name:'read'}]},
      {callGatewayTool:async(_method,_opts,value)=>{captured.push(value);return {id:'fixture-only',...value};}});
    await native.execute('native-system-event-fixture',structuredClone(params));
    assert.equal(captured.length,1);assert.equal(captured[0].payload.kind,'systemEvent');
    assert.equal(captured[0].sessionTarget,'main');
    assert.equal(validateCronAddParams(captured[0]),true,JSON.stringify(validateCronAddParams.errors));
  }
});
