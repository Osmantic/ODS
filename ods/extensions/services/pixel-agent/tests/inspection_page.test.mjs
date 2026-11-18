// An optional page starts an inspection on another page of the same published
// snapshot ("a three-page bakery site: home, menu and contact"). It grants no
// new permission: links between pages are still not followed, and every
// receipt of a page plan names its page.
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createWorkspacePreviewInspectTool,normalizeWorkspacePreviewInspectionParams as normalize,validateWorkspacePreviewInspectionReceipt as validate,
  inspectionPlanHash,pageFile,transitionCorrection,INSPECTION_KIND,INSPECTION_SCOPE,PAGE_INSPECTION_SCOPE,PRESS_INSPECTION_SCOPE,
  STEP_INSPECTION_SCOPE} from '../plugin/workspace-preview-inspect.mjs';
import {boundInspectionControls,boundRenderedPreviewInspection,boundVisibilityInspection} from '../plugin/preview-interaction-assurance.mjs';

const preview={siteId:'site-'+'a'.repeat(24),sha256:'a'.repeat(64)};
const visible=selector=>({action:'assert-visible',locator:{selector}});
const press={action:'press',locator:{selector:'#game'},key:'Space'};
const params=(page,...steps)=>({...preview,...(page===undefined?{}:{page}),viewport:{width:800,height:600},steps:steps.length?steps:[visible('h1')]});
const state=shown=>({count:1,visible:shown,display:shown?'block':'none',visibility:'visible',opacity:'1',hidden:!shown,hiddenUntilFound:false,rectCount:shown?1:0});
const CONTROLS={count:1,items:[{role:'button',name:'Show specials',visible:true,source:'content'}]};
// The scope of these plans: the press base for press plans, and the page suffix for page plans.
const scopeOf=request=>(request.steps.some(s=>s.action==='press')?STEP_INSPECTION_SCOPE+PRESS_INSPECTION_SCOPE:INSPECTION_SCOPE)+
  (request.page===undefined?'':PAGE_INSPECTION_SCOPE);
function receipt(request,extra={}) {
  return {schemaVersion:1,kind:INSPECTION_KIND,status:'passed',siteId:request.siteId,sha256:request.sha256,planSha256:inspectionPlanHash(request),
    ...(request.page===undefined?{}:{page:request.page}),viewport:request.viewport,
    steps:request.steps.map((s,index)=>({index,...s,stable:true,status:'passed',before:state(s.action!=='assert-hidden'),...(s.action==='click'?{after:state(true)}:{})})),
    diagnostics:{renderedHiddenAttributeCount:0,hiddenUntilFoundCount:0},blockedRequests:[],scope:scopeOf(request),...extra};
}
const failure=(request,errorCode)=>({schemaVersion:1,kind:INSPECTION_KIND,status:'failed',errorCode,siteId:request.siteId,sha256:request.sha256,
  planSha256:inspectionPlanHash(request),...(request.page===undefined?{}:{page:request.page}),scope:scopeOf(request)});
const run=async(plan,answer)=>createWorkspacePreviewInspectTool({request:async request=>answer(request)}).execute('page',plan);

// Published pages of a site, and nested ones: a trailing / names that
// folder's index.html, as the capsule's loopback server serves it. At most
// 512 characters, 12 segments and 128 characters per segment, as the snapshot.
const LONG=`${'x'.repeat(120)}/`.repeat(4);
const GOOD=['menu.html','about/team.html','about/','index.html','[slug].html','_not-found.html','v1.2/notes.html',
  'a/b/c/d/e/f/g/h/i/j/k/x.html','x/'.repeat(11),'x'.repeat(123)+'.html',LONG+'x'.repeat(23)+'.html'];
// Traversal, absolute paths, URLs, queries, fragments, hidden or reserved
// names, other file types, empty segments and the bounds.
const BAD=['','/','//','/menu.html','\\menu.html','menu\\x.html','menu\u0000.html','../menu.html','about/../menu.html','./menu.html','about/./team.html',
  'about//team.html','http://example.invalid/menu.html','//example.invalid/menu.html','file:menu.html','menu.html?x=1','menu.html#top','.hidden.html',
  'about/.secret.html','.well-known/','menu','menu.htm','menu.HTML','menu.html.bak','style.css','about','about/team','%2e%2e/menu.html','men u.html',
  'ménu.html','menu.html\n','__ods_view__.html','__pycache__/x.html','-menu.html','x'.repeat(124)+'.html','a/'.repeat(12)+'x.html','x/'.repeat(12),
  LONG+'x'.repeat(24)+'.html'];

