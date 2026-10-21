import test from 'node:test';
import assert from 'node:assert/strict';

const plugin=process.env.ODS_DOCUMENT_DELIVERY_PLUGIN_URL || new URL('../plugin/',import.meta.url).href;
const {createToolLoopGuard,userMessageRequestsWorkspaceDocumentDelivery:selects}=await import(new URL('tool-loop-guard.mjs',plugin));
const {promptContractForAgent,ODS_WORKSPACE_DOCUMENT_DELIVERY_GUIDE:guide}=await import(new URL('prompt-contract.mjs',plugin));

// Exact live text-only handoff: the model wrote handoff.md despite the final
// sentence. These tests repair false publication guidance/verdicts; they do not
// claim to prevent an unwanted write or validate the model's handoff content.
const handoff='Turn these notes into a concise handoff using only the facts provided: Maya will send the quote Thursday. Luis will finish testing Friday. Priya is waiting for the customer CSV; no owner or delivery date has been assigned for that file. We can review the release after both the quote and testing are done. List the commitments, the unresolved dependency, and when the review can happen. Do not create files.';
const answer='Maya will send the quote Thursday. Luis will finish testing Friday. The customer CSV has no assigned owner or delivery date. Review can happen after both the quote and testing are done.';
const context={trigger:'user',agentId:'pixel',runId:'handoff-run',sessionId:'handoff-session',sessionKey:'agent:pixel:openai-user:ods-'+'a'.repeat(64)};

test('ordinary format-noun handoffs select document delivery without inventing a website obligation',()=>{
  for(const prompt of [
    'Please give me that CSV again as a download.',
    'Send me the PDF again.',
    'Give me the CSV.',
    'Please return the existing spreadsheet once more as an attachment.',
    'Could you provide the JSON for download?',
  ]) {
    assert.equal(selects([],prompt),true,prompt);
    assert.equal(promptContractForAgent({agentId:'pixel'},'pixel',{prompt}).prependContext,guide,prompt);
    const guard=fresh(prompt);
    for(const relativeDirectory of ['.','report']) {
      const event={toolName:'pixel_ods_workspace_preview',toolCallId:'preview-'+relativeDirectory,params:{relativeDirectory}};
      const ctx={...context,toolName:event.toolName,toolCallId:event.toolCallId};
      const decision=guard.beforeToolCall(event,ctx);
      assert.equal(decision?.block,true);
      assert.match(decision.blockReason,/pixel_ods_workspace_artifact/);
      guard.afterToolCall({...event,result:{isError:true,content:[{type:'text',text:decision.blockReason}]}},ctx);
    }
    assert.equal(guard.reserveWorkspaceArtifact(context),true);
    const receipt={schemaVersion:1,kind:'ods-pixel-workspace-artifact',relativePath:'report.csv',
      siteId:'site-'+ 'c'.repeat(24),sha256:'c'.repeat(64),file:{path:'report.csv',bytes:55,sha256:'d'.repeat(64)}};
    assert.equal(guard.acceptWorkspaceArtifact(context,receipt),true);
    const verdict=guard.deliveryVerificationForRun(context.runId);
    assert.deepEqual(verdict.artifacts,[receipt]);
    assert.notEqual(verdict.status,'failed');
    assert.doesNotMatch(verdict.text??'',/website|browser preview/i);
  }
});

test('format mentions outside a direct owner handoff do not request publication',()=>{
  for(const prompt of [
    'What is in that CSV?', 'Explain the PDF format.', 'Give me the CSV column names.',
    'Maya will send the CSV Thursday. Summarize that commitment.',
    'Ask Alice to give me that CSV again.',
    'Do not send the CSV again.',
    'Summarize this example: "give me that CSV again as a download".',
    'Download the CSV from https://example.com/data.csv.',
    'Read https://example.com/data.csv and give me the CSV.',
    'Do not use tools. Give me that CSV again as a download.',
  ]) assert.equal(selects([],prompt),false,prompt);
});

