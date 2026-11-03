import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {EventEmitter} from 'node:events';
import {createIntegrationOutcomes} from '../plugin/integration-outcomes.mjs';
import {createIntegrationReviewRecovery} from '../plugin/integration-review-recovery.mjs';
import {createIntegrationTools,submitIntegration,canonicalReviewNotePath,canonicalReviewDocumentId} from '../plugin/integrations.mjs';

const ctx={agentId:'pixel',runId:'concrete-owner',sessionId:'concrete-session',sessionKey:'agent:pixel:openai-user:ods-'+'a'.repeat(64),
  trigger:'user',modelProviderId:'ods-gateway',modelId:'ods/current'};
const hash=value=>createHash('sha256').update(value).digest('hex');
const readRef=path=>({version:1,source:'obsidian',binding:hash('default-vault'),
  entries:[{path:canonicalReviewNotePath(path),binding:hash('read:'+canonicalReviewNotePath(path))}],truncated:false});
const note=(path='Example.md')=>({action:'read',path});
const write=(text='Current exact line')=>({action:'write',path:'Example.md',text,mode:'append'});
const event=(id,params,name='pixel_ods_notes')=>({toolName:name,toolCallId:id,params});
const at=id=>({...ctx,toolCallId:id});
const approved=candidate=>Object.freeze({candidate:candidate.ref,admission:'native-review-policy-v1'});
async function nativeController(outcomes,context=ctx) {
  const owner='Use the configured integrations within their current policy.';
  const recovery=createIntegrationReviewRecovery({eligible:()=>true,ready:()=>true,
    nativeStartClear:state=>outcomes.reviewStartClear({agentId:'pixel',runId:state.runId,sessionId:state.sessionId,sessionKey:state.sessionKey}),
    complete:()=>assert.fail('no secondary model is available')});
  recovery.observe({prompt:owner},context,context.runId);
  assert.equal((await recovery.finalize({...context,nativeRetryToolsAllowVersion:1,nativeRetryToolChoiceVersion:1,
    lastAssistantMessage:'A provisional answer.',messages:[{role:'user',content:owner},{role:'assistant',content:'A provisional answer.'}]},context)).action,'revise');
  assert.equal(recovery.admit({...context,nativeRetryToolsAllowState:Object.freeze({version:1,ownerRunId:context.runId,active:true,
    toolsAllow:Object.freeze(['pixel_ods_notes','pixel_ods_mcp'])})},context),undefined);
  return recovery;
}
function fixture(submit) {
  const outcomes=createIntegrationOutcomes();outcomes.begin({},ctx,ctx.runId);
  const sent=[];
  const tools=createIntegrationTools({context:ctx,outcomes,requireBound:()=>true,submit:async payload=>{
    sent.push(payload);
    return submit?submit(payload):{ok:true,recoveryReviewOnly:true,path:payload.path,text:'Invented note',results:[],servers:[],
      ...(payload.action==='notes-read'?{reviewReferences:readRef(payload.path)}:
        payload.action==='notes-search'?{reviewReferences:{version:1,source:'obsidian',binding:hash('default-vault'),entries:[],truncated:false}}:{})};
  }});
  const tool=name=>tools.find(tool=>tool.name===name);
  const prepare=(id,params,name='pixel_ods_notes')=>outcomes.prepareReviewCandidate(event(id,params,name),at(id));
  const admit=async(id,params,name='pixel_ods_notes')=>{
    const candidate=await prepare(id,params,name);
    assert.ok(candidate.ref);
    const verdict=await outcomes.checkNativeCandidate(candidate,c=>approved(c));
    assert.equal(outcomes.commitNativeCandidate(candidate,verdict),undefined);
    return candidate;
  };
  return {outcomes,sent,tool,prepare,admit};
}

