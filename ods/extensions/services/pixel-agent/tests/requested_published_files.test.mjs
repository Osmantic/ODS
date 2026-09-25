// Replays tower2 round 082 coding-v1 (Qwen3-Coder-Next, ODS main 771ee3b4):
// the owner asked for a public directory with index.html, test-results.txt,
// sources.json and three .py.txt copies, published alone. The model wrote
// test-results.txt into the project root, published public without it, and
// answered that the public directory contained it. The fleet check failed with
// "Published coding artifact missing test-results.txt".
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import * as fs from 'node:fs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {extractRequestedLiterals, requestedTextCheck, requestedTextInstruction, requestedTextRevisionInstruction,
  requestedTextDeliveryNote, REQUESTED_FILE_REVISION_INSTRUCTION, PREVIEW_FILE_SUFFIXES} from '../plugin/requested-literals.mjs';

const TOWER2 = JSON.parse(fs.readFileSync(new URL('./requested-files-tower2-round082.json', import.meta.url), 'utf8'));
const RECEIPT = TOWER2.publicationReceipt;
const DIRECTORY = RECEIPT.relativeDirectory;
const LISTED = ['index.html', 'test-results.txt', 'sources.json', 'report.py.txt', 'totals.py.txt', 'test_totals.py.txt'];
const NOTE = 'Requested files are not in the published directory "fleet-qualification-1d43828fc26e-coding/public": ' +
  '["test-results.txt"]. Put each one inside that directory, then republish it.';
const REVISION = 'Requested files are still missing from the published directory: ["test-results.txt"]. ' +
  'Put each file inside the directory you published (copy an existing file there with one short command instead of retyping it), ' +
  'republish that directory with pixel_ods_workspace_preview, and keep everything else unchanged.';
const FAILURE_NOTE = 'The published directory does not contain files the owner requested: "test-results.txt". ' +
  'The preview is available, but that requirement is not met.';

// A host receipt for the same directory with a different published path list.
function republished(paths, receipt = RECEIPT) {
  const sorted = [...paths].sort();
  const sha256 = createHash('sha256').update(JSON.stringify([receipt.relativeDirectory, sorted])).digest('hex');
  const siteId = `site-${sha256.slice(0, 24)}`;
  return {...receipt, sha256, siteId, url: `http://${siteId}.localhost:${receipt.port}/${siteId}/`,
    files: sorted.length, bytes: receipt.bytes + 3656 * (sorted.length - receipt.files), publishedPaths: sorted};
}

const literals = extractRequestedLiterals(TOWER2.prompt);

test('round 082 prompt: only the files listed for the public directory are required, bound to that directory', () => {
  assert.deepEqual(literals.map(literal => ({...literal})),
    LISTED.map(text => ({text, match: 'file', targets: [], directory: 'public'})));
  // The three executed sources and ".py itself" stay outside the publication.
  for (const source of ['report.py', 'totals.py', 'test_totals.py', '.py', 'INPUT.csv']) {
    assert.ok(!literals.some(literal => literal.text === source), source);
  }
});

test('round 082 receipt: test-results.txt is reported missing from the published directory', () => {
  assert.deepEqual(RECEIPT.publishedPaths, ['index.html', 'report.py.txt', 'sources.json', 'test_totals.py.txt', 'totals.py.txt']);
  const check = requestedTextCheck(literals, RECEIPT, {receipt: RECEIPT});
  assert.deepEqual({...check, missing: [...check.missing]},
    {siteId: RECEIPT.siteId, sha256: RECEIPT.sha256, missing: [{text: 'test-results.txt', file: true}]});
  assert.equal(requestedTextInstruction(RECEIPT, check), NOTE);
  assert.equal(requestedTextRevisionInstruction(RECEIPT, check), REVISION);
  assert.equal(requestedTextRevisionInstruction(RECEIPT, check), REQUESTED_FILE_REVISION_INSTRUCTION.join('["test-results.txt"]'));
  assert.equal(requestedTextDeliveryNote(RECEIPT, check), FAILURE_NOTE);
  // All listed files published (at any depth, any letter case): nothing missing.
  const complete = republished([...RECEIPT.publishedPaths, 'test-results.txt']);
  assert.deepEqual([...requestedTextCheck(literals, complete, {receipt: complete}).missing], []);
  const nested = republished([...RECEIPT.publishedPaths, 'logs/Test-Results.txt']);
  assert.deepEqual([...requestedTextCheck(literals, nested, {receipt: nested}).missing], []);
  // Only a complete path list for this snapshot is evidence.
  assert.equal(requestedTextCheck(literals, RECEIPT, {receipt: {...RECEIPT, publishedPathsOmitted: 1}}), undefined);
  assert.equal(requestedTextCheck(literals, RECEIPT, {receipt: {...RECEIPT, sha256: 'f'.repeat(64)}}), undefined);
  assert.equal(requestedTextCheck(literals, RECEIPT, {}), undefined);
  // A publication of another directory is not the one the owner named.
  const other = republished(RECEIPT.publishedPaths, {...RECEIPT, relativeDirectory: 'fleet-qualification-1d43828fc26e-coding/site'});
  assert.equal(requestedTextCheck(literals, other, {receipt: other}), undefined);
});

