import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { escapedLineBreakDiagnosis, pythonSyntaxGuidance } from '../plugin/python-syntax-guidance.mjs';
import { createToolLoopGuard, REPEATED_WRITE_REQUIRES_PATCH_REASON } from '../plugin/tool-loop-guard.mjs';

// Exact fleet evidence: Mac mini, Qwen3.5-9B, ODS main 0b36ddc9, journey
// file_write_execute_read, session 1e98aa6c-f229-473a-aa9d-9a758f44c42d.
const FLEET_PROMPT = "In a separate workspace directory fleet-qualification-468ad70ff1cb-calc, write a Python program that computes sum(i*i for i in range(1,101)). Execute it so the program writes public/result.json with fields sum_of_squares (integer) and marker (the exact string FLEET-2f65421dfd). Read result.json back using a file tool. Create public/index.html showing the result and marker, then publish ONLY the public subdirectory as a Pixel workspace preview. Keep Python source outside public. Give the exact numeric result, marker, preview URL, and concise execution evidence. Do not merely describe code.\n\n[ODS Portal delivery requirement: Answer the owner's complete message above. If it asks for exact text, copy that full exact text. Do not answer with a generic acknowledgement. Do not output NO_REPLY.]\n[ODS Portal workspace task route: Perform the requested workspace mutation before verification. When tool_call is visible, use it with id write and normal write args for every new file. edit cannot create a file and requires a non-empty oldText copied from an existing file. Use edit or apply_patch only after reading an existing file. Then use exec only for readback, tests, or the requested digest. Do not repeatedly list directories or hash proposed text instead of the created file.]";
const FLEET_WRITE = {"path":"/workspace/fleet-qualification-468ad70ff1cb-calc/sum_squares.py","content":"import json\n\nresult = sum(i * i for i in range(1, 101))\n\noutput = {\n    \"sum_of_squares\": result,\n    \"marker\": \"FLEET-2f65421dfd\"\n}\n\nwith open(\"public/result.json\", \"w\") as f:\\n    json.dump(output, f, indent=2)\\n\\nprint(f\"Computed sum: {result}\")"};
const FLEET_EXEC = {"command":"cd /workspace/fleet-qualification-468ad70ff1cb-calc && python3 sum_squares.py","workdir":"/workspace/fleet-qualification-468ad70ff1cb-calc"};
const FLEET_OUTPUT = "File \"/workspace/fleet-qualification-468ad70ff1cb-calc/sum_squares.py\", line 10\n    with open(\"public/result.json\", \"w\") as f:\\n    json.dump(output, f, indent=2)\\n\\nprint(f\"Computed sum: {result}\")\n                                               ^\nSyntaxError: unexpected character after line continuation character\n\n(Command exited with code 1)";
const FLEET_FILE = 'fleet-qualification-468ad70ff1cb-calc/sum_squares.py';
const FLEET_DIAGNOSIS = '[ODS Pixel Python syntax] Line 10 of fleet-qualification-468ad70ff1cb-calc/sum_squares.py contains literal \\n escape sequences ' +
  'outside string literals where real line breaks belong (the file content was escaped twice). ' +
  'Rewrite that file with real line breaks; keep escapes inside string literals unchanged.';
const HOST_ROOT = '/Users/owner/ods-install/data/pixel-native/home/.openclaw/workspace-pixel';

const failed = (text, exitCode = 1) =>
  ({isError: true, content: [{type: 'text', text}], details: {status: 'completed', exitCode, durationMs: 169, aggregated: text}});
const written = (file, content) => new Map([[file, content]]);
// Build a traceback in CPython's format for a synthetic source line.
const traceback = (file, line, source, message = 'unexpected character after line continuation character') =>
  `  File "${file}", line ${line}\n    ${source.split('\n')[line - 1].trim()}\n    ^\nSyntaxError: ${message}\n`;
const diagnose = (source, line, {file = '/workspace/project/tool.py', message} = {}) =>
  escapedLineBreakDiagnosis(failed(traceback(file, line, source, message)), written('project/tool.py', source));

