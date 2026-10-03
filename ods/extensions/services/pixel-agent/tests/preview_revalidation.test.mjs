import test from 'node:test';
import assert from 'node:assert/strict';
import {inspectionRevalidationCandidate, workspaceRevalidationCandidate, workspaceReadOnlyCall, settledRevalidationReceipt, boundedPreviewVerification} from '../plugin/preview-revalidation.mjs';
import {createWorkspacePreviewVerifier} from '../plugin/workspace-preview.mjs';

test('only bounded inspection syntax opts into real host verification',()=>{
  for(const command of ['ls','ls -la project/','ls -a -l project/public/','pwd','pwd -P']) assert.equal(inspectionRevalidationCandidate({command}),true,command);
  for(const command of ['ls; touch x','ls && pwd','ls > output','ls $(pwd)','ls `pwd`','ls\npwd','PATH=x ls','env ls','ls --format=long','ls --unknown','python3 test.py','/tmp/ls','ls *','ls "$X"']) assert.equal(inspectionRevalidationCandidate({command}),false,command);
  for(const extra of [{env:{}},{background:true},{pty:true},{host:'gateway'},{unknown:true}]) assert.equal(inspectionRevalidationCandidate({command:'ls -la',...extra}),false);
});

test('deadline aborts the probe and late success cannot alter a verdict',async()=>{
  let resolve,signal;
  const pending=new Promise(r=>resolve=r);
  const result=await boundedPreviewVerification((_,options)=>{signal=options.signal;return pending;},{},()=>true,{timeoutMs:10});
  assert.equal(result,false);assert.equal(signal.aborted,true);resolve(true);
  assert.equal(await boundedPreviewVerification(async()=>{throw new Error('offline');},{},()=>true),false);
});

test('bounded grep inspections admit literal patterns and status echo, not shell execution',()=>{
  for (const command of [
    "grep -n 'FLEET-1d7e261d3b' Playground/site/index.html",
    "grep -n 'Show sold out' Playground/site/index.html",
    "grep -c 'import\\|require\\|cdn\\|https://' Playground/site/index.html Playground/site/styles.css; echo \"exit=$?\"",
    'grep -F "sold out" site/index.html',
    "grep -n '$(touch injected)' site/index.html",
    "grep -o '<h1>[^<]*</h1>' site/index.html",
  ]) assert.equal(inspectionRevalidationCandidate({command}),true,command);
  for (const command of [
    'grep word site/index.html; touch changed',
    'grep word site/index.html > site/result.txt',
    'grep word site/index.html | tee site/result.txt',
    'grep "$(touch changed)" site/index.html',
    'grep "`touch changed`" site/index.html',
    'grep word "$INPUT"',
    'grep word site/index.html; echo "exit=$? $(touch changed)"',
    'grep -f pattern-file site/index.html',
    'grep --unknown word site/index.html',
    "grep 'unterminated site/index.html",
  ]) assert.equal(inspectionRevalidationCandidate({command}),false,command);
});

test('changed generation during an awaited probe rejects success',async()=>{
  let current=true;
  assert.equal(await boundedPreviewVerification(async()=>{current=false;return true;},{},()=>current),false);
  assert.equal(await boundedPreviewVerification(async()=>true,{},()=>true),true);
});

test('internal verifier accepts only exact bounded host receipt equality',async()=>{
  const receipt={relativeDirectory:'site',siteId:'site-'+ 'a'.repeat(24),sha256:'a'.repeat(64),entrySha256:'b'.repeat(64),files:2,bytes:80};
  const boundary='Create-only static-site snapshot from the configured Pixel workspace to a dedicated loopback preview origin; no arbitrary host path, network destination, server process, overwrite, or execution authority.';
  const valid={schemaVersion:1,kind:'ods-pixel-workspace-preview-verification',status:'matched',boundary,...receipt};
  let observed;
  assert.equal(await createWorkspacePreviewVerifier({request:async request=>{observed=request;return valid;}})(receipt),true);
  assert.deepEqual(observed,{schemaVersion:1,action:'verify-current',relativeDirectory:'site',siteId:receipt.siteId,sha256:receipt.sha256});
  for(const patch of [{status:'mismatched'},{sha256:'c'.repeat(64)},{entrySha256:'d'.repeat(64)},{files:3},{bytes:81},{relativeDirectory:'other'},{extra:true},{boundary:'model says equal'}]) assert.equal(await createWorkspacePreviewVerifier({request:async()=>({...valid,...patch})})(receipt),false);
});