test('a provisional or forged-positive candidate cannot dispatch; a checked exact one-use candidate can',async()=>{
  const first=fixture(),candidate=await first.prepare('pending',note());
  assert.equal(first.outcomes.commitNativeCandidate(candidate,approved(candidate)).block,true);
  assert.equal((await first.tool('pixel_ods_notes').execute('pending',note())).details.code,'integration-call-unbound');
  assert.equal(first.sent.length,0);
  const valid=fixture();await valid.admit('valid',note());
  assert.equal((await valid.tool('pixel_ods_notes').execute('valid',note())).details.status,'done');
  assert.equal(valid.sent.length,1);assert.equal(valid.sent[0].recoveryReviewOnly,true);
  assert.equal((await valid.tool('pixel_ods_notes').execute('valid',note())).details.code,'integration-call-unbound');
  assert.equal(valid.sent.length,1);
});

test('native permit object identity is bound to its exact candidate, route context and original family',async()=>{
  for(const mode of ['copy','wrong-ref','old-semantic','bare-true','async','replay']) {
    const f=fixture(),source=await nativeController(f.outcomes),candidate=await f.prepare('first',note());
    const permit=f.outcomes.checkNativeCandidate(candidate,current=>source.admitNativeCandidate(current,at('first')));
    assert.equal(permit.admission,'native-review-policy-v1');
    let supplied=permit;
    if(mode==='copy')supplied=Object.freeze({...permit});
    if(mode==='wrong-ref')supplied=Object.freeze({...permit,candidate:Object.freeze({})});
    if(mode==='old-semantic')supplied=Object.freeze({candidate:candidate.ref,entailed:true});
    if(mode==='bare-true')supplied=true;
    if(mode==='async')supplied=Promise.resolve(permit);
    if(mode==='replay') {
      assert.equal(f.outcomes.commitNativeCandidate(candidate,permit),undefined);
      await f.tool('pixel_ods_notes').execute('first',note());
      const later=await f.prepare('later',note('Other.md'));
      assert.equal(f.outcomes.commitNativeCandidate(later,permit).block,true);
    } else assert.equal(f.outcomes.commitNativeCandidate(candidate,supplied).block,true);
    assert.equal((await f.tool('pixel_ods_notes').execute(mode==='replay'?'later':'first',note())).details.code,'integration-call-unbound');
    assert.equal(f.sent.length,mode==='replay'?1:0);
  }
  const f=fixture(),source=await nativeController(f.outcomes),candidate=await f.prepare('route',note()),scope=at('route');
  const permit=f.outcomes.checkNativeCandidate(candidate,current=>source.admitNativeCandidate(current,scope));
  scope.modelId='foreign';source.observe({prompt:'changed'},scope,scope.runId);
  assert.equal(source.admitNativeCandidate(candidate,scope).admission,'refused');
  f.outcomes.end({success:false},ctx);
  assert.equal(f.outcomes.commitNativeCandidate(candidate,permit).block,true);
  assert.equal(f.sent.length,0);
});