test('exact fleet write and traceback produce the byte-stable double-escape diagnosis', () => {
  const diagnosis = escapedLineBreakDiagnosis(failed(FLEET_OUTPUT), written(FLEET_FILE, FLEET_WRITE.content), HOST_ROOT);
  assert.deepEqual(diagnosis, {file: FLEET_FILE, content: FLEET_WRITE.content, text: FLEET_DIAGNOSIS});
  // The generic command-shape advice never applied to this absolute cd, which
  // is why the fleet model saw no hint at all.
  assert.equal(pythonSyntaxGuidance(FLEET_EXEC, failed(FLEET_OUTPUT)), undefined);
  // Content-only receipts, CRLF output and host-rooted traceback paths are the same evidence.
  const {aggregated, ...details} = failed(FLEET_OUTPUT).details;
  assert.equal(escapedLineBreakDiagnosis({content: [{type: 'text', text: FLEET_OUTPUT}], details},
    written(FLEET_FILE, FLEET_WRITE.content)).text, FLEET_DIAGNOSIS);
  assert.equal(escapedLineBreakDiagnosis(failed(FLEET_OUTPUT.replace(/\n/g, '\r\n')),
    written(FLEET_FILE, FLEET_WRITE.content)).text, FLEET_DIAGNOSIS);
  assert.equal(escapedLineBreakDiagnosis(failed(FLEET_OUTPUT.replace('/workspace/', `${HOST_ROOT}/`)),
    written(FLEET_FILE, FLEET_WRITE.content), HOST_ROOT).text, FLEET_DIAGNOSIS);
  assert.equal(escapedLineBreakDiagnosis(failed(FLEET_OUTPUT), written(FLEET_FILE, FLEET_WRITE.content.replace(/\n/g, '\r\n'))).text,
    FLEET_DIAGNOSIS);
});

test('tab escapes are named precisely and alone', () => {
  const tabs = 'def main():\n\\tprint("ok")\n';
  assert.match(diagnose(tabs, 2).text, /^\[ODS Pixel Python syntax\] Line 2 of project\/tool\.py contains literal \\t escape sequences outside string literals where real tabs belong/);
  const both = 'def main():\\n\\tprint("ok")\n';
  assert.match(diagnose(both, 1).text, /contains literal \\n and \\t escape sequences outside string literals where real line breaks and tabs belong.*Rewrite that file with real line breaks and tabs;/);
});

test('real Python tracebacks agree with the diagnosis for escaped source and never for valid escapes', () => {
  const python = process.platform === 'win32' ? 'python' : 'python3';
  const compile = source => {
    const execution = spawnSync(python, ['-c', "import sys; compile(sys.stdin.read(), '/workspace/project/tool.py', 'exec')"],
      {input: source, encoding: 'utf8', timeout: 5000});
    assert.ifError(execution.error);
    return escapedLineBreakDiagnosis({content: [{type: 'text', text: execution.stderr}],
      details: {status: 'completed', exitCode: execution.status, aggregated: execution.stderr}}, written('project/tool.py', source));
  };
  assert.equal(compile(FLEET_WRITE.content)?.text, FLEET_DIAGNOSIS.replace(FLEET_FILE, 'project/tool.py'));
  assert.match(compile('import sys\\nprint(sys.argv)\n')?.text ?? '', /Line 1 of project\/tool\.py contains literal \\n/);
  for (const source of [
    'print("a\\nb")\nvalue = "\\t".join(["x", "y"])\n',
    'import re\npattern = re.compile(r"\\n+\\t")\n',
    'payload = \'{"text": "line\\\\nnext"}\'\n',
    'doc = """first\\nsecond\n\\tthird"""\n',
    'value = b"\\n" + rb"\\t"\n',
    'x = 1  # joined with \\n\n',
    'total = 1 + \\\n    2\n',
    'print("a\\nb") \\ x\n',
    'value = \\x27text\\x27\n',
    'if True print("a\\nb")\n',
  ]) assert.equal(compile(source), undefined, source);
});

