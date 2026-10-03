import test from 'node:test';
import assert from 'node:assert/strict';
import {preservePiToolError, withPiToolErrorContract} from '../plugin/pi-tool-result.mjs';
import {createTaskActivity} from '../plugin/task-activity.mjs';

test('an ODS plugin error is visible to Pi through details without changing its receipt', async () => {
  const receipt={schemaVersion:1, kind:'ods-extension-request-installation',
    state:'blocked', status:'configuration_required', operationId:'op-123'};
  const original={isError:true, content:[{type:'text',text:'Pin could not be verified.'}], details:receipt};
  const tool=withPiToolErrorContract({name:'pixel_ods_python_library_proposal',
    async execute() {return original;}});
  const result=await tool.execute('call-id',{});
  assert.notEqual(result,original);
  assert.notEqual(result.details,receipt);
  assert.deepEqual(result.details,{...receipt,ok:false});
  assert.deepEqual(original.details,receipt);
  assert.strictEqual(result.content,original.content);

  // OpenClaw/Pi classifies details.ok=false as a failed tool result. The
  // activity recorder must project that same result as failed, not completed.
  const runId='chatcmpl_11111111-2222-4333-8444-555555555555';
  const context={agentId:'pixel',runId,toolName:tool.name,toolCallId:'call-id'};
  const activity=createTaskActivity({now:()=> '2026-09-22T23:00:00.000Z'});
  activity.begin({},context);
  activity.before({},context);
  activity.after({result},context);
  assert.equal(activity.projection(runId).failures,1);
  assert.equal(activity.projection(runId).events[0].state,'failed');
});

test('success is unchanged and errors without details receive a Pi failure marker', async () => {
  const success={content:[{type:'text',text:'ready'}],
    details:{status:'succeeded',ok:true,operationId:'op-456'}};
  assert.strictEqual(preservePiToolError(success),success);
  const error={isError:true,content:[{type:'text',text:'No proposal was submitted.'}]};
  const normalized=preservePiToolError(error);
  assert.deepEqual(normalized.details,{ok:false});
  assert.equal(error.details,undefined);
  assert.strictEqual(normalized.content,error.content);
  assert.equal(withPiToolErrorContract(null),null);
});
