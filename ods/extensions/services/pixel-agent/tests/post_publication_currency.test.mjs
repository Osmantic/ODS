// Replays Tower2 fleet runs (rounds 060, 061 and 092) against the real host
// snapshot code: publish, later tool calls, final answer. The host re-derives
// the published directory's digest; only real byte changes may make it stale.
import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {createWorkspacePreviewVerifier} from '../plugin/workspace-preview.mjs';

const HOST = fileURLToPath(new URL('../host/workspace_preview.py', import.meta.url));
// Publication reuses publish_snapshot; verification goes through the real
// control-socket handler, including its fail-closed error responses.
const DRIVER = `
import importlib.util, json, os, pathlib, socket, sys, threading
spec = importlib.util.spec_from_file_location("workspace_preview", sys.argv[1])
host = importlib.util.module_from_spec(spec); spec.loader.exec_module(host)
workspace, previews, request = pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]), json.loads(sys.stdin.read())
if request["action"] == "publish":
    print(json.dumps(host.publish_snapshot(workspace, previews, request["relativeDirectory"], os.getuid())))
else:
    client, server = socket.socketpair()
    thread = threading.Thread(target=host._serve_connection, args=(server,),
        kwargs={"workspace": workspace, "previews": previews, "owner_uid": os.getuid(), "port": 9437})
    thread.start()
    client.sendall(json.dumps(request).encode() + b"\\n"); client.shutdown(socket.SHUT_WR)
    print(client.makefile("rb").readline().decode().strip()); thread.join(5)
`;
const python = process.platform !== 'win32' && spawnSync('python3', ['--version']).status === 0;
const root = typeof process.getuid === 'function' && process.getuid() === 0;
const MODEL_ANSWER = 'All tests pass and CLI works correctly.';
const REPORT = 'import csv, json, sys\nrows = list(csv.DictReader(open(sys.argv[1], encoding="utf-8")))\n' +
  'print(json.dumps({r["category"]: r["amount"] for r in rows}))\n';

const EXEC_CONTROL = {prepare: (_run, command) => `/control/wrapper ${Buffer.from(command).toString('base64')}`};
function hostFixture(t, prefix) {
  const dir = realpathSync(mkdtempSync(join(tmpdir(), prefix)));
  t.after(() => { spawnSync('chmod', ['-R', 'u+rwX', dir]); rmSync(dir, {recursive: true, force: true}); });
  const workspace = join(dir, 'workspace'), previews = join(dir, 'previews');
  for (const path of [workspace, previews]) mkdirSync(path, {mode: 0o700});
  const host = request => {
    const run = spawnSync('python3', ['-c', DRIVER, HOST, workspace, previews], {input: JSON.stringify(request), encoding: 'utf8'});
    assert.equal(run.status, 0, run.stderr);
    return JSON.parse(run.stdout);
  };
  let probes = 0;
  const verify = createWorkspacePreviewVerifier({request: async request => { probes++; return host(request); }});
  return {dir, workspace, host, verify, probes: () => probes};
}