test('strings, raw strings, JSON strings, comments and misaligned tracebacks never qualify', () => {
  for (const [source, line] of [
    ['print("a\\nb")\n', 1],
    ["print('a\\tb')\n", 1],
    ['pattern = r"\\n+\\t"\n', 1],
    ['payload = \'{"text": "line\\\\nnext", "tab": "\\\\t"}\'\n', 1],
    ['doc = """\nfirst\\nsecond\n"""\n', 2],
    ["doc = '''\n\\tindented\n'''\n", 2],
    ['label = f"{name}\\n"\n', 1],
    ['x = 1  # joined with \\n\n', 1],
    ['value = "unterminated\\n\n', 1],
    ['print("ok")\nvalue = 1\\ny = 2\n', 1],
    ['a = 1 \\ b\\n\n', 1],
    ['a = 1\\\\nb = 2\n', 1],
    ['value = \\x27text\\x27\n', 1],
    ['a = 1\\x\nb = 2\\n\n', 2],
  ]) assert.equal(diagnose(source, line), undefined, source);
});

test('other SyntaxErrors, unbound files and unverified bytes never qualify', () => {
  const content = FLEET_WRITE.content;
  const files = written(FLEET_FILE, content);
  for (const output of [
    FLEET_OUTPUT.replace('unexpected character after line continuation character', 'invalid syntax'),
    FLEET_OUTPUT.replace('unexpected character after line continuation character', 'unterminated string literal (detected at line 10)'),
    FLEET_OUTPUT.replace('SyntaxError:', 'IndentationError:'),
    FLEET_OUTPUT.replace('/workspace/fleet-qualification-468ad70ff1cb-calc/sum_squares.py', '<stdin>'),
    FLEET_OUTPUT.replace('/workspace/fleet-qualification-468ad70ff1cb-calc/sum_squares.py', '/workspace/other/sum_squares.py'),
    FLEET_OUTPUT.replace('/workspace/fleet-qualification-468ad70ff1cb-calc/', '/workspace/../fleet-qualification-468ad70ff1cb-calc/'),
    FLEET_OUTPUT.replace('/workspace/', '/tmp/'),
    FLEET_OUTPUT.replace('line 10', 'line 9'),
    FLEET_OUTPUT.replace('with open(', 'with  open('),
    FLEET_OUTPUT.replace(/\n +\^\n/, '\n'),
    `${FLEET_OUTPUT}\n${FLEET_OUTPUT}`,
    'SyntaxError: unexpected character after line continuation character',
  ]) assert.equal(escapedLineBreakDiagnosis(failed(output), files), undefined, output);
  assert.equal(escapedLineBreakDiagnosis(failed(FLEET_OUTPUT, 0), files), undefined);
  assert.equal(escapedLineBreakDiagnosis({...failed(FLEET_OUTPUT), details: {...failed(FLEET_OUTPUT).details, status: 'running'}}, files), undefined);
  assert.equal(escapedLineBreakDiagnosis(failed(FLEET_OUTPUT), new Map()), undefined);
  assert.equal(escapedLineBreakDiagnosis(failed(FLEET_OUTPUT), undefined), undefined);
  // A stale record whose reported line differs from Python's echo is not evidence.
  assert.equal(escapedLineBreakDiagnosis(failed(FLEET_OUTPUT), written(FLEET_FILE, content.replace('indent=2', 'indent=4'))), undefined);
  assert.equal(escapedLineBreakDiagnosis(failed(FLEET_OUTPUT), written(FLEET_FILE, content.replace('import json\n', 'import json\r'))), undefined);
  assert.equal(escapedLineBreakDiagnosis(failed(FLEET_OUTPUT.replace('/workspace/', `${HOST_ROOT}/`)), files, '/'), undefined);
});

const envelope = (name, result) => {
  const details = {tool: {id: `openclaw:core:${name}`, name, source: 'openclaw', sourceName: 'core'}, result};
  return {content: [{type: 'text', text: JSON.stringify(details)}], details};
};

