import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createIntegrationOutcomes} from '../plugin/integration-outcomes.mjs';
import {createIntegrationTools,canonicalReviewNotePath,integrationRequest} from '../plugin/integrations.mjs';
import {createIntegrationReviewRecovery,REVIEW_LABEL_SYSTEM} from '../plugin/integration-review-recovery.mjs';

const context={agentId:'pixel',runId:'progress-owner',sessionId:'progress-session',sessionKey:'agent:pixel:openai-user:ods-'+'a'.repeat(64),
  trigger:'user',modelProviderId:'ods-gateway',modelId:'ods/current'};
const at=id=>({...context,toolCallId:id});
const hash=value=>createHash('sha256').update(value).digest('hex');
const pathParams=(path='Example.md')=>({action:'read',path});
const append={action:'write',path:'Example.md',text:'Exact current line',mode:'append'};
const ref=(path='Example.md',binding=hash('file:'+path),sourceBinding=hash('vault'))=>({version:1,source:'obsidian',binding:sourceBinding,
  entries:[{path,binding}],truncated:false});
const positive=candidate=>Object.freeze({candidate:candidate.ref,entailed:true});
function fixture(reply){
  const outcomes=createIntegrationOutcomes();outcomes.begin({},context,context.runId);const sent=[];
  const tools=createIntegrationTools({context,outcomes,requireBound:()=>true,submit:async payload=>{
    sent.push(payload);return reply?reply(payload):{ok:true,recoveryReviewOnly:true,path:canonicalReviewNotePath(payload.path),
      text:'Invented bounded read',reviewReferences:ref(canonicalReviewNotePath(payload.path))};
  }});
  const tool=name=>tools.find(value=>value.name===name);
  const prepare=(id,params,name='pixel_ods_notes')=>outcomes.prepareReviewCandidate({toolName:name,params,toolCallId:id},at(id));
  const admit=async(id,params,name='pixel_ods_notes')=>{
    const candidate=await prepare(id,params,name);assert.ok(candidate.ref);
    const verdict=await outcomes.checkReviewCandidate(candidate,async value=>positive(value));
    assert.equal(outcomes.commitReviewCandidate(candidate,verdict),undefined);return candidate;
  };
  return {outcomes,sent,tools,tool,prepare,admit};
}

test('only an actual validated direct read creates a prior completion fact for a later proposal',async()=>{
  const f=fixture(),before=await f.admit('read',pathParams());
  assert.deepEqual(before.view.progress,{reads:[],truncated:false});
  assert.equal((await f.tool('pixel_ods_notes').execute('read',pathParams())).details.status,'done');
  const later=await f.prepare('append',append);
  assert.deepEqual(later.view.progress,{reads:[{sequence:1,raw:{name:'pixel_ods_notes',params:pathParams()},status:'read-complete'}],truncated:false});
  assert.ok(Object.isFrozen(later.view.progress.reads[0].raw.params));
  assert.equal(JSON.stringify(later.view.progress).includes('Invented bounded read'),false);
  assert.equal(JSON.stringify(later.view.progress).includes(hash('file:Example.md')),false);
});

test('rendered native status, refusal and a different successful read cannot prove the requested read',async()=>{
  const forged=fixture();await forged.admit('not-executed',pathParams());
  forged.outcomes.after({toolName:'pixel_ods_notes',toolCallId:'not-executed',params:pathParams(),
    result:{details:{status:'done',readCompleted:true}}},at('not-executed'));
  assert.deepEqual((await forged.prepare('append',append)).view.progress.reads,[]);
  const refused=fixture(()=>({ok:false,error:'notes-off',message:'Off',readCompleted:true,reviewReferences:ref()}));
  await refused.admit('refused',pathParams());assert.equal((await refused.tool('pixel_ods_notes').execute('refused',pathParams())).details.status,'refused');
  assert.deepEqual((await refused.prepare('append',append)).view.progress.reads,[]);
  const other=fixture();await other.admit('other',pathParams('Other.md'));await other.tool('pixel_ods_notes').execute('other',pathParams('Other.md'));
  const next=await other.prepare('append',append);
  assert.equal(next.view.progress.reads.some(value=>value.raw.params.path==='Example.md'),false);
  assert.equal(next.view.progress.reads[0].raw.params.path,'Other.md');
});

