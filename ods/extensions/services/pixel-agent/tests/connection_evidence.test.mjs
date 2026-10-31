import test from 'node:test';
import assert from 'node:assert/strict';
import {createConnectionEvidence,GOOGLE_LOOKUP_UNVERIFIED} from '../plugin/connection-evidence.mjs';
import {createConnectionsStatusTool} from '../plugin/connections-status.mjs';
import {countEmail} from '../../../../vendor/pixel/plugin/email-query.js';
import {countCalendar} from '../../../../vendor/pixel/plugin/calendar-query.js';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {register} from 'node:module';
import {tmpdir} from 'node:os';
import path from 'node:path';

const STATUS='pixel_ods_connections_status', GMAIL='pixel_gmail_count', CALENDAR='pixel_calendar_count';
const instant='2026-10-29T20:00:00.000Z', now=Date.parse(instant);
const status=()=>({schemaVersion:1,kind:'ods-connection-agent-status',scope:'reviewed-google',
  observedAt:instant,available:true,reason:null,recordConfigured:true,localSavedSignIn:true,
  connectionState:'connected',ownerEnabled:{gmailRead:true,calendarRead:true},
  providerPermissionObserved:{gmailRead:true,calendarRead:true},sourceCheckedAt:instant,
  reads:{email:{lastObservedState:'ok',recent:true,lastSuccessAt:instant,complete:false,error:null},
    calendar:{lastObservedState:'ok',recent:true,lastSuccessAt:instant,complete:true,error:null}},providerCheckedNow:false});
const reply=value=>({content:[{type:'text',text:JSON.stringify(value)}],details:value});
const binding=(name,callId='call-1',extra={})=>({runId:'run-1',sessionId:'session-1',sessionKey:'key-1',
  callId,toolName:name,params:{},...extra});
const count=(source='gmail')=>source==='gmail'
  ? countEmail({enabled:true,records:[],generatedAt:instant,coverage:{sync:{state:'in-progress',retainedRecords:0}}},undefined,{state:'ok'},now,180000)
  : countCalendar({enabled:true,records:[],generatedAt:instant,coverage:{calendarTimeZone:'UTC',calendarTimeZoneState:'consistent',sync:{state:'in-progress',retainedRecords:0}}},
    {window:{kind:'rolling',days:7}},{state:'ok'},now,180000,'UTC');
const wrap=(name,result,sourceName=name===STATUS?'pixel-ods':'pixel-source-broker')=>({details:{tool:{
  id:`openclaw:${sourceName}:${name}`,source:'openclaw',sourceName,name},result}});
const observed=(e,name,value,id='call-1')=>{const b=binding(name,id);e.prepare(b);e.observe(b,reply(value));};

test('discovery is not account evidence; unknown cannot become absence through an empty catalog',async()=>{
  const e=createConnectionEvidence();
  observed(e,'pixel_ods_extensions',{totalMatches:0});
  assert.equal(e.unresolved(),false,'unrelated turns have no new obligation');
  const b=binding(STATUS);e.prepare(b);
  const value=status();Object.assign(value,{recordConfigured:null,localSavedSignIn:null,connectionState:'unknown'});
  e.observe(b,await createConnectionsStatusTool({read:async()=>value}).execute('status',{}));
  observed(e,'pixel_ods_extensions',{totalMatches:0},'catalog');
  observed(e,'pixel_ods_extension_request_status',{ok:false},'request');
  assert.equal(e.unresolved(),true);
  const absent=status();Object.assign(absent,{recordConfigured:false,localSavedSignIn:false,connectionState:'not-connected'});
  observed(e,STATUS,absent,'known-absence');
  assert.equal(e.unresolved(),false,'strict known absence is a valid observation');
  assert.equal(GOOGLE_LOOKUP_UNVERIFIED,'Could not verify the requested Google lookup.');
});

test('cached Source success cannot settle unknown saved-account status',()=>{
  const e=createConnectionEvidence();e.prepare(binding(STATUS));
  observed(e,GMAIL,count(),'source-ok');
  assert.equal(e.unresolved(),true);
  observed(e,STATUS,status(),'status-ok');
  assert.equal(e.unresolved(),false);
});