test('the native budget spans sources, counts canonical operations not calls, and exempts bound metadata',async()=>{
  const f=fixture(payload=>({ok:true,recoveryReviewOnly:true,path:payload.path,text:'Bounded result',servers:[],results:[],
    ...(payload.action==='notes-search'?{reviewReferences:{...readRef('Example.md'),entries:[]}}:
      payload.action==='notes-read'?{reviewReferences:readRef(payload.path)}:
      payload.server==='app:documents'?{reviewReferences:{version:1,source:'paperless',binding:hash('docs'),entries:[],truncated:false}}:{})}));
  const source=await nativeController(f.outcomes);
  const calls=[['pixel_ods_mcp',{action:'list'}],['pixel_ods_notes',{action:'search',query:'owned'}],
    ['pixel_ods_notes',note()],['pixel_ods_mcp',{action:'call',server:'app:documents',tool:'search',arguments:{query:'owned'}}],
    ['pixel_ods_mcp',{action:'call',server:'app:photos',tool:'search',arguments:{place:'Lisbon'}}],
    ['pixel_ods_notes',note('Other.md')],['pixel_ods_mcp',{action:'list'}]];
  for(const [index,[name,params]]of calls.entries()) {
    const id='bounded-'+index,candidate=await f.prepare(id,params,name);
    const permit=f.outcomes.checkNativeCandidate(candidate,current=>source.admitNativeCandidate(current,at(id)));
    assert.equal(f.outcomes.commitNativeCandidate(candidate,permit),undefined);
    assert.equal((await f.tool(name).execute(id,params)).details.status,'done');
  }
  f.outcomes.begin({},ctx,ctx.runId); // Same native fallback must not reset budgets.
  const news={action:'call',server:'app:news',tool:'unread',arguments:{}},candidate=await f.prepare('fifth',news,'pixel_ods_mcp');
  const permit=f.outcomes.checkNativeCandidate(candidate,current=>source.admitNativeCandidate(current,at('fifth')));
  assert.equal(f.outcomes.commitNativeCandidate(candidate,permit).code,'review-operation-limit');
  assert.equal((await f.tool('pixel_ods_mcp').execute('fifth',news)).details.code,'integration-call-unbound');
  assert.equal(f.sent.length,calls.length);
});

test('queued identical proposals reserve one original family before any second host submission',async()=>{
  let release;const f=fixture(()=>new Promise(resolve=>{release=()=>resolve({ok:true,status:'approval-required',approvalId:'pending',
    recoveryReviewOnly:true,actionExecuted:false,approvalConsumed:false});})),source=await nativeController(f.outcomes);
  const parent=await f.outcomes.prepareReviewCandidate(event('parent',{id:'openclaw:pixel-ods:pixel_ods_notes',args:write()},'tool_call'),at('parent'));
  const permit=f.outcomes.checkNativeCandidate(parent,current=>source.admitNativeCandidate(current,at('parent')));
  assert.equal(f.outcomes.commitNativeCandidate(parent,permit),undefined);
  const childId='tool_search_code:parent:pixel_ods_notes:1',child=await f.prepare(childId,write());
  assert.equal(child,parent);assert.equal(f.outcomes.checkNativeCandidate(child,()=>assert.fail('original family must reuse proof')),permit);
  assert.equal(f.outcomes.commitNativeCandidate(child,permit),undefined);
  const host=f.tool('pixel_ods_notes').execute(childId,write());let queued=false;
  const second=f.prepare('different-original-family',write()).then(value=>{queued=true;return value;});
  await new Promise(resolve=>setImmediate(resolve));assert.equal(queued,false);assert.equal(f.sent.length,1);
  release();assert.equal((await host).details.status,'approval-required');const candidate=await second;
  const next=f.outcomes.checkNativeCandidate(candidate,current=>source.admitNativeCandidate(current,at('different-original-family')));
  assert.equal(f.outcomes.commitNativeCandidate(candidate,next).code,'review-proposal-limit');
  assert.equal((await f.tool('pixel_ods_notes').execute('different-original-family',write())).details.code,'integration-call-unbound');
  assert.equal(f.sent.length,1);
  const newOwner={...ctx,runId:'explicit-new-owner'};f.outcomes.begin({},newOwner,newOwner.runId);
  const fresh=await nativeController(f.outcomes,newOwner),current=await f.outcomes.prepareReviewCandidate(event('fresh',write()),{...newOwner,toolCallId:'fresh'});
  const valid=f.outcomes.checkNativeCandidate(current,value=>fresh.admitNativeCandidate(value,{...newOwner,toolCallId:'fresh'}));
  assert.equal(f.outcomes.commitNativeCandidate(current,valid),undefined);
  assert.equal((await f.prepare('old-late',write())).block,true);
  f.outcomes.clear();
});

