import assert from 'node:assert/strict';
import test from 'node:test';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {register,registerHooks} from 'node:module';
import {listCalendar,countCalendar,calendarListParameters,resolveCountWindow} from '../plugin/calendar-query.js';
register('./fixtures/source-plugin/loader.mjs',import.meta.url);
const {Check}=await import('typebox/value');
const NOW=Date.parse('2026-11-03T22:00:00Z'),TTL=180000,ZONE='America/Sao_Paulo',sync={state:'ok'};
const event=(id,start,end)=>({id,title:'Synthetic '+id,location:'',notesSummary:'',start,end});
const fixture=()=>({schemaVersion:1,enabled:true,generatedAt:new Date(NOW-1000).toISOString(),boundary:{projectionOnly:true},records:[
 event('midday',{dateTime:'2026-11-04T15:00:00Z'},{dateTime:'2026-11-04T16:00:00Z'}),
 event('late-owner-day',{dateTime:'2026-11-05T01:00:00Z'},{dateTime:'2026-11-05T02:00:00Z'}),
 event('all-day',{date:'2026-11-04'},{date:'2026-11-05'}),
 event('previous-owner-day',{dateTime:'2026-11-04T02:00:00Z'},{dateTime:'2026-11-04T02:30:00Z'}),
 event('exclusive-end',{dateTime:'2026-11-05T03:00:00Z'},{dateTime:'2026-11-05T04:00:00Z'}),
 event('ends-at-start',{dateTime:'2026-11-04T02:00:00Z'},{dateTime:'2026-11-04T03:00:00Z'})],
 coverage:{bounded:true,windowStart:'2026-11-01T00:00:00Z',windowEnd:'2026-11-10T00:00:00Z',completeWithinWindow:true,truncated:false,
 calendarTimeZone:ZONE,calendarTimeZoneState:'consistent',sync:{state:'complete',retainedRecords:0,failure:null}}});