test('a failed source lookup is repaired only by a strict same-source receipt',()=>{
  const e=createConnectionEvidence();const b=binding(GMAIL);e.prepare(b);e.observe(b,{isError:true});
  observed(e,CALENDAR,count('calendar'),'calendar-ok');
  assert.equal(e.unresolved(),true);
  let sequence=0;
  for(const alter of [v=>({...v,indexedMatchCount:'0'}),v=>({...v,privateField:'untrusted'}),
    v=>({...v,readState:'unavailable'}),v=>({...v,generatedAt:'2026-02-30T20:00:00Z'}),
    v=>({...v,absenceWithinCoveredProjection:true})]) {
    observed(e,GMAIL,alter(count()),`bad-${++sequence}`);
    assert.equal(e.unresolved(),true);
  }
  observed(e,GMAIL,count(),'gmail-repaired');
  assert.equal(e.unresolved(),false);
});

test('qualified dispatch requires the exact registered target and envelope source',()=>{
  for(const name of [STATUS,GMAIL,CALENDAR]) {
    const e=createConnectionEvidence(),source=name===STATUS?'pixel-ods':'pixel-source-broker';
    const b=binding('tool_call','outer',{params:{id:`openclaw:${source}:${name}`,args:{}}});e.prepare(b);
    e.observe(b,wrap(name,reply(name===STATUS?status():count(name===GMAIL?'gmail':'calendar')),'other-plugin'));
    assert.equal(e.unresolved(),true);
    const wrong=binding('tool_call','foreign',{params:{id:`openclaw:other-plugin:${name}`,args:{}}});
    e.prepare(wrong);e.observe(wrong,wrap(name,reply(status())));
    assert.equal(e.unresolved(),true,'same suffix is not the trusted source');
    const good={...b,callId:'correct'};e.prepare(good);e.observe(good,wrap(name,reply(name===STATUS?status():count(name===GMAIL?'gmail':'calendar'))));
    assert.equal(e.unresolved(),false);
  }
});

test('foreign run, session, call and changed target cannot settle a pending observation',()=>{
  for(const extra of [{runId:'other-run'},{sessionId:'other-session'},{sessionKey:'other-key'},
    {callId:'other-call'},{toolName:'pixel_ods_extensions'}]) {
    const e=createConnectionEvidence(),b=binding(STATUS);e.prepare(b);
    e.observe({...b,...extra},reply(status()));assert.equal(e.unresolved(),true);
    e.observe(b,reply(status()));assert.equal(e.unresolved(),false);
  }
});

test('lost, replayed and older parallel results cannot erase the newest unknown',()=>{
  const e=createConnectionEvidence(),old=binding(STATUS,'old'),fresh=binding(STATUS,'fresh');
  e.prepare(old);e.prepare(fresh);e.observe(fresh,{isError:true});
  e.observe(old,reply(status()));e.observe(fresh,reply(status()));
  assert.equal(e.unresolved(),true);
  observed(e,STATUS,status(),'retry');assert.equal(e.unresolved(),false);
  const newRun=createConnectionEvidence();assert.equal(newRun.unresolved(),false,'no facts carry into another run');
});

test('bounded pending receipts fail closed under eviction without retaining contents',()=>{
  const e=createConnectionEvidence(),old=binding(STATUS,'evicted');e.prepare(old);
  for(let n=0;n<130;n++)e.prepare(binding(STATUS,`bounded-${n}`));
  e.observe(old,reply(status()));assert.equal(e.unresolved(),true);
  e.observe(binding(STATUS,'bounded-129'),reply(status()));assert.equal(e.unresolved(),false);
  assert.deepEqual(Object.keys(e).sort(),['observe','prepare','unresolved']);
});

test('malformed, raw-owner and reflected error payloads never become status proof',()=>{
  for(const result of [reply({googleAuth:{clientId:'private'},state:'connected'}),{details:status(),isError:true},
    {content:[{type:'text',text:'x'.repeat(262145)}]},{content:[{type:'text',text:'{'}]},
    reply({...status(),providerCheckedNow:true})]) {
    const e=createConnectionEvidence(),b=binding(STATUS);e.prepare(b);e.observe(b,result);
    assert.equal(e.unresolved(),true);
  }
});