test('page accepts published .html paths of the snapshot and refuses every other form',async()=>{
  for(const page of GOOD) assert.equal(normalize(params(page)).page,page,page);
  assert.equal(Object.hasOwn(normalize(params()),'page'),false,'the entry page is not written into the plan');
  let calls=0;
  const tool=createWorkspacePreviewInspectTool({request:async()=>{calls++;throw Error('must not execute');}});
  for(const page of [...BAD,null,undefined,1,true,['menu.html'],{path:'menu.html'}]) {
    assert.throws(()=>normalize({...params(),page}),/invalid preview inspection page/,JSON.stringify(page));
    const result=await tool.execute('bad-page',{...params(),page});
    assert.equal(result.details.errorCode,'invalid_request',JSON.stringify(page));
    assert.match(result.content[0].text,/page names one published \.html page of the same snapshot/);
    assert.match(result.content[0].text,/Omit page for the entry page/);
  }
  const marker='PRIVATE_PAGE_DO_NOT_REPEAT';
  assert.ok(!(await tool.execute('marker',{...params(),page:`../${marker}.html`})).content[0].text.includes(marker),'the value is never reflected');
  assert.equal(calls,0,'an invalid page never reaches the inspector');
  // Exactly one optional top-level field; nothing else is loosened.
  for(const extra of [{url:'http://localhost/menu.html'},{pages:['menu.html']},{path:'menu.html'}]) {
    assert.throws(()=>normalize({...params('menu.html'),...extra}),/invalid preview inspection fields/);
  }
  assert.equal(pageFile('about/'),'about/index.html');
  assert.equal(pageFile('menu.html'),'menu.html');
});

test('existing plans hash exactly as before and a page is part of the plan',()=>{
  // The plan of tests/test-preview-inspection.mjs, whose hash matches Python's canonical contract.
  const legacy={...preview,viewport:{width:375,height:812},steps:[{action:'assert-hidden',locator:{selector:'#card'}},
    {action:'click',locator:{role:'button',name:'Mostrar próximos eventos',exact:true}},visible('#card')]};
  assert.equal(inspectionPlanHash(normalize(legacy)),'156ae762e4eb8cbec17063823bfb5a61705c2917251da8cdff43af2c9ffbc06c');
  assert.deepEqual(Object.keys(normalize(legacy)).sort(),['action','schemaVersion','sha256','siteId','steps','viewport']);
  const paged=normalize({...legacy,page:'menu.html'});
  assert.notEqual(inspectionPlanHash(paged),inspectionPlanHash(normalize(legacy)));
  assert.notEqual(inspectionPlanHash(paged),inspectionPlanHash(normalize({...legacy,page:'contact.html'})));
});

// The capsule protocol in Python decides what the broker accepts and hashes.
function python(requests) {
  const script='import json,sys\nsys.path.insert(0,sys.argv[1])\nimport preview_inspection_protocol as p\n'+
    'out={"scope":p.PAGE_SCOPE,"capability":p.PAGE_CAPABILITY,"requests":[]}\n'+
    'for r in json.load(sys.stdin):\n'+
    '    try: p.validate_request(r); out["requests"].append({"valid":True,"hash":p.plan_hash(r),"scope":p.inspection_scope(r),'+
    '"file":p.page_file(r["page"]) if "page" in r else None,"failure":p.failure("page_not_found",r)})\n'+
    '    except p.Invalid: out["requests"].append({"valid":False})\n'+
    'print(json.dumps(out))\n';
  const result=spawnSync(process.platform==='win32'?'python':'python3',['-c',script,fileURLToPath(new URL('../host',import.meta.url))],
    {input:JSON.stringify(requests),encoding:'utf8',windowsHide:true});
  assert.equal(result.status,0,result.stderr);
  return JSON.parse(result.stdout);
}

