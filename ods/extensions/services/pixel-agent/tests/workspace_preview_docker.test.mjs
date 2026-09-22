import test from 'node:test';
import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import {syncBuiltinESMExports} from 'node:module';
import {dockerWorkspacePreviewRequest} from '../plugin/workspace-preview-docker.mjs';

test('native preview uses only the configured executable and fixed arguments', {skip:process.platform!=='darwin'}, async t => {
  const before=process.env.PIXEL_PREVIEW_DOCKER;
  const calls=[];
  t.mock.method(childProcess,'execFile',(file,args,options,callback)=>{
    calls.push({file,args,options});
    queueMicrotask(()=>callback(null,'{"schemaVersion":1}\n'));
    return {stdin:{on(){},end(body){calls.at(-1).body=body;}}};
  });
  syncBuiltinESMExports();
  try {
    const payload={schemaVersion:1,action:'publish',relativeDirectory:'projects/test'};
    process.env.PIXEL_PREVIEW_DOCKER='/opt/Custom Docker/bin/docker';
    assert.deepEqual(await dockerWorkspacePreviewRequest(payload),{schemaVersion:1});
    assert.equal(calls[0].file,process.env.PIXEL_PREVIEW_DOCKER);
    assert.deepEqual(calls[0].args,['exec','-i','ods-pixel-workspace-preview','python3','/source/workspace_preview.py','request']);
    assert.equal(calls[0].body,JSON.stringify(payload));
    assert.equal(calls[0].options.shell,undefined);
    for(const invalid of ['', 'docker', '/', '/tmp/../docker', '/tmp//docker', '/tmp/docker\n']) {
      process.env.PIXEL_PREVIEW_DOCKER=invalid;
      await assert.rejects(dockerWorkspacePreviewRequest(payload),/invalid native preview/);
    }
    assert.equal(calls.length,1);
    delete process.env.PIXEL_PREVIEW_DOCKER;
    await dockerWorkspacePreviewRequest(payload);
    assert.equal(calls[1].file,'/Applications/Docker.app/Contents/Resources/bin/docker');
  } finally {
    if(before===undefined) delete process.env.PIXEL_PREVIEW_DOCKER;
    else process.env.PIXEL_PREVIEW_DOCKER=before;
    t.mock.restoreAll();
    syncBuiltinESMExports();
  }
});