test('thenable authority, post-check mutation and a late invalid mode cannot borrow an accepted append',async()=>{
  const changed=fixture(),params=write(),candidate=await changed.prepare('changed',params);
  const held=changed.outcomes.checkNativeCandidate(candidate,()=>Promise.resolve(approved(candidate)));
  assert.equal(held,undefined,'native policy is synchronous; a promised positive is not authority');
  assert.equal(changed.outcomes.commitNativeCandidate(candidate,held).block,true);
  assert.equal(changed.sent.length,0);
  const drift=fixture(),raw=write(),current=await drift.prepare('drift',raw);
  const permit=drift.outcomes.checkNativeCandidate(current,approved);raw.text='Different current text';
  assert.equal(drift.outcomes.commitNativeCandidate(current,permit).block,true);
  const late=fixture();await late.admit('late',write());
  const result=await late.tool('pixel_ods_notes').execute('late',{...write(),mode:'overwrite'});
  assert.equal(result.details.code,'integration-call-unbound');assert.equal(late.sent.length,0);
  assert.match(late.outcomes.projection(ctx.runId),/did not submit/);
});

test('raw source drift after taking a ticket is refused at the actual socket write boundary',async()=>{
  const f=fixture(),params=note();await f.admit('socket',params);let writes=0;
  const tools=createIntegrationTools({context:ctx,outcomes:f.outcomes,requireBound:()=>true,
    submit:(payload,options)=>submitIntegration(payload,{...options,platform:'linux',connect:()=>{
      const socket=new EventEmitter();socket.destroy=()=>{};socket.write=()=>{writes++;};
      queueMicrotask(()=>{params.path='Changed.md';socket.emit('connect');});return socket;
    }})});
  const result=await tools.find(tool=>tool.name==='pixel_ods_notes').execute('socket',params);
  assert.equal(result.details.code,'integration-call-unbound');assert.equal(writes,0);
  assert.match(f.outcomes.projection(ctx.runId),/did not submit/);
  assert.equal((await f.prepare('later',note())).block,true);
});

test('known builtin unknown arguments are rejected before a semantic check or host submission',async()=>{
  const f=fixture();
  const denied=await f.prepare('ignored-date',{action:'call',server:'app:news',tool:'digest',arguments:{before:'2026-01-01'}},'pixel_ods_mcp');
  assert.equal(denied.block,true);assert.equal(f.sent.length,0);
});

test('the first native floor refusal records a fact without any ticket and never adopts a sparse or fresh owner',()=>{
  const outcomes=createIntegrationOutcomes();outcomes.begin({},ctx,ctx.runId);
  outcomes.refuseReviewCall(undefined,ctx,'review-candidate-refused',{recoveryReviewOnly:true});
  assert.match(outcomes.projection(ctx.runId),/did not submit/);assert.equal(outcomes.reviewStartClear(ctx),false);
  assert.equal(outcomes.take('absent',{action:'notes-read',path:'Example.md'},ctx,{name:'pixel_ods_notes',params:note()}),undefined);
  const next={...ctx,runId:'fresh-owner',trigger:'user'};outcomes.begin({},next,next.runId);
  outcomes.refuseReviewCall('old',ctx,'review-candidate-refused',{recoveryReviewOnly:true});
  outcomes.refuseReviewCall('sparse',{agentId:ctx.agentId,sessionId:ctx.sessionId,sessionKey:ctx.sessionKey},
    'review-candidate-refused',{recoveryReviewOnly:true});
  assert.equal(outcomes.projection(next.runId),undefined);assert.equal(outcomes.reviewStartClear(next),true);
});