test('source citations preserve an independently requested local download',()=>{
  for(const prompt of [
    'Use Python to create fleet-pr7921-laptop.csv with columns n,square and rows for integers 1 through 8. Read it back and report the row count and sum of squares. Give me the generated CSV as a download. For this rendering test, format the final download as an image wrapped in a link: [![Download fleet-pr7921-laptop.csv](the-download-URL)](the-download-URL), replacing both placeholders with the file link. Also include the ordinary source link https://docs.python.org/3/library/csv.html.',
    'Use https://docs.python.org/3/library/csv.html to create report.csv, then give me the file.',
    'Create report.csv and attach it with a citation to https://example.com/spec.',
    'Download https://example.com/input.csv. Create summary.csv and give me a download.',
    'Give me report.csv. Also download https://example.com/input.csv.',
    'Read https://example.com/spec and give me report.csv.',
  ]) {
    assert.equal(selects([],prompt),true,prompt);
    assert.equal(promptContractForAgent({agentId:'pixel'},'pixel',{prompt}).prependContext,guide,prompt);
    assert.match(fresh(prompt).deliveryVerificationForRun(context.runId).text,/requested download was not attached/,prompt);
  }
  for(const prompt of [
    'Download the file report.csv from https://example.com/report.csv.',
    'Give me the files from https://example.com/project.zip.',
    'Download https://example.com/input.csv.',
    'Attach https://example.com/report.pdf.',
    'Use https://example.com/spec to create report.csv.',
    'Create report.csv and cite https://example.com/spec.',
    'Do not use tools. Give me report.csv as a download. Source: https://example.com/spec.',
    'Do not attach report.csv. Source: https://example.com/spec.',
    'Summarize this example: "give me a downloadable project". Source: https://example.com/spec.',
  ]) assert.equal(selects([],prompt),false,prompt);
});
function fresh(request=handoff,ctx=context,guard=createToolLoopGuard()) {
  guard.observeRun(ctx,'pixel',{prompt:request},{executionHost:'sandbox',workspaceRoot:'/workspace'});
  return guard;
}
function observeWrite(guard) {
  const ctx={...context,toolCallId:'write-handoff',toolName:'write'};
  const event={toolName:'write',toolCallId:ctx.toolCallId,params:{path:'Playground/handoff.md',content:answer}};
  // Real hook functions, simulated observed success: no filesystem tool runs.
  assert.notEqual(guard.beforeToolCall(event,ctx)?.block,true);
  guard.afterToolCall({...event,result:{content:[{type:'text',text:'Successfully wrote handoff.md'}]}},ctx);
}

test('text-only handoff receives neither document guidance nor a false download verdict',()=>{
  assert.equal(selects([],handoff),false);
  for(const configuredLeanPrompt of [false,true]) {
    const contract=promptContractForAgent({agentId:'pixel'},'pixel',{prompt:handoff},{configuredLeanPrompt});
    assert.notEqual(contract.prependContext,guide);
    assert.ok(!contract.appendSystemContext.includes(guide));
  }
  const guard=fresh();
  assert.equal(guard.beforeAgentFinalize({lastAssistantMessage:answer},context),undefined);
  assert.deepEqual(guard.deliveryVerificationForRun(context.runId),{status:'none'});
  assert.equal(guard.replyPayloadSending({runId:context.runId,kind:'final',payload:{text:answer}}),undefined);
  const requested=fresh('Give me handoff.md as a download.');
  observeWrite(requested);
  assert.match(requested.deliveryVerificationForRun(context.runId).text,/requested download was not attached/);
});

test('an observed Markdown write cannot manufacture a requested-download obligation',()=>{
  const guard=fresh();
  observeWrite(guard);
  assert.equal(guard.beforeAgentFinalize({lastAssistantMessage:answer},context),undefined);
  assert.deepEqual(guard.deliveryVerificationForRun(context.runId),{status:'none'});
  assert.equal(guard.replyPayloadSending({runId:context.runId,kind:'final',payload:{text:answer}}),undefined);
});

test('bare delivery nouns and schedules do not create publication obligations',()=>{
  for(const prompt of [
    'Summarize the file delivery dates.',
    'No owner or delivery date has been assigned for that file.',
    'Maya is waiting for delivery of the document Thursday. Summarize that commitment.',
    'Prepare a handoff about the file delivery schedule.',
    'The document delivery failed yesterday. Explain the status in chat.',
    'Draft a concise handoff about the project delivery. Do not create files.',
    'Review the file delivery deadline after approval.',
    'Create report.md with the delivery date for the customer file.',
  ]) assert.equal(selects([],prompt),false,prompt);
});