function fleetRun({deferred = false, wrap = false, prompt = FLEET_PROMPT} = {}) {
  const guard = createToolLoopGuard(wrap ? {execControl: {prepare: (runId, command) => `/control/wrapper ${runId} ${Buffer.from(command).toString('base64')}`}} : {});
  const context = {agentId: 'pixel', runId: 'run-1', sessionId: 'session-1', sessionKey: 'owner-1'};
  guard.observeRun(context, 'pixel', {prompt});
  let calls = 0;
  const call = (tool, args, result) => {
    const toolCallId = `call-${++calls}`;
    const toolName = deferred ? 'tool_call' : tool;
    const params = deferred ? {id: tool, args} : args;
    const ctx = {...context, toolName, toolCallId};
    const before = guard.beforeToolCall({toolName, params, toolCallId}, ctx);
    if (before?.block) return {before};
    const actual = structuredClone(deferred ? (before?.params ?? params) : {...params, ...before?.params});
    const receipt = deferred ? envelope(tool, result) : result;
    guard.afterToolCall({toolName, params: actual, toolCallId, result: receipt}, ctx);
    const message = {role: 'toolResult', toolName, toolCallId, ...structuredClone(receipt)};
    return {before, receipt, persisted: guard.toolResultPersist({toolName, toolCallId, message}, ctx)?.message ?? message};
  };
  const write = (content = FLEET_WRITE.content) => call('write', {...FLEET_WRITE, content},
    {content: [{type: 'text', text: `Successfully wrote ${Buffer.byteLength(content)} bytes to ${FLEET_FILE}`}], details: {}});
  return {guard, call, write};
}

for (const deferred of [false, true]) for (const wrap of [false, true]) {
  test(`fleet replay: exec result and every identical re-send name the same diagnosis (deferred=${deferred},wrap=${wrap})`, () => {
    const {call, write} = fleetRun({deferred, wrap});
    assert.notEqual(write().before?.block, true);
    const exec = call('exec', FLEET_EXEC, failed(FLEET_OUTPUT));
    // One byte-identical block; it replaces, not joins, the generic execution note.
    assert.deepEqual(exec.persisted.content.filter(block => /\[ODS Pixel (?:Python syntax|execution)\]/.test(block.text)),
      [{type: 'text', text: FLEET_DIAGNOSIS}]);
    // The receipt itself is diagnostic-only: Python's output and exit status are unchanged.
    const kept = deferred ? exec.persisted.details.result : exec.persisted;
    assert.equal(kept.details.exitCode, 1);
    assert.equal(kept.content[0].text, FLEET_OUTPUT);
    // Unrelated later results carry no copy of it.
    const read = call('read', {path: FLEET_WRITE.path}, {content: [{type: 'text', text: FLEET_WRITE.content}], details: {}});
    assert.doesNotMatch(JSON.stringify(read.persisted), /escaped twice/);
    for (let attempt = 0; attempt < 3; attempt++) {
      assert.deepEqual(write().before, {block: true, blockReason: `${REPEATED_WRITE_REQUIRES_PATCH_REASON} ${FLEET_DIAGNOSIS}`});
    }
    // A materially different correction is still allowed, and once the
    // recorded bytes change, a repeat of the new bytes gets the plain refusal.
    const fixed = FLEET_WRITE.content.replace(/\\n/g, '\n');
    assert.notEqual(write(fixed).before?.block, true);
    assert.deepEqual(write(fixed).before, {block: true, blockReason: REPEATED_WRITE_REQUIRES_PATCH_REASON});
  });
}

test('repeated writes after unrelated Python failures keep the plain refusal', () => {
  const legit = 'import json\nprint("a\\nb")\nvalue = r"\\t"\nprint(json.dumps({"k": "v\\n"}))\n';
  const {call, write} = fleetRun();
  assert.notEqual(write(legit).before?.block, true);
  const output = `File "/workspace/${FLEET_FILE}", line 2\n    print("a\\nb")\n    ^\nSyntaxError: invalid syntax\n`;
  const exec = call('exec', FLEET_EXEC, failed(output));
  assert.doesNotMatch(JSON.stringify(exec.persisted), /escaped twice/);
  assert.deepEqual(write(legit).before, {block: true, blockReason: REPEATED_WRITE_REQUIRES_PATCH_REASON});
});

test('a diagnosis from an unbound exec or another run never reaches the refusal', () => {
  const {guard, write} = fleetRun();
  assert.notEqual(write().before?.block, true);
  // A result with no matching dispatched exec is not bound evidence.
  guard.afterToolCall({toolName: 'exec', params: FLEET_EXEC, toolCallId: 'forged', result: failed(FLEET_OUTPUT)},
    {agentId: 'pixel', runId: 'run-1', sessionId: 'session-1', sessionKey: 'owner-1', toolName: 'exec', toolCallId: 'forged'});
  assert.deepEqual(write().before, {block: true, blockReason: REPEATED_WRITE_REQUIRES_PATCH_REASON});
});