test('review candidates refuse actual host truncation tails without changing or shortening arguments',async()=>{
  for (const [name,params] of [
    ['pixel_ods_notes',{action:'search',query:'x'.repeat(301)}],
    ['pixel_ods_mcp',{action:'call',server:'app:documents',tool:'search',arguments:{query:'x'.repeat(200)+' restriction'}}],
    ['pixel_ods_mcp',{action:'call',server:'app:photos',tool:'search',arguments:{place:'x'.repeat(101)}}],
    ['pixel_ods_mcp',{action:'call',server:'app:photos',tool:'search',arguments:{query:'x'.repeat(200)+'\ufeff'}}],
    ['pixel_ods_mcp',{action:'call',server:'app:watch',tool:'add',arguments:{url:'https://example.com',title:'x'.repeat(201)}}],
  ]) {
    const f=fixture(),before=JSON.stringify(params),result=await f.prepare('tail',params,name);
    assert.equal(result.block,true);assert.equal(f.sent.length,0);assert.equal(JSON.stringify(params),before);
  }
  const f=fixture(),params={action:'call',server:'app:documents',tool:'search',arguments:{query:'\u0085 '+'x'.repeat(200)+'\u001c'}};
  const candidate=await f.prepare('normal-whitespace',params,'pixel_ods_mcp');
  assert.equal(candidate.view.payload.arguments.query,params.arguments.query,'normalization is checked, never substituted');
});

test('native wrapper and exact child share one verifier and admission, without queueing behind themselves',async()=>{
  const f=fixture(),params=note();let checks=0;
  const parent=await f.outcomes.prepareReviewCandidate(event('parent',{id:'openclaw:pixel-ods:pixel_ods_notes',args:params},'tool_call'),at('parent'));
  const verdict=await f.outcomes.checkNativeCandidate(parent,c=>{checks++;return approved(c);});
  assert.equal(f.outcomes.commitNativeCandidate(parent,verdict),undefined);
  const childId='tool_search_code:parent:pixel_ods_notes:1';
  const child=await f.prepare(childId,params);
  assert.equal(child,parent);
  assert.equal(await f.outcomes.checkNativeCandidate(child,()=>assert.fail('a child must not rejudge')),verdict);
  assert.equal(f.outcomes.commitNativeCandidate(child,verdict),undefined);
  assert.equal((await f.tool('pixel_ods_notes').execute(childId,params)).details.status,'done');
  assert.equal(checks,1);assert.equal(f.sent.length,1);
});

test('distinct candidate families wait through real host settlement, not merely the first verdict',async()=>{
  let release,entered;
  const started=new Promise(resolve=>{entered=resolve;});
  const f=fixture(payload=>new Promise(resolve=>{entered();release=()=>resolve({ok:true,recoveryReviewOnly:true,path:payload.path,text:'Read',reviewReferences:readRef(payload.path)});}));
  await f.admit('first',note());
  const executing=f.tool('pixel_ods_notes').execute('first',note());await started;
  let prepared=false;
  const next=f.prepare('second',note('Second.md')).then(value=>{prepared=true;return value;});
  await new Promise(resolve=>setImmediate(resolve));assert.equal(prepared,false);
  release();await executing;
  const candidate=await next;assert.ok(candidate.ref);assert.equal(prepared,true);
  const verdict=await f.outcomes.checkNativeCandidate(candidate,c=>approved(c));
  assert.equal(f.outcomes.commitNativeCandidate(candidate,verdict),undefined);
  f.outcomes.cancelUser('ods-'+'a'.repeat(64));
});

test('a later native result without factory entry releases only that unsubmitted family',async()=>{
  const f=fixture();await f.admit('blocked-later',note());
  const next=f.prepare('next',note('Second.md'));
  f.outcomes.after(event('blocked-later',note()),at('blocked-later'));
  const candidate=await next;assert.ok(candidate.ref);assert.equal(f.sent.length,0);
  assert.equal((await f.tool('pixel_ods_notes').execute('blocked-later',note())).details.code,'integration-call-unbound');
});

