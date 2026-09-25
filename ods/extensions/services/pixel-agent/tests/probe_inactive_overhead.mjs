// CPU-only diagnostic overhead; no HTTP, model, installed state or raw payload log.
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import crypto from 'node:crypto';import {performance} from 'node:perf_hooks';
import {createOwnedProbeFetch} from '../host/probe_fetch.mjs';
const root=fs.mkdtempSync(path.join(os.tmpdir(),'ods-probe-overhead-'));fs.chmodSync(root,0o700);
const source=fs.readFileSync(new URL('../host/probe_fetch.mjs',import.meta.url));
const base=async (_url,options)=>{if(new Headers(options.headers).has('x-ods-probe'))throw Error('unexpected capture');return 1};
const wrapped=createOwnedProbeFetch({agentId:'pixel',runId:'fixture-only'},base,{leasePath:path.join(root,'absent.json')});
const options={method:'POST',body:'{"model":"fixture-only","messages":[]}',headers:{'content-type':'application/json'}};
async function run(fn,count){const start=performance.now();for(let i=0;i<count;i++)await fn('http://127.0.0.1:1/v1/chat/completions',options);return (performance.now()-start)/count;}
try{
 await run(base,100);await run(wrapped,100);
 const rows=[];for(const order of [['baseline','wrapped'],['wrapped','baseline'],['baseline','wrapped'],['wrapped','baseline'],['wrapped','baseline'],['baseline','wrapped']]){const row={order,iterationsPerArm:1000};for(const arm of order)row[arm+'MsPerCall']=await run(arm==='baseline'?base:wrapped,1000);row.extraMsPerCall=row.wrappedMsPerCall-row.baselineMsPerCall;rows.push(row);}
 console.log(JSON.stringify({schema:'ods-probe-inactive-overhead.v1',sourceSha256:crypto.createHash('sha256').update(source).digest('hex'),node:process.version,platform:process.platform,condition:'no active lease, owner-private existing directory, warm filesystem, deterministic no-network fetch',rows,interpretation:'Wrapper-only CPU microbenchmark; not end-to-end model latency or production request overhead; both timed arms must use identical instrumentation.'}));
}finally{fs.rmSync(root,{recursive:true,force:true});}