function fleetFixture(t, {wrapped}) {
  const {workspace, host, verify, probes} = hostFixture(t, 'pixel-currency-');
  for (const path of [join(workspace, 'expense-report'), join(workspace, 'expense-report/public')]) mkdirSync(path, {mode: 0o700});
  const context = {agentId: 'pixel', runId: 'run-060', sessionId: 'session-060', sessionKey: 'agent:pixel:fleet'};
  const guard = createToolLoopGuard({verifyWorkspacePreview: verify, ...(wrapped ? {execControl: EXEC_CONTROL} : {})});
  guard.observeRun(context, 'pixel', {prompt: 'Build and publish a website in existing expense-report.'});
  const invoke = (name, params, result, id, {blockable = false} = {}) => {
    const ctx = {...context, toolName: name, toolCallId: id};
    const prepared = guard.beforeToolCall({toolName: name, params, toolCallId: id}, ctx);
    // The runtime reports a blocked call's receipt through both hooks.
    if (blockable && prepared?.block) {
      result = {isError: true, content: [{type: 'text', text: prepared.blockReason}], details: {status: 'blocked'}};
      guard.afterToolCall({toolName: name, params, error: prepared.blockReason, result, toolCallId: id}, ctx);
    } else {
      assert.notEqual(prepared?.block, true, prepared?.blockReason);
      guard.afterToolCall({toolName: name, params: prepared?.params ?? params, result,
        ...(result.isError ? {error: resultText(result)} : {}), toolCallId: id}, ctx);
    }
    guard.toolResultPersist({toolName: name, toolCallId: id, message: {role: 'toolResult', toolName: name, toolCallId: id, ...result}}, ctx);
  };
  const write = (path, content, id) => {
    writeFileSync(join(workspace, path), content, {mode: 0o600});
    invoke('write', {path, content}, {content: [{type: 'text', text: `Successfully wrote ${content.length} bytes to ${path}`}]}, id);
  };
  // The exec tool runs the model's command; production wraps it only for cancellation.
  const exec = (command, id) => {
    const run = spawnSync('sh', ['-c', command], {cwd: workspace, encoding: 'utf8'});
    invoke('exec', {command}, {content: [{type: 'text', text: run.stdout + run.stderr}], ...(run.status ? {isError: true} : {}),
      details: {status: 'completed', exitCode: run.status, durationMs: 1, aggregated: run.stdout + run.stderr, cwd: workspace}}, id);
    return run;
  };
  write('expense-report/report.py', REPORT, 'report');
  write('expense-report/data.csv', 'category,amount\nfood,15.75\n', 'data');
  write('expense-report/public/index.html', '<!doctype html><title>Expense report</title><h1>Expense report</h1>', 'index');
  write('expense-report/public/report.py.txt', REPORT, 'copy');
  const published = host({schemaVersion: 1, action: 'publish', relativeDirectory: 'expense-report/public'});
  const url = `http://${published.siteId}.localhost:9437/${published.siteId}/`;
  invoke('pixel_ods_workspace_preview', {relativeDirectory: 'expense-report/public'}, {content: [{type: 'text', text: `Verified browser URL: ${url}`}],
    details: {...published, port: 9437, url, httpStatus: 200, readbackVerified: true}}, 'publish');
  assert.equal(guard.verificationForRun(context.runId).status, 'passed', 'fresh publication is verified');
  return {guard, context, invoke, write, exec, url, probes};
}

async function finalAnswer({guard, context, url}) {
  await guard.revalidateWorkspacePreview({}, context);
  const delivered = guard.replyPayloadSending({runId: context.runId, kind: 'final',
    payload: {text: `${MODEL_ANSWER}\n\nPublished preview: ${url}`}});
  return {status: guard.verificationForRun(context.runId).status, text: delivered?.payload?.text ?? ''};
}

const STALE = /has not been verified again since later tool activity/;
const scenarios = {
  // Tower2 round 060: the extra CLI demo read files and wrote only stdout.
  'read-only exec': f => assert.equal(f.exec('python3 expense-report/report.py expense-report/data.csv', 'demo').status, 0),
  // Tower2 round 061: error-handling demos exit non-zero and change nothing.
  'failing CLI demo': f => assert.equal(f.exec('python3 expense-report/report.py missing.csv', 'demo-error').status, 1),
  'exec rewrites identical bytes': f => f.exec('cp expense-report/public/index.html index.tmp && cat index.tmp > expense-report/public/index.html', 'same'),
  'write outside published dir': f => f.write('expense-report/notes.txt', 'CLI demo passed', 'notes'),
  'exec modifies public/index.html': f => f.exec(`python3 -c "import pathlib; p = pathlib.Path('expense-report/public/index.html'); p.write_text(p.read_text().replace('Expense', 'Changed'))"`, 'modify'),
  'write inside published dir': f => f.write('expense-report/public/index.html', '<!doctype html><title>Changed</title>', 'rewrite'),
  'exec adds a published file': f => f.exec('echo extra > expense-report/public/extra.txt', 'add'),
  'failing exec modifies public/index.html': f => assert.equal(f.exec("sed -i 's/Expense/Changed/' expense-report/public/index.html; exit 3", 'modify-fail').status, 3),
  'symlink swap to identical bytes': f => f.exec('cp expense-report/public/index.html same.html && rm expense-report/public/index.html && ln -s ../../same.html expense-report/public/index.html', 'swap'),
  'read error': f => f.exec('chmod 000 expense-report/public/report.py.txt', 'unreadable'),
};
const verified = new Set(['read-only exec', 'failing CLI demo', 'exec rewrites identical bytes', 'write outside published dir']);