test('uncertainty refuses effectful queued calls but preserves otherwise-admitted builtin reads',async()=>{
  let reject,entered;
  const started=new Promise(resolve=>{entered=resolve;});
  const f=fixture(payload=>payload.action==='notes-write'?new Promise((_resolve,no)=>{entered();reject=()=>no(Object.assign(Error('lost acknowledgement'),{code:'integration-outcome-unknown'}));})
    :{ok:true,recoveryReviewOnly:true,path:payload.path,text:'Read',reviewReferences:readRef(payload.path)});
  await f.admit('uncertain',write());const executing=f.tool('pixel_ods_notes').execute('uncertain',write());await started;
  const effectful=f.prepare('next-write',write('Not submitted'));
  reject();assert.equal((await executing).details.status,'outcome-unknown');
  const refused=await effectful;assert.equal(refused.code,'integration-outcome-unresolved');
  await f.admit('safe-read',note());
  assert.equal((await f.tool('pixel_ods_notes').execute('safe-read',note())).details.status,'done');
  assert.equal(f.sent.length,2);
});

test('cancellation releases queued families and old settlement cannot release a new owner lease',async()=>{
  let release;
  const f=fixture(payload=>new Promise(resolve=>{release=()=>resolve({ok:true,recoveryReviewOnly:true,path:payload.path,text:'Old result'});}));
  await f.admit('old',note());const old=f.tool('pixel_ods_notes').execute('old',note());
  const queued=f.prepare('queued',note('Queued.md'));
  f.outcomes.cancelUser('ods-'+'a'.repeat(64));assert.equal((await queued).block,true);
  const newer={...ctx,runId:'new-owner'};f.outcomes.begin({},newer,newer.runId);
  const candidate=await f.outcomes.prepareReviewCandidate(event('new',note()),{...newer,toolCallId:'new'});
  assert.ok(candidate.ref);
  release();await old;
  const verdict=await f.outcomes.checkNativeCandidate(candidate,c=>approved(c));
  assert.equal(f.outcomes.commitNativeCandidate(candidate,verdict),undefined);
  f.outcomes.clear();
});

test('an old native aborted closure cannot consume or stop a newer same-ID owner admission',async()=>{
  const f=fixture(),oldContext={agentId:ctx.agentId,sessionId:ctx.sessionId,sessionKey:ctx.sessionKey};
  const old=createIntegrationTools({context:oldContext,outcomes:f.outcomes,requireBound:()=>true,
    submit:async()=>assert.fail('an aborted old native closure reached the host')}).find(t=>t.name==='pixel_ods_notes');
  f.outcomes.end({success:false},ctx);
  const next={...ctx,runId:'new-collision-owner'};f.outcomes.begin({},next,next.runId);
  const candidate=await f.outcomes.prepareReviewCandidate(event('reused',note()),{...next,toolCallId:'reused'});
  const verdict=await f.outcomes.checkNativeCandidate(candidate,c=>approved(c));
  assert.equal(f.outcomes.commitNativeCandidate(candidate,verdict),undefined);
  const controller=new AbortController();controller.abort();
  assert.equal((await old.execute('reused',note(),controller.signal)).details.code,'integration-call-cancelled');
  let sent=0;const fresh=createIntegrationTools({context:next,outcomes:f.outcomes,requireBound:()=>true,
    submit:async()=>{sent++;return {ok:true,recoveryReviewOnly:true,path:'Example.md',text:'Current',reviewReferences:readRef('Example.md')};}}).find(t=>t.name==='pixel_ods_notes');
  assert.equal((await fresh.execute('reused',note())).details.status,'done');assert.equal(sent,1);
  assert.equal(f.outcomes.projection(next.runId),undefined);
});