test('settled alone or a nonmatching read envelope is never a read-complete proof',async()=>{
  const f=fixture();await f.admit('forged',pathParams());
  const token=f.outcomes.take('forged',integrationRequest('pixel_ods_notes',pathParams(),context),context,
    {name:'pixel_ods_notes',params:pathParams()});
  assert.equal(f.outcomes.complete(token,'settled',ref()),false,'missing code-owned direct-read marker');
  assert.equal((await f.prepare('append',append)).block,true);
  const wrong=fixture(()=>({ok:true,recoveryReviewOnly:true,path:'Example.md',text:'Bounded read',reviewReferences:ref('Other.md')}));
  await wrong.admit('wrong',pathParams());
  assert.equal((await wrong.tool('pixel_ods_notes').execute('wrong',pathParams())).details.code,'review-reference-result-invalid');
  assert.equal((await wrong.prepare('append',append)).block,true);
});

test('a valid empty truncated read envelope retains source custody but creates no target proof',async()=>{
  const f=fixture(()=>({ok:true,recoveryReviewOnly:true,path:'Example.md',text:'Bounded read',
    reviewReferences:{version:1,source:'obsidian',binding:hash('vault'),entries:[],truncated:true}}));
  await f.admit('empty',pathParams());assert.equal((await f.tool('pixel_ods_notes').execute('empty',pathParams())).details.status,'done');
  const next=await f.prepare('append',append);assert.deepEqual(next.view.progress,{reads:[],truncated:true});
});

test('a completed Documents read preserves its exact decimal target and records no body or binding',async()=>{
  const id='9007199254740993',binding=hash('documents');
  const f=fixture(()=>({ok:true,recoveryReviewOnly:true,text:'Invented bounded document body',
    reviewReferences:{version:1,source:'paperless',binding,entries:[{id,title:'Invented title'}],truncated:false}}));
  const params={action:'call',server:'app:documents',tool:'read',arguments:{id}};
  await f.admit('doc',params,'pixel_ods_mcp');assert.equal((await f.tool('pixel_ods_mcp').execute('doc',params)).details.status,'done');
  const next=await f.prepare('next-doc',params,'pixel_ods_mcp');
  assert.deepEqual(next.view.progress.reads,[{sequence:1,raw:{name:'pixel_ods_mcp',params},status:'read-complete'}]);
  assert.equal(next.view.progress.reads[0].raw.params.arguments.id,id);
  assert.doesNotMatch(JSON.stringify(next.view.progress),new RegExp(binding+'|Invented bounded document body'));
});

test('new source epochs or newer same-path targets expire old progress without resurrection',async()=>{
  for(const change of ['source','target']) {
    let stage=0;const f=fixture(payload=>payload.action==='notes-read'?{ok:true,recoveryReviewOnly:true,path:'Example.md',text:'Bounded',reviewReferences:ref()}
      :{ok:true,recoveryReviewOnly:true,results:[],reviewReferences:stage++===0?
        ref('Example.md',hash('new-file'),change==='source'?hash('new-vault'):hash('vault')):ref()});
    await f.admit('read',pathParams());await f.tool('pixel_ods_notes').execute('read',pathParams());
    for(const id of ['changed','old-shaped-again']) {
      const search={action:'search',query:id};await f.admit(id,search);await f.tool('pixel_ods_notes').execute(id,search);
    }
    const next=await f.prepare('append',append);
    assert.deepEqual(next.view.progress.reads,[]);assert.equal(next.view.progress.truncated,true);
  }
});

test('a matching read fact keeps its private target expectation when public lookup facts clip',async()=>{
  let lookup=0;const f=fixture(payload=>payload.action==='notes-read'?{ok:true,recoveryReviewOnly:true,path:'Example.md',text:'Bounded',reviewReferences:ref()}
    :payload.action==='notes-search'?{ok:true,recoveryReviewOnly:true,results:[],reviewReferences:{version:1,source:'obsidian',binding:hash('vault'),
      entries:Array.from({length:16},(_value,index)=>({path:`L${lookup}-${index}-`+'x'.repeat(400)+'.md',binding:hash(`L${lookup}-${index}`)})),truncated:false}}
      :{ok:true,status:'approval-required',approvalId:'new-approval',recoveryReviewOnly:true,actionExecuted:false,approvalConsumed:false});
  await f.admit('read',pathParams());await f.tool('pixel_ods_notes').execute('read',pathParams());
  for(lookup=0;lookup<3;lookup++) {const search={action:'search',query:'long'+lookup};await f.admit('lookup'+lookup,search);await f.tool('pixel_ods_notes').execute('lookup'+lookup,search);}
  const candidate=await f.admit('append',append);
  assert.equal(candidate.view.references.lookups.some(group=>group.entries.some(entry=>entry.path==='Example.md')),false);
  assert.equal(candidate.view.progress.reads[0].raw.params.path,'Example.md');
  await f.tool('pixel_ods_notes').execute('append',append);
  assert.ok(f.sent.at(-1).recoveryReferenceContext.entries.some(entry=>entry.path==='Example.md'&&entry.binding===hash('file:Example.md')));
});