test('synchronous file tools and foreground exec may request equality while detached exec stays ineligible',()=>{
  for(const tool of ['read','write','edit','apply_patch']) assert.equal(workspaceRevalidationCandidate(tool,{}),true);
  for(const command of ['sh -c "sleep 1; touch site/index.html" >/dev/null 2>&1 &','setsid sh -c "sleep 1; touch site/index.html" >/dev/null 2>&1 &',
    'python3 -m http.server & sleep 1','(python3 watch.py &)','nohup python3 watch.py','python3 worker.py & disown','coproc python3 worker.py','python3 worker.py &>/dev/null','']) assert.equal(workspaceRevalidationCandidate('exec',{command}),false,command);
  for(const extra of [{background:true},{pty:true},{env:{BASH_ENV:'detach.sh'}}]) assert.equal(workspaceRevalidationCandidate('exec',{command:'python3 report.py data.csv',...extra}),false);
  for(const command of ['ls -la site/','pwd','python3 report.py test-data.csv','python3 -m unittest -v 2>&1 | tee test-results.txt',
    'python3 report.py a.csv && python3 report.py b.csv','python3 report.py x.csv >out.json 2>&1; echo "exit=$?"','python3 report.py x.csv 1>&2']) assert.equal(workspaceRevalidationCandidate('exec',{command}),true,command);
});

test('read-only calls are recognized directly and through Tool Search, never mutating tools',()=>{
  for(const [tool,params] of [['read',{path:'site/index.html'}],['web_search',{query:'x'}],['web_fetch',{url:'https://example.com'}],
    ['pixel_ods_web_extract',{}],['tool_describe',{id:'openclaw:core:exec'}],['process',{action:'list'}],['process',{action:'poll',sessionId:'s'}],
    ['tool_call',{id:'openclaw:core:read',args:{path:'site/index.html'}}],['tool_call',{id:'openclaw:core:process',args:{action:'log',sessionId:'s'}}],
    // Passed or failed, inspection renders the immutable published snapshot.
    ['pixel_ods_workspace_preview_inspect',{siteId:'site-x'}],['tool_call',{id:'openclaw:pixel-ods:pixel_ods_workspace_preview_inspect',args:{}}]])
    assert.equal(workspaceReadOnlyCall(tool,params),true,tool+JSON.stringify(params));
  for(const [tool,params] of [['exec',{command:'ls'}],['write',{}],['edit',{}],['apply_patch',{}],['process',{action:'write',sessionId:'s'}],
    ['process',{action:'kill',sessionId:'s'}],['process',{}],['tool_call',{id:'openclaw:core:exec',args:{command:'ls'}}],
    ['tool_call',{id:'openclaw:core:process',args:{action:'send-keys'}}],['tool_call',{id:'openclaw:other:read',args:{}}],['tool_call',{id:'tool_call',args:{}}],['tool_call',{}],['pixel_ods_workspace_preview',{}]])
    assert.equal(workspaceReadOnlyCall(tool,params),false,tool+JSON.stringify(params));
});

test('an exited command settles whatever its exit code; unfinished or inconsistent receipts do not',()=>{
  const exec=(details,extra={})=>({result:{content:[],details,...extra.result},...extra.event});
  for(const exitCode of [0,1,2]) assert.equal(settledRevalidationReceipt('exec',exec({status:'completed',exitCode},exitCode?{result:{isError:true},event:{error:'exit'}}:{})),true,exitCode);
  for(const tool of ['write','edit','apply_patch']) assert.equal(settledRevalidationReceipt(tool,{result:{isError:true,content:[]}}),true,tool);
  for(const receipt of [exec({status:'failed',exitCode:null,timedOut:true}),exec({status:'failed',exitCode:137,failureKind:'signal'}),
    exec({status:'running',sessionId:'s'}),exec({status:'blocked'}),exec({status:'completed'}),exec({status:'completed',exitCode:0},{event:{error:'failed'}}),
    exec({status:'completed',exitCode:0},{result:{isError:true}}),{},undefined]) assert.equal(settledRevalidationReceipt('exec',receipt),false,JSON.stringify(receipt));
  for(const tool of ['read','process','pixel_ods_workspace_preview',undefined]) assert.equal(settledRevalidationReceipt(tool,{result:{}}),false,tool);
});