test('the plugin mirrors the Python page rules, scope, failure receipt and plan hash',()=>{
  const samples=[params(),params(undefined,press),params('menu.html',press),...GOOD.map(page=>params(page)),...BAD.map(page=>params(page)),
    {...params(),page:null},{...params(),page:7}];
  const py=python(samples.map(sample=>({schemaVersion:1,action:'inspect',...sample})));
  assert.equal(py.scope,PAGE_INSPECTION_SCOPE);
  assert.equal(py.capability,'snapshot-page-v1');
  samples.forEach((sample,i)=>{
    let request;
    try { request=normalize(sample); } catch { request=undefined; }
    assert.equal(py.requests[i].valid,request!==undefined,JSON.stringify(sample.page));
    if(!request) return;
    assert.equal(py.requests[i].hash,inspectionPlanHash(request));
    assert.equal(py.requests[i].scope,validate(failure(request,'unavailable'),request).scope);
    assert.equal(py.requests[i].file,request.page===undefined?null:pageFile(request.page));
    // The host's bound failure for a missing page validates here only for a page plan.
    if(request.page!==undefined) assert.deepEqual(validate(py.requests[i].failure,request),py.requests[i].failure);
    else assert.throws(()=>validate(py.requests[i].failure,request));
  });
});

test('the schema offers page as one optional bounded string, and the description says links are not followed',()=>{
  const {parameters,description}=createWorkspacePreviewInspectTool({request:async()=>{}});
  assert.deepEqual(parameters.required,['siteId','sha256','viewport','steps']);
  const {page}=parameters.properties;
  assert.deepEqual([page.type,page.minLength,page.maxLength,page.pattern],['string',1,512,undefined]);
  assert.match(page.description,/Omit for the entry page/);
  assert.match(description,/It opens the entry page; to check another page of the same snapshot, pass page: its published \.html path from the site root/);
  assert.match(description,/Links between pages are not followed: inspect each page with its own call\./);
});

test('every receipt of a page plan names that page, and an entry-page receipt never does',()=>{
  const request=normalize(params('menu.html',visible('#specials'))),good=receipt(request);
  assert.equal(validate(good,request).page,'menu.html');
  const entry=normalize(params(undefined,visible('#specials')));
  assert.equal(validate(receipt(entry),entry).page,undefined);
  for(const change of [r=>{delete r.page;},r=>r.page='contact.html',r=>r.page='menu.html/',r=>r.scope=INSPECTION_SCOPE,
    r=>r.scope='Only the listed CSS layout visibility assertions and click dispatches were tested; not pixel paint, occlusion, clipping, a full accessibility audit, or overall functionality.']) {
    const forged=structuredClone(good);change(forged);
    assert.throws(()=>validate(forged,request),undefined,JSON.stringify(forged));
  }
  assert.throws(()=>validate({...receipt(entry),page:'index.html'},entry),'an entry-page receipt names no page');
  assert.throws(()=>validate(receipt(request),entry),'a page receipt never stands for the entry page');
  // Bound failures name the page too; page_not_found exists only for a page plan.
  for(const code of ['page_not_found','unavailable','unsupported_capability','request_blocked']) assert.equal(validate(failure(request,code),request).errorCode,code);
  const unnamed=failure(request,'unavailable');delete unnamed.page;
  assert.throws(()=>validate(unnamed,request));
  assert.throws(()=>validate(failure(entry,'page_not_found'),entry));
  assert.throws(()=>validate({...failure(entry,'unavailable'),page:'menu.html'},entry));
});