test('bounded progress prioritizes the candidate target without manufacturing future or duplicate sequence IDs',async()=>{
  const f=fixture();for(let index=0;index<17;index++) {const params=pathParams('N'+index+'.md');await f.admit('r'+index,params);await f.tool('pixel_ods_notes').execute('r'+index,params);}
  const candidate=await f.prepare('append',{...append,path:'N0.md'}),progress=candidate.view.progress;
  assert.equal(progress.reads.length,16);assert.equal(progress.truncated,true);assert.equal(progress.reads[0].sequence,1);
  assert.equal(new Set(progress.reads.map(value=>value.sequence)).size,16);
  assert.ok(progress.reads.every(value=>value.sequence<18));assert.ok(Buffer.byteLength(JSON.stringify(progress))<=8192);
});

test('actual Recovery receives completed-read evidence only after real factory settlement',async()=>{
  for(const hasRead of [false,true]) {
    const f=fixture(payload=>payload.action==='notes-read'?{ok:true,recoveryReviewOnly:true,path:'Example.md',text:'Bounded read',reviewReferences:ref()}
      :{ok:true,status:'approval-required',approvalId:'fresh',recoveryReviewOnly:true,actionExecuted:false,approvalConsumed:false});
    const owner='Read Example.md, then only request approval to append Exact current line to that same note.';
    const inputs=[];
    const recovery=createIntegrationReviewRecovery({eligible:()=>true,ready:()=>true,
      nativeStartClear:state=>f.outcomes.reviewStartClear({agentId:'pixel',runId:state.runId,sessionId:state.sessionId,sessionKey:state.sessionKey}),
      complete:async params=>{
        const input=JSON.parse(params.messages[0].content);inputs.push(input);
        const data=params.systemPrompt===REVIEW_LABEL_SYSTEM?{operations:[{id:'notes.read',approval:'none'},{id:'notes.append',approval:'before'}]}
          :{entailed:input.candidateOperation.maximumEffect==='private-read'||input.priorCompletedReads.reads.some(value=>value.operation.arguments.path==='Example.md')};
        return {agentId:'pixel',provider:'ods-gateway',model:'ods/current',text:JSON.stringify(data)};
      }});
    recovery.observe({prompt:owner},context,context.runId);
    assert.equal((await recovery.finalize({...context,nativeRetryToolsAllowVersion:1,nativeRetryToolChoiceVersion:1,
      messages:[{role:'user',content:owner},{role:'assistant',content:'I will do that.'}],lastAssistantMessage:'I will do that.'},context)).action,'revise');
    const checked=async(id,params)=>{
      const ev={toolName:'pixel_ods_notes',params,toolCallId:id};recovery.beforeTool(ev,at(id));
      const candidate=await f.outcomes.prepareReviewCandidate(ev,at(id));
      const verdict=await f.outcomes.checkReviewCandidate(candidate,value=>recovery.checkConcrete(value,at(id)));
      return f.outcomes.commitReviewCandidate(candidate,verdict);
    };
    if(hasRead) {assert.equal(await checked('read',pathParams()),undefined);await f.tool('pixel_ods_notes').execute('read',pathParams());}
    const decision=await checked('append',append);
    if(hasRead) {assert.equal(decision,undefined);assert.equal((await f.tool('pixel_ods_notes').execute('append',append)).details.status,'approval-required');}
    else {assert.equal(decision.block,true);assert.equal(f.sent.length,0);}
    assert.equal(inputs.at(-1).priorCompletedReads.reads.length,hasRead?1:0);
  }
});