for (const wrapped of [false, true]) for (const [name, act] of Object.entries(scenarios)) {
  test(`post-publication ${name} is ${verified.has(name) ? 'still verified' : 'stale'} (wrapped exec=${wrapped})`,
    {skip: !python ? 'python3 unavailable' : name === 'read error' && root ? 'root reads mode-000 files' : false}, async t => {
      const fixture = fleetFixture(t, {wrapped});
      act(fixture);
      assert.notEqual(fixture.guard.verificationForRun(fixture.context.runId).status, 'passed', 'later activity always requires a fresh host check');
      const delivered = await finalAnswer(fixture);
      assert.equal(fixture.probes(), 1, 'one bounded host comparison at finalization');
      if (verified.has(name)) {
        assert.equal(delivered.status, 'passed');
        assert.ok(delivered.text.startsWith(MODEL_ANSWER), delivered.text);
        assert.doesNotMatch(delivered.text, STALE);
      } else {
        assert.equal(delivered.status, 'failed');
        assert.match(delivered.text, STALE);
        assert.doesNotMatch(delivered.text, new RegExp(MODEL_ANSWER), 'no verification claim for changed bytes');
      }
    });
}

test('read-only calls after the fleet exec neither advance nor revoke the pending comparison', {skip: !python && 'python3 unavailable'}, async t => {
  const fixture = fleetFixture(t, {wrapped: true});
  scenarios['read-only exec'](fixture);
  // Blocked, failed or successful: none of these can change workspace bytes.
  fixture.invoke('process', {action: 'list'}, {content: [{type: 'text', text: 'No sessions.'}]}, 'sessions', {blockable: true});
  fixture.invoke('web_fetch', {url: 'https://docs.python.org/3/library/csv.html'}, {content: [{type: 'text', text: 'csv'}]}, 'docs', {blockable: true});
  fixture.invoke('read', {path: 'expense-report/missing.csv'}, {isError: true, content: [{type: 'text', text: 'ENOENT'}]}, 'missing', {blockable: true});
  fixture.invoke('read', {path: 'expense-report/public/index.html'}, {content: [{type: 'text', text: 'Expense report'}]}, 'readback');
  const delivered = await finalAnswer(fixture);
  assert.equal(delivered.status, 'passed');
  assert.doesNotMatch(delivered.text, STALE);
});

// Tower2 round 061 (main 17dfce8a, #6673 installed): website-create and
// coding-v1 replayed call by call from the recorded session. Writes, execs and
// publication really run against a temporary workspace and the real host code;
// each publication must reproduce the recorded snapshot digest, and the guard
// must refuse exactly the calls it refused in production.
const ROUND061 = JSON.parse(readFileSync(new URL('./post-publication-tower2-round061.json', import.meta.url), 'utf8'));
// strixy-wsl-beta round 069 (main 76706b5f, #6681 installed): coding-v1 published
// through the deferred tool_call wrapper, then ran one exit-0 CLI smoke test.
const ROUND069 = JSON.parse(readFileSync(new URL('./post-publication-strixy-round069.json', import.meta.url), 'utf8'));
// Tower2 round 092 (main 04f0a835): website-create ran a read-only grep after
// its last publication, then a passing transition inspection of that snapshot.
const ROUND092 = JSON.parse(readFileSync(new URL('./post-publication-tower2-round092.json', import.meta.url), 'utf8'));
// A publication, direct or through Tool Search (bare or qualified id).
const publication = call => call.tool === 'pixel_ods_workspace_preview'
  ? {relativeDirectory: call.args.relativeDirectory, details: call.result.details}
  : call.tool === 'tool_call' && call.args.id?.split(':').at(-1) === 'pixel_ods_workspace_preview'
    ? {relativeDirectory: call.args.args.relativeDirectory, details: call.result.details.result.details} : undefined;
