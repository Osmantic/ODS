import test from 'node:test';
import assert from 'node:assert/strict';
import {displayForActivity} from '../plugin/activity-display.mjs';

test('deferred project jobs are identifiable without exposing raw output', () => {
  const result = displayForActivity({params:{id:'openclaw:pixel-ods:pixel_ods_project_build',
    args:{action:'submit',project:'Playground/library'}},result:{text:'secret output'}}, {toolName:'tool_call'});
  assert.equal(result.label,'Starting project tests and build');
  assert.equal(result.detail,'Playground/library');
  assert.equal(JSON.stringify(result).includes('secret output'),false);
});

test('cancellation activity reports a request, not proof of a stopped job', () => {
  const result = displayForActivity({params:{action:'cancel',jobId:'ods-project-'+'a'.repeat(24)}},
    {toolName:'pixel_ods_project_build'});
  assert.equal(result.label,'Requesting build cancellation');
  assert.equal(result.detail,null);
});

test('runtime metadata does not appear as a build or permission change', () => {
  const result=displayForActivity({params:{action:'capabilities',runtime:'python'}},{toolName:'pixel_ods_project_build'});
  assert.equal(result.label,'Checking project runtime compatibility');
  assert.equal(result.detail,null);
});