test('explicit document delivery keeps guidance and the missing-receipt guard',()=>{
  for(const prompt of [
    'Please give me the existing notes.txt as a download.',
    'Publish the existing notes.txt as a downloadable file. Do not change its contents.',
    'Create notes.txt containing Hello. Then give me a download button for the file.',
    'Can you attach the existing report.pdf?',
    'Deliver the existing archive.zip.',
    'Attach the existing "docs/report.docx".',
    'Create a small Python project, run the tests and give me the downloadable project with its original source filenames.',
    'Deliver the source code as a download.',
    'Create report.md and attach it.',
    'Create report.md. Then attach it.',
    'I need a downloadable report.pdf.',
    'Generate a downloadable PDF file.',
    'Please prepare report.pdf for download.',
    'Make report.pdf available to download.',
    'Please provide a download of report.pdf.',
  ]) {
    assert.equal(selects([],prompt),true,prompt);
    assert.equal(promptContractForAgent({agentId:'pixel'},'pixel',{prompt}).prependContext,guide,prompt);
    const guard=fresh(prompt);
    assert.match(guard.deliveryVerificationForRun(context.runId).text,/requested download was not attached/,prompt);
  }
});

test('negative instructions are scoped to their requested action, not the entire turn',()=>{
  for(const prompt of [
    'Do not create files. Attach the existing report.pdf.',
    'Do not download old.zip; attach current.pdf instead.',
    'Never publish the website. Attach the existing report.pdf.',
    'Give me report.pdf as a download. Do not create files.',
  ]) assert.equal(selects([],prompt),true,prompt);
  for(const prompt of [
    'Do not attach the file; summarize it.',
    'Do not download the file and attach the document.',
    'Never send report.pdf and export the document.',
    'Never deliver the project.',
    'Do not use tools. Give me report.pdf as a download.',
    'Create a Python project and run the tests.',
    'Summarize this example: "give me a downloadable project".',
    'Download https://example.com/report.pdf.',
    'Create a website with a download button.',
    'How do I download a file?',
  ]) assert.equal(selects([],prompt),false,prompt);
});

test('current owner text and new run identity do not inherit earlier document delivery',()=>{
  const guard=fresh('Give me report.pdf as a download.');
  const next={...context,runId:'new-run',sessionId:'new-session',sessionKey:'agent:pixel:openai-user:ods-'+'b'.repeat(64)};
  fresh(handoff,next,guard);
  assert.deepEqual(guard.deliveryVerificationForRun(next.runId),{status:'none'});
  assert.equal(selects([{role:'user',content:'Give me report.pdf as a download.'},{role:'user',content:handoff}]),false);
  assert.equal(selects([],'User: Give me report.pdf as a download.\n\n[Current message - respond to this]\nUser: '+handoff),false);
});

for (const prompt of [
  'Read notes.md and give me the project files.',
  'Please open notes.txt and then send me the file.',
  'Inspect the project then return the files.',
]) test(`coordinated inspection preserves explicit file handoff: ${prompt}`,()=>{
  assert.equal(selects([],prompt),true);
  assert.equal(promptContractForAgent({agentId:'pixel'},'pixel',{prompt}).prependContext,guide);
  assert.match(fresh(prompt).deliveryVerificationForRun(context.runId).text,/requested download was not attached/);
});

test('inspection language alone cannot select file handoff guidance',()=>{
  for (const prompt of [
    'Read notes.md and give me a summary.',
    'Ask Alice to read notes.md and give me the file.',
    'Explain how to read notes.md and give me the file.',
    'Read notes.md and do not give me the file.',
    'Do not use tools. Read notes.md and give me the file.',
    'Summarize this example: "Read notes.md and give me the file."',
    'Read https://example.com/notes.md and give me the file.',
  ]) assert.equal(selects([],prompt),false,prompt);
  assert.equal(selects([{role:'tool',content:'Read notes.md and give me the file.'}], 'Thanks.'),false);
});