test('listed-file extraction skips anything ambiguous', () => {
  const files = prompt => extractRequestedLiterals(prompt).filter(literal => literal.match === 'file')
    .map(literal => `${literal.directory}:${literal.text}`);
  for (const prompt of [
    // Round 082 files-code: paths inside public, no listed directory contents.
    'In a separate workspace directory fleet-calc, write a Python program. Execute it so the program writes public/result.json. ' +
      'Create public/index.html showing the result, then publish ONLY the public subdirectory. Keep Python source outside public.',
    'Create a directory with index.html and styles.css.',
    'Create a public directory with index.html and copies of report.py and totals.py.',
    'Create a public directory with index.html and sources.json mapping report.py, totals.py and test_totals.py to their text.',
    'Create a public directory with index.html or index.htm.',
    'Create a public directory with files such as index.html and data.json.',
    'Create a public directory with index.html and assets/logo.png.',
    'Do not create a public directory with secrets.json and index.html.',
    'Create a public directory with index.html and notes.md removed.',
    'Publish the site with debug.js removed.',
    'Write totals.py, report.py and test_totals.py, then publish the result.',
    'Create a site with index.html, styles.css and app.js.',
  ]) assert.deepEqual(files(prompt), [], prompt);
  assert.deepEqual(files('Then create a directory named site with `index.html`, `results.txt` holding the output, and app.js.'),
    ['site:index.html', 'site:results.txt', 'site:app.js']);
  assert.deepEqual(files('Publish a static site containing index.html, styles.css and app.js, built with Chart.js.'),
    [':index.html', ':styles.css', ':app.js']);
  assert.deepEqual(files('Then create a public directory with an index.html file, a test-results.txt file with the output, and a sources.json file.'),
    ['public:index.html', 'public:test-results.txt', 'public:sources.json']);
});

// Review reproducers for PR #6710: none of these may require a file.
test('files the host cannot publish, optional files and content descriptions are never required', () => {
  const files = prompt => extractRequestedLiterals(prompt).filter(literal => literal.match === 'file');
  for (const prompt of [
    // The host preview rejects these types, so the list is not about the publication.
    'In a new workspace project site-proj, build a small landing page. Then create a public directory with index.html, ' +
      'robots.txt and sitemap.xml. Publish ONLY public as a verified Pixel workspace preview.',
    'Publish a site containing index.html and resume.pdf as a download.',
    'Create a public directory with index.html, results.txt and source report.py. Keep .py files outside the preview.',
    'Create a public directory with index.html, report.py and results.json.',
    'Create a public directory with index.html and config.yaml.',
    'Create a public directory with index.html and build.log.',
    // Optional or conditional.
    'Create a public directory with index.html, styles.css and favicon.ico (optional).',
    'Create a public directory with index.html, app.js, and README.md if you have time.',
    'You can optionally create a public directory with index.html and notes.md.',
    'Create a public directory with index.html and an optional favicon.ico.',
    // Content or a runtime result, not a published file.
    'Publish a preview containing the README.md contents rendered as HTML.',
    'Publish a static site containing the sales.csv data as a bar chart.',
    'Publish a site containing the results.json output as a table.',
    'Create a public directory with index.html, app.js, and export.csv which the page generates client-side when clicked.',
  ]) assert.deepEqual(files(prompt), [], prompt);
});

test('publishable suffixes mirror the host preview allowlist', () => {
  const host = fs.readFileSync(new URL('../host/workspace_preview.py', import.meta.url), 'utf8');
  const block = /^ALLOWED_SUFFIXES = frozenset\(\s*\{([^}]*)\}\s*\)/m.exec(host);
  assert.ok(block, 'host/workspace_preview.py defines ALLOWED_SUFFIXES');
  assert.deepEqual([...PREVIEW_FILE_SUFFIXES].sort(), [...block[1].matchAll(/"(\.[a-z0-9]+)"/g)].map(match => match[1]).sort());
});

const context = {agentId: 'pixel', runId: 'chatcmpl_12a07451-429c-41f0-817c-d3b8b95d3623', sessionId: 'session',
  sessionKey: 'agent:pixel:openai-user:owner'};