test('a page that names no file of the snapshot is reported as not found, never unavailable or passed',async()=>{
  const result=await run(params('menu.html'),request=>failure(request,'page_not_found'));
  assert.equal(result.isError,true);
  assert.equal(result.details.errorCode,'page_not_found');
  assert.equal(result.details.page,'menu.html');
  const text=result.content[0].text;
  assert.match(text,/^Preview inspection of page "menu\.html" failed\. The requested page is not a file of this published snapshot, so nothing was loaded and no step ran\./);
  assert.match(text,/copy it from the publication receipt's published paths, or omit page for the entry page/);
  assert.match(text,/add it, republish and inspect the new snapshot/);
  assert.match(text,/Requested behavior remains unverified/);
  assert.doesNotMatch(text,/unavailable or invalid|guard blocked/);
});

test('an inspector without page support says so and points to the entry page',async()=>{
  const result=await run(params('menu.html',press),request=>failure(request,'unsupported_capability'));
  assert.equal(result.details.errorCode,'unsupported_capability');
  assert.match(result.content[0].text,/cannot start on another page \(page\)\. Update the ODS inspector and its capsule together/);
  assert.match(result.content[0].text,/omit page for that page, keep the published pages unchanged and report the other pages as unverified/);
  const entry=await run(params(undefined,press),request=>failure(request,'unsupported_capability'));
  assert.match(entry.content[0].text,/does not support trusted key presses/,'entry-page plans keep their own message');
  assert.doesNotMatch(entry.content[0].text,/another page/);
});

test('a passing page plan names its page and counts as browser evidence for the snapshot',async()=>{
  const steps=[{action:'assert-hidden',locator:{selector:'#specials'}},{action:'click',locator:{selector:'#show'}},visible('#specials')];
  const plan=params('menu.html',...steps);
  const result=await run(plan,request=>receipt(request,{controls:CONTROLS}));
  assert.equal(result.isError,undefined);
  assert.equal(result.details.page,'menu.html');
  assert.match(result.content[0].text,/^Preview inspection of page "menu\.html" passed\. These steps tested opposite visibility states/);
  assert.ok(result.content[0].text.includes(PAGE_INSPECTION_SCOPE));
  assert.match(result.content[0].text,/"page":"menu\.html"/,'the evidence copy names the page');
  assert.ok(boundRenderedPreviewInspection(plan,result,preview));
  assert.deepEqual(boundVisibilityInspection(plan,result,preview),{...preview,planSha256:result.details.planSha256});
  // Load-time control names of another page cannot show or rule out the
  // owner's control on the entry page; the entry page's own still count.
  assert.equal(boundInspectionControls(plan,result,preview),undefined);
  const entryPlan=params(undefined,...steps);
  const entry=await run(entryPlan,request=>receipt(request,{controls:CONTROLS}));
  assert.match(entry.content[0].text,/^Preview inspection passed\./,'the entry page keeps its wording');
  assert.deepEqual(boundInspectionControls(entryPlan,entry,preview).controls,CONTROLS);
});

test('a click-through to another page stays a blocked navigation',async()=>{
  const plan=params('menu.html',{action:'click',locator:{selector:'#to-contact'}},visible('h1'));
  const result=await run(plan,request=>({...receipt(request),status:'failed',blockedRequests:['navigation'],
    steps:[{index:0,...request.steps[0],before:state(true),stable:true,status:'failed',errorCode:'click_failed'}]}));
  assert.equal(result.isError,true);
  assert.match(result.content[0].text,/^Preview inspection of page "menu\.html" failed\. The inspection guard blocked a page operation\./);
  assert.match(result.content[0].text,/The inspector never follows a link to another page: inspect that page with page instead of clicking the link\./);
  assert.match(result.content[0].text,/Do not broaden network access/);
  assert.equal(boundRenderedPreviewInspection(plan,result,preview),undefined);
});

test('an untested show/hide correction stays on the inspected page and ignores the entry-page outline',()=>{
  // The entry page's outline: its .card has the id specials.
  const outline={elements:[{tag:'section',parent:-1,id:'specials',classes:['card','hidden']}],stateClasses:['hidden']};
  const steps=[{action:'assert-hidden',locator:{selector:'.card.hidden'}},{action:'click',locator:{selector:'#show'}}];
  const entry=transitionCorrection(normalize(params(undefined,...steps)),{outline,initiallyHidden:true});
  assert.equal(entry.basis,'id');
  assert.equal(entry.args.page,undefined);
  assert.deepEqual(entry.args.steps[0].locator,{selector:'#specials'});
  const paged=transitionCorrection(normalize(params('menu.html',...steps)),{outline,initiallyHidden:true});
  assert.equal(paged.basis,'unverified');
  assert.equal(paged.args.page,'menu.html');
  assert.deepEqual(paged.args.steps.map(step=>[step.action,step.locator]),
    [['assert-hidden',{selector:'.card'}],['click',{selector:'#show'}],['assert-visible',{selector:'.card'}]]);
  assert.equal(normalize(paged.args).page,'menu.html','the ready-to-send args are a valid page plan');
});
