import test from 'node:test';
import assert from 'node:assert/strict';
import net from 'node:net';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {registerProjectBuild} from '../plugin/project-registration.mjs';
import {createProjectRunControl} from '../plugin/project-run-control.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

test('registered discovered tool and owner Stop share a controller job through the real socket transport',
  {skip:process.platform==='win32'}, async () => {
    const root=await mkdtemp(join(tmpdir(),'ods-stop-')), socketPath=join(root,'s');
    const jobId=`ods-project-${'c'.repeat(24)}`, user=`ods-${'c'.repeat(64)}`;
    const context={agentId:'pixel',sessionKey:`agent:pixel:openai-user:${user}`,sessionId:'session',runId:'run'};
    const messages=[];
    const server=net.createServer(socket=>{
      socket.once('data',data=>{
        const message=JSON.parse(data); messages.push(message);
        socket.end(JSON.stringify({schemaVersion:1,kind:'ods-project-job',jobId,project:'demo',
          status:message.request.action==='submit'?'running':'cancelled',
          cancelRequested:message.request.action!=='submit',steps:[],output:null})+'\n');
      });
    });
    await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(socketPath,resolve);});
    try {
      const control=createProjectRunControl(); let factory;
      registerProjectBuild({pluginConfig:{projectBuildSocket:socketPath},on(){},registerTool(value){factory=value;}},x=>x,control);
      const guard=createToolLoopGuard({abortRunAndDrain:async()=>({aborted:true,drained:true}),cancelProjectRun:scope=>control.cancel(scope)});
      guard.observeRun(context,'pixel',{prompt:'Build demo.'});
      const params={action:'submit',project:'demo',outputDirectory:'dist'};
      control.before({toolName:'tool_call',params:{id:'pixel_ods_project_build',args:params}}, {...context,toolCallId:'parent'});
      const result=await factory(context).execute('tool_search_code:parent:pixel_ods_project_build:1',params);
      assert.equal(result.details.status,'running');
      control.after({}, {...context,toolCallId:'parent'});
      assert.equal(await guard.abortUserRun(user),true);
      assert.deepEqual(messages.map(m=>m.request.action),['submit','cancel']);
      assert.equal(messages[1].request.jobId,jobId);
      assert.ok(messages.every(m=>m.context.sessionId===context.sessionKey));
    } finally {
      await new Promise(resolve=>server.close(resolve));
      await rm(root,{recursive:true,force:true});
    }
  });