test('typed lookup facts stay private and >16 retained refs preserve the selected old-last target and note alias',async()=>{
  const source=hash('vault'),entry=i=>({path:`N${i}.md`,binding:hash('target'+i)});
  const f=fixture(payload=>payload.action==='notes-search'?{ok:true,recoveryReviewOnly:true,results:[],
    reviewReferences:{version:1,source:'obsidian',binding:source,entries:Array.from({length:16},(_v,i)=>entry((payload.query==='first'?0:16)+i)),truncated:false}}
    :{ok:true,recoveryReviewOnly:true,path:'N15.md',text:'Current note',reviewReferences:{version:1,source:'obsidian',binding:source,entries:[entry(15)],truncated:false}});
  for(const query of ['first','second']) {
    await f.admit(query,{action:'search',query});
    const reply=await f.tool('pixel_ods_notes').execute(query,{action:'search',query});
    assert.equal(JSON.stringify(reply).includes(source),false);
  }
  const alias=' \u0085N15 ',candidate=await f.admit('target',note(alias));
  assert.equal(candidate.view.references.lookups.reduce((n,item)=>n+item.entries.length,0),32);
  assert.equal(JSON.stringify(candidate.view.references).includes('binding'),false);
  await f.tool('pixel_ods_notes').execute('target',note(alias));
  const expected=f.sent.at(-1).recoveryReferenceContext;
  assert.equal(expected.entries.length,16);assert.equal(expected.truncated,true);
  assert.ok(expected.entries.some(item=>item.path==='N15.md'&&item.binding===hash('target15')));
  assert.equal(canonicalReviewNotePath(alias),'N15.md');
});

test('decimal-string document identities beyond JS precision remain exact; unsafe numeric candidates refuse',async()=>{
  const id='9007199254740993';
  const f=fixture(()=>({ok:true,recoveryReviewOnly:true,text:'Invented document',isError:false,
    reviewReferences:{version:1,source:'paperless',binding:hash('docs'),entries:[{id,title:'Invented'}],truncated:false}}));
  const search={action:'call',server:'app:documents',tool:'search',arguments:{query:'invented'}};
  await f.admit('search',search,'pixel_ods_mcp');await f.tool('pixel_ods_mcp').execute('search',search);
  const read={action:'call',server:'app:documents',tool:'read',arguments:{id}};
  await f.admit('read',read,'pixel_ods_mcp');await f.tool('pixel_ods_mcp').execute('read',read);
  assert.equal(f.sent.at(-1).recoveryReferenceContext.entries[0].id,id);
  assert.equal(canonicalReviewDocumentId(Number(id)),undefined);
  assert.equal((await f.prepare('unsafe',{...read,arguments:{id:Number(id)}},'pixel_ods_mcp')).block,true);
});

test('host-valid non-BMP note paths and document titles retain their exact typed reference identities',async()=>{
  const path=Array.from({length:20},()=> '😀'.repeat(15)).join('/')+'.md';
  assert.ok(path.length>512);assert.ok([...path].length<=512);assert.equal(canonicalReviewNotePath(path),path);
  const f=fixture(payload=>({ok:true,recoveryReviewOnly:true,results:[],path:payload.path,text:'Invented bounded read',reviewReferences:{version:1,source:'obsidian',
    binding:hash('unicode-vault'),entries:[{path,binding:hash('unicode-note')}],truncated:false}}));
  const search={action:'search',query:'invented'};await f.admit('search',search);await f.tool('pixel_ods_notes').execute('search',search);
  const candidate=await f.admit('read',note(path));assert.equal(candidate.view.references.lookups[0].entries[0].path,path);
  await f.tool('pixel_ods_notes').execute('read',note(path));assert.equal(f.sent.at(-1).recoveryReferenceContext.entries[0].path,path);
  const docs=fixture(()=>({ok:true,recoveryReviewOnly:true,text:'Invented',reviewReferences:{version:1,source:'paperless',
    binding:hash('unicode-docs'),entries:[{id:'1',title:'😀'.repeat(200)}],truncated:false}}));
  const find={action:'call',server:'app:documents',tool:'search',arguments:{query:'invented'}};
  await docs.admit('search',find,'pixel_ods_mcp');await docs.tool('pixel_ods_mcp').execute('search',find);
  const read={action:'call',server:'app:documents',tool:'read',arguments:{id:'1'}};
  const doc=await docs.admit('read',read,'pixel_ods_mcp');assert.equal(doc.view.references.lookups[0].entries[0].title,'😀'.repeat(200));
});

