import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn, spawnSync} from 'node:child_process';
import {mkdtemp, mkdir, writeFile, readFile, rm, realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {get} from 'node:http';
import {socketRequest} from '../plugin/workspace-preview.mjs';
import {ARTIFACT_TOOL, createWorkspaceArtifactAdmission, createWorkspaceArtifactTool} from '../plugin/workspace-artifact.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

const hostModule=process.env.ODS_ARCHIVE_TEST_HOST || fileURLToPath(new URL('../host/workspace_preview.py',import.meta.url));
const python=process.env.PYTHON || 'python3';
// Use the production peer authorization, control parser, archive publisher and
// HTTP handler. Only listener lifetime and temporary paths belong to this fixture.
const helper=String.raw`
import importlib.util,json,os,socket,sys,threading
from pathlib import Path
spec=importlib.util.spec_from_file_location('preview',sys.argv[1])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
root=Path(sys.argv[2]);workspace=root/'workspace';previews=root/'previews'
server=m.PreviewHTTPServer(('127.0.0.1',0),previews)
threading.Thread(target=server.serve_forever,daemon=True).start()
listener=socket.socket(socket.AF_UNIX);listener.bind(str(root/'control.sock'));listener.listen(4)
print(json.dumps({'port':server.server_port}),flush=True)
try:
 while True:
  peer,_=listener.accept()
  with peer:
   m._serve_connection(peer,workspace=workspace,previews=previews,owner_uid=os.getuid(),port=server.server_port)
finally:
 listener.close();server.shutdown();server.server_close()
`;

async function fixture(t) {
 const parent=await realpath(tmpdir()),root=await mkdtemp(path.join(parent,'ods-archive-'));
 let child,exited;
 t.after(async()=>{
  if(child && child.exitCode===null && child.signalCode===null) {
   child.kill('SIGTERM');const timer=setTimeout(()=>child.kill('SIGKILL'),3000);
   try {await exited;} finally {clearTimeout(timer);}
  }
  assert.equal(path.dirname(root),parent);assert.ok(path.basename(root).startsWith('ods-archive-'));
  await rm(root,{recursive:true,force:true});
 });
 for(const dir of ['workspace','workspace/project','workspace/project/src','workspace/project/__pycache__','previews'])
  await mkdir(path.join(root,dir),{mode:0o700});
 child=spawn(python,['-u','-c',helper,hostModule,root],{stdio:['ignore','pipe','pipe']});
 exited=new Promise(resolve=>{child.once('exit',resolve);child.once('error',resolve);});
 const ready=await new Promise((resolve,reject)=>{
  let output='',error='';const timer=setTimeout(()=>reject(new Error('broker fixture readiness timeout')),5000);
  const stop=error=>{clearTimeout(timer);reject(error);};
  child.once('error',stop);child.once('exit',()=>stop(new Error('broker fixture exited: '+error)));
  child.stderr.on('data',chunk=>{error=(error+chunk).slice(-4096);});
  child.stdout.on('data',chunk=>{
   output+=chunk;if(output.length>8192)return stop(new Error('unexpected fixture output'));
   if(output.includes('\n'))try{const value=JSON.parse(output.split('\n')[0]);clearTimeout(timer);resolve(value);}catch(e){stop(e);}
  });
 });
 return {root,port:ready.port,socketPath:path.join(root,'control.sock')};
}

function download(port,receipt) {
 return new Promise((resolve,reject)=>{
  const request=get({host:'127.0.0.1',port,path:`/${receipt.siteId}/${receipt.file.path}`,
   headers:{Host:`${receipt.siteId}.localhost:${port}`}},response=>{
    const chunks=[];let bytes=0;
    response.on('data',chunk=>{bytes+=chunk.length;if(bytes>4*1024*1024)request.destroy(new Error('oversized download'));else chunks.push(chunk);});
    response.on('error',reject);response.on('end',()=>resolve({status:response.statusCode,headers:response.headers,body:Buffer.concat(chunks)}));
   });
  request.setTimeout(5000,()=>request.destroy(new Error('download timeout')));request.on('error',reject);
 });
}

test('admitted archive traverses actual Unix broker, HTTP readback and download without workspace changes',
 {skip:process.platform==='win32' && 'POSIX broker runs in Linux/WSL or macOS',timeout:20000},async t=>{
 const {root,port,socketPath}=await fixture(t),workspace=path.join(root,'workspace');
 const source={'project/src/main.py':'print("café")\r\n','project/test_main.py':'import unittest\n','project/README.md':'# Project\r\n'};
 for(const [name,body] of Object.entries({...source,'project/__pycache__/main.pyc':'generated cache','download.zip':'untouched output sentinel'}))
  await writeFile(path.join(workspace,name),body,{mode:0o600});
 const context={trigger:'user',agentId:'pixel',runId:'archive-run',sessionId:'archive-session',
  sessionKey:'agent:pixel:openai-user:ods-'+'a'.repeat(64),toolCallId:'artifact'};
 const args={relativePath:'download.zip',sourcePath:'project'},guard=createToolLoopGuard(),admission=createWorkspaceArtifactAdmission();
 guard.observeRun(context,'pixel',{prompt:'Give me the downloadable project.'});
 admission.before({toolName:ARTIFACT_TOOL,params:args},context);
 let hostReceipt;
 const tool=createWorkspaceArtifactTool(context,{admission,reserve:s=>guard.reserveWorkspaceArtifact(s),
  accept:(s,r)=>guard.acceptWorkspaceArtifact(s,r),request:async(payload,options)=>{
   hostReceipt=await socketRequest(payload,{...options,socketPath,timeoutMs:5000});return hostReceipt;
  }});
 const result=await tool.execute(context.toolCallId,args);
 assert.equal(result.isError,undefined,JSON.stringify(result));
 assert.deepEqual(hostReceipt.archive,{sourcePath:'project',fileCount:3,sourceBytes:Object.values(source).reduce((n,v)=>n+Buffer.byteLength(v),0),omittedCacheEntries:1});
 assert.equal(result.details.archive,undefined);
 assert.deepEqual(guard.deliveryVerificationForRun(context.runId).artifacts,[result.details]);
 const actual=await download(port,result.details);
 assert.equal(actual.status,200);assert.equal(actual.body.length,result.details.file.bytes);
 assert.equal(createHash('sha256').update(actual.body).digest('hex'),result.details.file.sha256);
 assert.equal(actual.headers['x-preview-sha256'],result.details.file.sha256);
 const decoded=spawnSync(python,['-c','import io,json,sys,zipfile; z=zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())); assert z.testzip() is None; print(json.dumps({n:z.read(n).hex() for n in z.namelist()}))'],{input:actual.body,timeout:5000,maxBuffer:1024*1024});
 assert.ifError(decoded.error);assert.equal(decoded.status,0,decoded.stderr.toString());
 assert.deepEqual(JSON.parse(decoded.stdout.toString()),Object.fromEntries(Object.entries(source).map(([name,body])=>[name,Buffer.from(body).toString('hex')])));
 for(const [name,body] of Object.entries(source))assert.deepEqual(await readFile(path.join(workspace,name)),Buffer.from(body));
 assert.equal(await readFile(path.join(workspace,'project/__pycache__/main.pyc'),'utf8'),'generated cache');
 assert.equal(await readFile(path.join(workspace,'download.zip'),'utf8'),'untouched output sentinel');
 const again=await socketRequest({schemaVersion:1,action:'publish-artifact',...args},{socketPath,timeoutMs:5000});
 assert.equal(again.sha256,hostReceipt.sha256);
 // A real host refusal has a useful fixed diagnostic and never becomes a card.
 await writeFile(path.join(workspace,'project/credentials.txt'),'private test fixture',{mode:0o600});
 admission.before({toolName:ARTIFACT_TOOL,params:args},context);
 const refused=await tool.execute(context.toolCallId,args);
 assert.equal(refused.details.code,'archive_private_source');
 assert.match(refused.content[0].text,/do not remove or rewrite source/);
 assert.doesNotMatch(refused.content[0].text,/private test fixture|credentials\.txt/);
 assert.deepEqual(guard.deliveryVerificationForRun(context.runId).artifacts,[result.details]);
});