const civil={window:{kind:'civil-days',date:'2026-11-04',days:1}};
const compare=(data,params=civil,owner=ZONE,now=NOW,status=sync)=>{
 const count=countCalendar(data,params,status,now,TTL,owner),list=listCalendar(data,params,100,status,now,TTL,owner);
 for(const key of Object.keys(count).filter(k=>k!=='recordsReturned'))assert.deepEqual(list[key],count[key],key);
 assert.equal(list.count,list.events.length);assert.equal(count.recordsReturned,0);
 return {count,list};
};
test('list/count share explicit owner civil day, all-day timezone and exclusive boundaries',()=>{
 const {count,list}=compare(fixture());assert.equal(count.completeMatchCount,3);
 assert.equal(list.windowStart,'2026-11-04T03:00:00.000Z');assert.equal(list.windowEnd,'2026-11-05T03:00:00.000Z');
 assert.deepEqual(list.events.map(e=>e.id),['midday','late-owner-day','all-day']);
 const limited=listCalendar(fixture(),civil,1,sync,NOW,TTL,ZONE);
 assert.equal(limited.count,1);assert.equal(limited.recordsReturned,1);assert.equal(limited.totalMatches,3);
 assert.equal(limited.completeMatchCount,3);assert.equal(limited.hasMore,true);
});
test('same absolute interval agrees across list/count, including DST and all-day calendar zone',()=>{
 for(const [date,hours] of [['2026-03-08',23],['2026-11-01',25]]){
  const zone='America/New_York',now=Date.parse(date+'T12:00:00Z'),w=resolveCountWindow({kind:'civil-days',date,days:1},now,zone);
  assert.equal(w.to-w.from,hours*3600000);
  const data=fixture();data.generatedAt=new Date(now-1000).toISOString();data.coverage={...data.coverage,windowStart:'2026-01-01T00:00:00Z',windowEnd:'2027-01-01T00:00:00Z',calendarTimeZone:zone};
  const endDate=new Date(Date.parse(date+'T00:00:00Z')+86400000).toISOString().slice(0,10);
  data.records=[event('all-day',{date},{date:endDate}),event('at-end',{dateTime:w.windowEnd},{dateTime:new Date(w.to+1000).toISOString()})];
  const params={window:{kind:'absolute',timeMin:w.windowStart,timeMax:w.windowEnd}};
  const {count}=compare(data,params,zone,now);assert.equal(count.completeMatchCount,1);
  const legacy=listCalendar(data,{timeMin:w.windowStart,timeMax:w.windowEnd},100,sync,now,TTL,zone);
  assert.equal(legacy.completeMatchCount,count.completeMatchCount);assert.deepEqual(legacy.events.map(e=>e.id),['all-day']);
 }
});
test('unresolved events, missing/ambiguous zones and partial/stale coverage never prove complete zero',()=>{
 const cases=[d=>{d.records.push(event('floating',{dateTime:'2026-11-04T10:00:00'},{dateTime:'2026-11-04T11:00:00'}));},
  d=>{delete d.coverage.calendarTimeZone;},d=>{d.coverage.calendarTimeZoneState='inconsistent';},
  d=>{d.coverage.completeWithinWindow=false;},d=>{d.generatedAt=new Date(NOW-TTL-1).toISOString();}];
 for(const mutate of cases){const data=fixture();mutate(data);const {count,list}=compare(data);
  assert.equal(count.completeMatchCount,null);assert.equal(list.completeWithinWindow,false);}
 const {list}=compare(fixture(),civil,null);assert.equal(list.windowState,'unavailable');assert.equal(list.totalMatches,null);
 assert.equal(list.completeMatchCount,null);assert.equal(list.count,0);
});
test('legacy internal absolute adapter refuses mixed, partial, unknown and invalid arguments',()=>{
 const absolute={timeMin:'2026-11-04T03:00:00Z',timeMax:'2026-11-05T03:00:00Z'};
 assert.deepEqual(calendarListParameters(absolute),{window:{kind:'absolute',...absolute}});
 for(const args of [{...absolute,...civil},{timeMin:absolute.timeMin},{timeMax:absolute.timeMax},{...absolute,unknown:true},
  {...absolute,timeMin:'2026-02-30T00:00:00Z'},{...absolute,timeMax:absolute.timeMin}])assert.throws(()=>calendarListParameters(args));
});
let sequence=0;
async function native(t,files={},spy){
 const dir=await mkdtemp(join(tmpdir(),'source-calendar-read-'));t.after(()=>rm(dir,{recursive:true,force:true}));
 for(const [name,value]of Object.entries(files))await writeFile(join(dir,name+'.json'),typeof value==='string'?value:JSON.stringify(value));
 const flags={PIXEL_SOURCE_PROJECTION_DIR:dir,PIXEL_LIMB_EMAIL_ENABLED:'1',PIXEL_LIMB_CALENDAR_ENABLED:'1',PIXEL_LIMB_WEB_ENABLED:'0',PIXEL_LIMB_CALENDAR_WRITE_ENABLED:'0',PIXEL_LIMB_EMAIL_DRAFTS_ENABLED:'0'};
 const prior=Object.fromEntries(Object.keys(flags).map(k=>[k,process.env[k]]));Object.assign(process.env,flags);
 let hook;
 if(spy){globalThis[Symbol.for('ods.qa.calendar.projection-reads')]=spy;
  const body=`import {open,readFile as actual} from 'node:fs/promises';export {open};export function readFile(...args){globalThis[Symbol.for('ods.qa.calendar.projection-reads')].push(String(args[0]));return actual(...args);}`;
  hook=registerHooks({resolve(s,c,next){return s==='node:fs/promises'&&c.parentURL?.startsWith(new URL('../plugin/index.js',import.meta.url).href)
   ?{url:'data:text/javascript;base64,'+Buffer.from(body).toString('base64'),shortCircuit:true}:next(s,c);}});
 }
 try{const {default:entry}=await import('../plugin/index.js?calendar-consistency='+ ++sequence),tools=new Map();
  entry.register({config:{agents:{defaults:{userTimezone:ZONE}}},registerTool(factory){const tool=factory({agentId:'pixel'});if(tool)tools.set(tool.name,tool);}});return tools;
 }finally{hook?.deregister();for(const[k,v]of Object.entries(prior))if(v===undefined)delete process.env[k];else process.env[k]=v;}
}
test('registered list/count share exact window schema and refuse old/mixed/invalid input before source access',async t=>{
 const reads=[],tools=await native(t,{calendar:'MALFORMED_SOURCE_MUST_NOT_BE_READ'},reads),list=tools.get('pixel_calendar_list'),count=tools.get('pixel_calendar_count');
 assert.deepEqual(list.parameters.properties.window,count.parameters.properties.window);
 assert.deepEqual(list.parameters.required,['window']);assert.equal(list.parameters.properties.timeMin,undefined);
 for(const args of [{timeMin:'2026-11-04T00:00:00Z',timeMax:'2026-11-05T00:00:00Z'},
  {...civil,timeMin:'2026-11-04T00:00:00Z'},{window:{kind:'civil-days',date:'2026-02-30',days:1}},
  {window:{kind:'absolute',timeMin:'2026-11-04T00:00:00Z'}},{window:{kind:'civil-days',date:'2026-11-04',days:1,start:'tomorrow'}}]){
  const reply=await list.execute('invalid',args);assert.equal(reply.isError,true);
  if(!args.window||args.timeMin||args.window.start||!args.window.timeMax&&args.window.kind==='absolute')assert.equal(Check(list.parameters,args),false);
  assert.doesNotMatch(JSON.stringify(reply),/MALFORMED_SOURCE|Unexpected token|SyntaxError|JSON\.parse/);
 }
 const badDate=await count.execute('invalid-date',{window:{kind:'civil-days',date:'2026-02-30',days:1}});
 assert.equal(badDate.isError,true);assert.deepEqual(badDate.details.limitations,['invalid-parameters']);
 assert.deepEqual(reads,[],'schema/semantic invalid arguments refuse before actual Source readFile');
 const family=[...tools.values()].filter(v=>['pixel_gmail_inbox','pixel_gmail_sent','pixel_gmail_search','pixel_gmail_read','pixel_gmail_thread','pixel_gmail_count','pixel_calendar_list','pixel_calendar_get','pixel_calendar_count'].includes(v.name));
 assert.equal(family.length,9);const bytes=Buffer.byteLength(JSON.stringify(family.map(v=>({type:'function',function:{name:v.name,description:v.description,parameters:v.parameters}}))));
 assert.ok(bytes<=8192,'same reviewed family cap');t.diagnostic(JSON.stringify({actualFamilyBytes:bytes}));
});