test('clipping all lookup facts never drops the privately observed source generation',async()=>{
  const binding=hash('docs-before-connection-change');
  const f=fixture(()=>({ok:true,recoveryReviewOnly:true,text:'Invented lookup',reviewReferences:{version:1,source:'paperless',
    binding,entries:[{id:'1',title:'Invented'}],truncated:false}}));
  const search={action:'call',server:'app:documents',tool:'search',arguments:{query:' '.repeat(17000)+'invented'}};
  await f.admit('large-query',search,'pixel_ods_mcp');await f.tool('pixel_ods_mcp').execute('large-query',search);
  const read={action:'call',server:'app:documents',tool:'read',arguments:{id:'1'}};
  const candidate=await f.admit('read',read,'pixel_ods_mcp');
  assert.deepEqual(candidate.view.references,{lookups:[],truncated:true});
  await f.tool('pixel_ods_mcp').execute('read',read);
  assert.equal(f.sent.at(-1).recoveryReferenceContext.binding,binding);
  assert.deepEqual(f.sent.at(-1).recoveryReferenceContext.entries,[]);
  assert.equal(f.sent.at(-1).recoveryReferenceContext.truncated,true);
});

test('missing or malformed successful lookup references cannot settle or ground another private call',async()=>{
  for (const references of [undefined,{}, {version:1,source:'obsidian',binding:hash('vault'),entries:[{path:'a.md'}],truncated:false}]) {
    const f=fixture(()=>({ok:true,recoveryReviewOnly:true,results:[],reviewReferences:references}));
    const search={action:'search',query:'invented'};await f.admit('lookup',search);
    const result=await f.tool('pixel_ods_notes').execute('lookup',search);
    assert.equal(result.details.code,'review-reference-result-invalid');assert.equal(result.isError,true);
    assert.equal((await f.prepare('retry',note())).block,true);assert.equal(f.sent.length,1);
    assert.match(f.outcomes.projection(ctx.runId),/lookup was submitted/);
    assert.doesNotMatch(f.outcomes.projection(ctx.runId),/did not submit/);
  }
});

test('a stale host reference refusal terminates review I/O, releases queues and never appears as unknown or done',async()=>{
  const f=fixture(()=>({ok:false,error:'stale-review-reference',message:'Connection changed.'}));
  await f.admit('stale',note());
  const result=await f.tool('pixel_ods_notes').execute('stale',note());
  assert.equal(result.details.status,'refused');assert.equal(result.details.code,'stale-review-reference');
  assert.equal((await f.prepare('probe',note('Other.md'))).block,true);
  assert.equal(f.sent.length,1);assert.match(f.outcomes.projection(ctx.runId),/service refused this proposed/);
  assert.doesNotMatch(f.outcomes.projection(ctx.runId),/did not submit|nothing ran/);
});

test('the final review wire frame includes schema/control/refs and refuses oversize before any submit',async()=>{
  const entries=Array.from({length:16},(_v,i)=>({path:`N${i}-`+'x'.repeat(400)+'.md',binding:hash('long'+i)}));
  const f=fixture(()=>({ok:true,recoveryReviewOnly:true,results:[],reviewReferences:{version:1,source:'obsidian',binding:hash('large'),entries,truncated:false}}));
  await f.admit('lookup',{action:'search',query:'long'});await f.tool('pixel_ods_notes').execute('lookup',{action:'search',query:'long'});
  const params=write('界'.repeat(20000));await f.admit('large',params);
  const result=await f.tool('pixel_ods_notes').execute('large',params);
  assert.equal(result.details.code,'integration-request-too-large');assert.equal(f.sent.length,1);
  assert.equal((await f.prepare('after',note())).block,true);
});