const resultText = result => result.content.filter(item => item.type === 'text').map(item => item.text).join('\n');

async function replay(t, run, {wrapped}) {
  const {dir, workspace, host, verify, probes} = hostFixture(t, 'pixel-replay-');
  const scratch = join(dir, 'tmp');
  mkdirSync(scratch, {mode: 0o700});
  const context = {agentId: 'pixel', runId: 'run-replay', sessionId: 'session-replay', sessionKey: 'agent:pixel:fleet'};
  const guard = createToolLoopGuard({verifyWorkspacePreview: verify, workspacePreviewInspectionAvailable: true,
    ...(wrapped ? {execControl: EXEC_CONTROL} : {})});
  guard.observeRun(context, 'pixel', {prompt: run.prompt}, {workspaceRoot: workspace, executionHost: 'sandbox', privateBrowserAccess: false});
  // The sandbox mounts the workspace at /workspace, its cwd, and a private tmpfs at /tmp.
  const sandboxed = command => command.replace(/(^|[\s'"=(>])\/(workspace|tmp)(?=[/\s;&|)'"]|$)/g,
    (_, before, mount) => `${before}${mount === 'workspace' ? workspace : scratch}`);
  // Performs the model's call on the workspace (production wraps exec only
  // for cancellation); returns the recorded receipt.
  const perform = (id, tool, args, recorded) => {
    const result = structuredClone(recorded);
    if (tool === 'write') {
      mkdirSync(dirname(join(workspace, args.path)), {recursive: true, mode: 0o755});
      writeFileSync(join(workspace, args.path), args.content, {mode: 0o600});
    } else if (tool === 'edit') {
      let text = readFileSync(join(workspace, args.path), 'utf8');
      // A recorded edit failure changed nothing; the replayed bytes must reproduce it.
      if (recorded.isError) assert.ok(args.edits.some(({oldText}) => text.split(oldText).length !== 2), `${id}: edit must still fail`);
      else {
        for (const {oldText, newText} of args.edits) {
          assert.equal(text.split(oldText).length, 2, `${id}: edit must match once`);
          text = text.replace(oldText, () => newText);
        }
        writeFileSync(join(workspace, args.path), text);
      }
    } else if (tool === 'exec') {
      const done = spawnSync('sh', ['-c', `umask 022\n${sandboxed(args.command)}`], {cwd: workspace, encoding: 'utf8'});
      assert.equal(done.status, recorded.details.exitCode, `${id}: ${done.stderr}`);
      const text = resultText(result);
      result.details.aggregated ??= text === '(no output)' ? '' : text;
    } else if (tool === 'pixel_ods_workspace_preview') {
      const published = host({schemaVersion: 1, action: 'publish', relativeDirectory: args.relativeDirectory});
      for (const key of ['siteId', 'sha256', 'files', 'bytes', 'entrySha256']) assert.equal(published[key], recorded.details[key], key);
    } else assert.ok(['read', 'pixel_ods_workspace_preview_inspect'].includes(tool), tool);
    return result;
  };
  // OpenClaw 2026.6.33 merges before_tool_call params over the model's
  // arguments (mergeParamsWithApprovalOverrides) and reports the merged
  // params to after_tool_call; e.g. a recorded exec workdir survives wrapping.
  const executed = (params, decision) => decision?.params ? {...params, ...decision.params} : params;
  let nested = 0;
  for (const call of run.calls) {
    const ctx = {...context, toolName: call.tool, toolCallId: call.id};
    const prepared = guard.beforeToolCall({toolName: call.tool, params: call.args, toolCallId: call.id}, ctx);
    assert.equal(prepared?.block === true, call.blocked === true, `${call.tool} ${call.id}: ${prepared?.blockReason}`);
    let result = structuredClone(call.result);
    if (!call.blocked && call.tool === 'tool_call') {
      // OpenClaw's Tool Search runs the catalog tool through its own
      // before_tool_call and after_tool_call hooks under a child ID. Only the
      // outer tool_call result is written to the session, so only the outer
      // call reaches tool_result_persist.
      const tool = call.result.details.tool.name, args = (prepared?.params ?? call.args).args ?? {};
      const childId = `tool_search_code:${call.id}:${tool}:${++nested}`, childCtx = {...context, toolName: tool, toolCallId: childId};
      const child = guard.beforeToolCall({toolName: tool, params: args, toolCallId: childId}, childCtx);
      assert.notEqual(child?.block, true, child?.blockReason);
      const inner = perform(childId, tool, call.args.args ?? {}, call.result.details.result);
      guard.afterToolCall({toolName: tool, params: executed(args, child), result: inner,
        ...(inner.isError ? {error: resultText(inner)} : {}), toolCallId: childId}, childCtx);
      result.details.result = inner;
    } else if (!call.blocked) result = perform(call.id, call.tool, call.args, call.result);
    // As OpenClaw reports it: a refused call keeps the model's params.
    guard.afterToolCall({toolName: call.tool, params: call.blocked ? call.args : executed(call.args, prepared), result,
      ...(result.isError ? {error: resultText(result)} : {}), toolCallId: call.id}, ctx);
    guard.toolResultPersist({toolName: call.tool, toolCallId: call.id, message: {role: 'toolResult', toolName: call.tool, toolCallId: call.id, ...result}}, ctx);
  }
  // before_agent_finalize, then reply_payload_sending with the model's recorded answer.
  await guard.revalidateWorkspacePreview({}, context);
  guard.beforeAgentFinalize({}, context);
  const delivered = guard.replyPayloadSending({runId: context.runId, kind: 'final', payload: {text: run.final}});
  return {status: guard.verificationForRun(context.runId).status, text: delivered?.payload?.text ?? run.final, probes: probes()};
}
const PREVIEW_READY = /\n\nYour preview is ready\.\n\n\[Open preview\]\((http:\/\/[^)]+)\)/;

const REPLAYS = [['tower2 round 061', ROUND061.runs, [false, true]], ['strixy round 069', ROUND069.runs, [false, true]],
  // Production always wraps exec for cancellation (index.js). These execs pass
  // workdir "/workspace", which OpenClaw keeps in the executed params; the
  // guard models that merge on its exec-wrapper path, so replay only that.
  ['tower2 round 092', ROUND092.runs, [true]]];
for (const wrapped of [false, true]) for (const [fleet, runs, modes] of REPLAYS) if (modes.includes(wrapped))
for (const [name, run] of Object.entries(runs)) {
  test(`${fleet} ${name} replay delivers the model's verified answer (wrapped exec=${wrapped})`,
    {skip: !python && 'python3 unavailable'}, async t => {
      const delivered = await replay(t, run, {wrapped});
      assert.equal(delivered.probes, 1, 'one bounded host comparison at finalization');
      assert.equal(delivered.status, 'passed');
      assert.ok(delivered.text.startsWith(run.final), delivered.text);
      assert.doesNotMatch(delivered.text, STALE);
      const published = publication(run.calls.findLast(publication));
      assert.equal(PREVIEW_READY.exec(delivered.text)?.[1], published.details.url);
    });
}