function fixture({prompt = TOWER2.prompt, directory = DIRECTORY} = {}) {
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: false});
  guard.observeRun(context, 'pixel', {prompt});
  const invoke = (tool, params, id, result) => {
    const ctx = {...context, toolName: tool, toolCallId: id};
    const event = {toolName: tool, runId: context.runId, toolCallId: id, params};
    const prepared = guard.beforeToolCall(event, ctx);
    assert.notEqual(prepared?.block, true, prepared?.blockReason);
    guard.afterToolCall({...event, params: prepared?.params ?? params, result}, ctx);
    const persisted = guard.toolResultPersist({toolName: tool, toolCallId: id,
      message: {role: 'toolResult', toolName: tool, toolCallId: id, ...result}}, ctx);
    return (persisted?.message?.content ?? result.content).map(block => block.text).join('\n');
  };
  // As recorded, the model wrote public/index.html itself (content stands in;
  // the check reads only the receipt's published path list).
  const index = `${directory}/index.html`, html = '<!DOCTYPE html><title>Expense report CLI</title><h1>Test results</h1>';
  assert.ok(directory !== DIRECTORY || TOWER2.calls.some(call => call.tool === 'write' && call.arguments.path === index));
  invoke('write', {path: index, content: html}, 'write-index',
    {content: [{type: 'text', text: `Successfully wrote ${html.length} bytes to ${index}`}]});
  const publish = (receipt, id) => invoke('pixel_ods_workspace_preview', {relativeDirectory: directory}, id,
    {content: [{type: 'text', text: `ODS independently published and read back ${receipt.files} workspace static files.`}], details: receipt});
  return {guard, publish};
}

test('tower2 round 082 coding: the receipt carries the note, the recorded answer gets one revision, then the honest failure', () => {
  const {guard, publish} = fixture();
  const published = publish(RECEIPT, 'publish');
  assert.ok(published.includes(`[ODS Pixel next step] ${NOTE}`), published);
  assert.deepEqual(guard.beforeAgentFinalize({lastAssistantMessage: TOWER2.finalAnswer}, context), {action: 'revise',
    reason: 'Pixel has not completed every owner-requested verified step.',
    retry: {instruction: REVISION, idempotencyKey: 'pixel-ods-workspace-preview-requested-text', maxAttempts: 1}});
  assert.equal(guard.beforeAgentFinalize({lastAssistantMessage: TOWER2.finalAnswer}, context)?.action, 'finalize');
  const outcome = guard.verificationForRun(context.runId);
  assert.equal(outcome.status, 'failed');
  assert.ok(outcome.text.startsWith(`${FAILURE_NOTE}\n\n`), outcome.text);
});

test('tower2 round 082 coding: republishing with test-results.txt inside public clears the miss', () => {
  const {guard, publish} = fixture();
  publish(RECEIPT, 'publish');
  const receipt = republished([...RECEIPT.publishedPaths, 'test-results.txt']);
  const repaired = publish(receipt, 'republish');
  assert.doesNotMatch(repaired, /Requested files/);
  // This replay omits the recorded test executions, so only the requested-file
  // revision is asserted absent here.
  const answer = TOWER2.finalAnswer.replaceAll(RECEIPT.url, receipt.url);
  assert.notEqual(guard.beforeAgentFinalize({lastAssistantMessage: answer}, context)?.retry?.idempotencyKey,
    'pixel-ods-workspace-preview-requested-text');
  const outcome = guard.verificationForRun(context.runId);
  assert.equal(outcome.status, 'passed');
  assert.equal(outcome.preview.sha256, receipt.sha256);
});

// Review reproducers for PR #6710: a correct delivery of an optional file or a
// file type the host cannot publish is never turned into a repair and a failure.
test('an unpublishable or optional listed file never gates a correct publication', () => {
  for (const [listed, paths] of [['index.html, styles.css and favicon.ico (optional)', ['index.html', 'styles.css']],
    ['index.html, robots.txt and sitemap.xml', ['index.html', 'robots.txt']]]) {
    const directory = 'site-proj/public';
    const prompt = `In a new workspace project site-proj, build a small landing page. Then create a public directory with ${listed}. ` +
      'Publish ONLY public as a verified Pixel workspace preview and include the preview URL.';
    const {guard, publish} = fixture({prompt, directory});
    const receipt = republished(paths, {...RECEIPT, relativeDirectory: directory});
    assert.doesNotMatch(publish(receipt, 'publish'), /Requested files/, listed);
    assert.equal(guard.beforeAgentFinalize({lastAssistantMessage: `Published ${receipt.url}.`}, context), undefined, listed);
    assert.equal(guard.verificationForRun(context.runId).status, 'passed', listed);
  }
});