test('actual Source factory count/list/read receipts settle only their source lookup',
  {skip:!process.env.PIXEL_TEST_TYPEBOX_DIR},async t=>{
  register('../../../../vendor/pixel/tests/fixtures/source-plugin/loader.mjs',import.meta.url);
  const directory=await mkdtemp(path.join(tmpdir(),'connection-evidence-source-'));
  t.after(()=>rm(directory,{recursive:true,force:true}));
  const at=Date.now(), date=offset=>new Date(at+offset*86400000).toISOString();
  const boundary={rawContentStored:false,instructionsAuthorized:false,projectionOnly:true};
  const email={schemaVersion:1,source:'gmail',enabled:true,sanitizerRevision:3,generatedAt:date(0),boundary,
    records:[{id:'fixture-email',threadId:'fixture-thread',subject:'Invented fixture',from:'example.invalid',
      to:'example.invalid',cc:'',summary:'Invented summary',mailboxes:['inbox'],labelIds:['INBOX']}],
    coverage:{bounded:true,sync:{state:'in-progress',retainedRecords:0},folders:{}}};
  const calendar={schemaVersion:1,source:'google-calendar',enabled:true,generatedAt:date(0),boundary,
    records:[{id:'fixture-event',title:'Invented event',start:{dateTime:date(1)},end:{dateTime:date(1.1)}}],
    coverage:{bounded:true,windowStart:date(-1),windowEnd:date(10),completeWithinWindow:true,truncated:false,
      calendarTimeZone:'UTC',calendarTimeZoneState:'consistent',sync:{state:'complete',retainedRecords:0,failure:null}}};
  const sync={kind:'pixel-source-broker-status',sources:{email:{state:'ok'},calendar:{state:'ok'}}};
  for(const [name,value] of Object.entries({email,calendar,status:sync}))
    await writeFile(path.join(directory,`${name}.json`),JSON.stringify(value));
  const env={PIXEL_SOURCE_PROJECTION_DIR:directory,PIXEL_AGENT_ID:'pixel',PIXEL_LIMB_EMAIL_ENABLED:'1',
    PIXEL_LIMB_CALENDAR_ENABLED:'1',PIXEL_LIMB_CALENDAR_WRITE_ENABLED:'0',PIXEL_LIMB_WEB_ENABLED:'0',
    PIXEL_LIMB_SOCIAL_ENABLED:'0',PIXEL_LIMB_EMAIL_DRAFTS_ENABLED:'0',PIXEL_SOURCE_STALE_AFTER_MS:'180000'};
  const saved=Object.fromEntries(Object.keys(env).map(key=>[key,process.env[key]]));
  const tools=new Map();
  try {
    Object.assign(process.env,env);
    const {default:entry}=await import('../../../../vendor/pixel/plugin/index.js?connection-evidence');
    entry.register({config:{agents:{defaults:{userTimezone:'UTC'}}},registerTool(factory,{names}){
      const tool=factory({agentId:'pixel'});if(tool)tools.set(names[0],tool);
    }});
  } finally {
    for(const [key,value] of Object.entries(saved))if(value===undefined)delete process.env[key];else process.env[key]=value;
  }
  const cases=[
    [GMAIL,{}],['pixel_gmail_inbox',{maxResults:1}],['pixel_gmail_read',{messageId:'fixture-email'}],
    ['pixel_gmail_sent',{maxResults:1}],['pixel_gmail_search',{query:'fixture',maxResults:1}],
    ['pixel_gmail_thread',{threadId:'fixture-thread',maxResults:1}],
    [CALENDAR,{window:{kind:'rolling',days:7}}],
    ['pixel_calendar_list',{timeMin:date(2),timeMax:date(3),maxResults:1}],
    ['pixel_calendar_get',{eventId:'fixture-event'}],
  ];
  for(const [name,args] of cases)for(const wrapped of [false,true]) {
    const result=await tools.get(name).execute('actual-source',args);
    assert.notEqual(result.isError,true,name);
    if(name===GMAIL)assert.equal(result.details.coverageState,'partial-index');
    if(name==='pixel_calendar_list')assert.equal(result.details.count,0,'empty covered lookup is valid evidence');
    const e=createConnectionEvidence(),b=binding(wrapped?'tool_call':name,`${name}-${wrapped}`,
      wrapped?{params:{id:`openclaw:pixel-source-broker:${name}`,args}}:{});
    e.prepare(b);e.observe(b,wrapped?wrap(name,result):result);
    assert.equal(e.unresolved(),false,`${name} real factory ${wrapped?'wrapped':'direct'}`);
  }
  const invalid=await tools.get(GMAIL).execute('bad-source',{max:1});
  assert.equal(invalid.isError,true);
  const e=createConnectionEvidence(),b=binding(GMAIL);e.prepare(b);e.observe(b,invalid);
  assert.equal(e.unresolved(),true,'schema rejection cannot establish an empty account');
});
