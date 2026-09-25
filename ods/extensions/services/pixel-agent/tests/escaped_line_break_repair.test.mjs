import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {escapedLineBreakDiagnosis, escapedLineBreakScan, escapedLineBreakText, strayEscapeOffsets,
  ESCAPED_LINE_BREAK_RERUN_NEXT, ESCAPED_LINE_BREAK_WRITE_NEXT, MAX_ESCAPED_LINE_BREAK_REPAIRS} from '../plugin/python-syntax-guidance.mjs';
import {REPEATED_WRITE_REQUIRES_PATCH_REASON} from '../plugin/tool-loop-guard.mjs';
import {execResult, guardReplay, hostPath, removeWorkspaces, workspaceWith} from './fixtures/guard-replay.mjs';

// Fleet evidence: Mac mini, Qwen3.5-9B, round 078, coding_v1. The written
// test_totals.py had literal \n in code on lines 132 and 302; the traceback
// named only line 132, and the model re-typed the 13 KB file four times
// (write, heredoc, Python heredoc, base64), repeating the same escapes.
const MAC = JSON.parse(fs.readFileSync(new URL('./fixtures/escaped-line-breaks-mac-round078.json', import.meta.url), 'utf8'));
const SOURCE = MAC.write.content;
const DIRECTORY = MAC.path.split('/')[0];
// The fleet coding_v1 prompt, first sentence (the owner names the project directory).
const PROMPT = `CODE TASK ${DIRECTORY}-v1: In a new workspace project ${DIRECTORY}, actually implement a useful stdlib-only Python CSV ` +
  'expense-report CLI using exactly three Python files: totals.py (logic), report.py (CLI), test_totals.py (unittest). Put a browser report in public/index.html.';
// Python's own view of the source, or undefined when python3 is unavailable.
const PYTHON = spawnSync('python3', ['-c', 'import sys; print(sys.version_info[1])'], {encoding: 'utf8'});
const PYTHON_MINOR = PYTHON.status === 0 ? Number(PYTHON.stdout.trim()) : undefined;
const needsPython = {skip: PYTHON_MINOR === undefined && 'python3 is unavailable'};
const parses = file => spawnSync('python3', ['-c', 'import ast,sys; ast.parse(open(sys.argv[1], encoding="utf-8").read())', file]).status === 0;
const run = (command, cwd) => spawnSync('sh', ['-c', command], {cwd, encoding: 'utf8', timeout: 10000});
// The Mac file with the 8 places replaced, computed independently of the command.
function repaired(source, offsets) {
  const chars = Array.from(source);
  for (const {offset, kind} of [...offsets].reverse()) chars.splice(offset, 2, kind === 'n' ? '\n' : '\t');
  return chars.join('');
}

after(removeWorkspaces);

test('the whole-file scan finds all 8 places on lines 132 and 302, not only the traceback line', () => {
  const found = strayEscapeOffsets(SOURCE);
  assert.deepEqual(found.map(item => item.line), [132, 132, 132, 132, 302, 302, 302, 302]);
  assert.ok(found.every(item => item.kind === 'n'));
  const chars = Array.from(SOURCE);
  assert.ok(found.every(({offset}) => chars[offset] === '\\' && chars[offset + 1] === 'n'));
  assert.match(MAC.output, /line 132\n/);
  assert.doesNotMatch(MAC.output, /line 302/);
});

test('the scanner is conservative: strings, comments and continuations never count; doubt returns nothing', () => {
  const clean = 'import re\nx = "a\\nb"  # \\n in a comment\ny = r"\\t" + b"\\n"\nz = """\n\\n\n"""\ntotal = 1 + \\\n    2\n';
  assert.deepEqual(strayEscapeOffsets(clean), []);
  assert.deepEqual(strayEscapeOffsets('a = 1\\nb = 2\n\\tc = 3\n').map(({line, kind}) => [line, kind]), [[1, 'n'], [2, 't']]);
  for (const unsure of ['a = 1\\nb = 2 \\x\n', 'a = "open\nb = 1\\n\n', 'a = 1\\nb = 2\r\n', 'a = """never closed\\n',
    // Python 3.12 lets a replacement field reuse the f-string's own quote; this lexer does not follow that.
    'label = f"{"a\\nb"}"\n', "x = rf'{d['k']}'\\n\n"]) {
    assert.equal(strayEscapeOffsets(unsure), undefined, JSON.stringify(unsure));
  }
  // Ordinary f-strings: fields, nested format specs, doubled braces and other quotes inside.
  const formatted = 'w = 3\ns = f"{w:{w}}|{{literal}}|{d[\'k\']}|{ {1: 2}[1] }"\\nprint(s)\n';
  assert.deepEqual(strayEscapeOffsets(formatted).map(({line}) => line), [2]);
  // "if" before a quote is a keyword, not an f-string prefix.
  assert.deepEqual(strayEscapeOffsets('if"x"=="x":\n    pass\n'), []);
});

test('the repair command is one line whose program has no shell-active character', () => {
  const scan = escapedLineBreakScan(MAC.path, SOURCE);
  assert.deepEqual({file: scan.file, lines: scan.lines, count: scan.count, kinds: scan.kinds, workdir: scan.workdir},
    {file: MAC.path, lines: [132, 302], count: 8, kinds: 'n', workdir: `/workspace/${DIRECTORY}`});
  assert.match(scan.command, /^python3 -c "[^"]+"$/);
  const program = scan.command.slice('python3 -c "'.length, -1);
  assert.doesNotMatch(program, /[\\$`"!\r\n]/);
  assert.ok(scan.command.length < 1000, `${scan.command.length} characters`);
  assert.equal(escapedLineBreakText(scan, ESCAPED_LINE_BREAK_WRITE_NEXT),
    `[ODS Pixel Python syntax] ${MAC.path} has literal \\n outside string literals on lines 132 and 302 (8 places): ` +
    'the tool call escaped them twice, and re-typing the file repeats it. Do not rewrite the file. ' +
    `Run exactly this one command with exec (workdir /workspace/${DIRECTORY}): ${scan.command} ` +
    'It changes only those 8 places, refuses if the file changed or already parses, and writes only a result that parses. ' +
    'Then continue with the task.');
});

test('no command for an unsafe path, a non-Python file or too many places; the note remains', () => {
  const source = 'a = 1\\nb = 2\n';
  for (const file of ['my project/tool.py', '-x/tool.py', 'project/tool.txt']) {
    const scan = escapedLineBreakScan(file, source);
    assert.equal(scan.command, undefined, file);
    assert.match(escapedLineBreakText(scan, ESCAPED_LINE_BREAK_RERUN_NEXT),
      /Replace only those escape sequences with real line breaks using targeted edits; keep escapes inside string literals unchanged\. Then rerun the same test command\.$/);
  }
  const many = Array.from({length: MAX_ESCAPED_LINE_BREAK_REPAIRS + 1}, (_, index) => `a${index} = 1\\n`).join('') + '\n';
  assert.equal(escapedLineBreakScan('project/many.py', many).command, undefined);
  assert.equal(escapedLineBreakScan('project/clean.py', 'print("a\\nb")\n'), undefined);
});

test('running the command repairs exactly the 8 places, from the file directory or the workspace root', needsPython, () => {
  const scan = escapedLineBreakScan(MAC.path, SOURCE);
  const expected = repaired(SOURCE, strayEscapeOffsets(SOURCE));
  for (const cwd of ['directory', 'root']) {
    const root = workspaceWith({[MAC.path]: SOURCE});
    const file = hostPath(root, MAC.path);
    assert.equal(parses(file), false);
    const result = run(scan.command, cwd === 'root' ? root : path.dirname(file));
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.trim(), `replaced 8 escaped line breaks in ${cwd === 'root' ? MAC.path : 'test_totals.py'}`);
    assert.equal(fs.readFileSync(file, 'utf8'), expected);
    assert.ok(parses(file));
    // A second run finds a different file and changes nothing.
    const again = run(scan.command, path.dirname(file));
    assert.notEqual(again.status, 0);
    assert.match(again.stderr, /file missing or changed; not modified/);
    assert.equal(fs.readFileSync(file, 'utf8'), expected);
  }
});

test('the command refuses a mis-copied offset, an already valid file and a result that does not parse', needsPython, () => {
  const scan = escapedLineBreakScan(MAC.path, SOURCE);
  const root = workspaceWith({[MAC.path]: SOURCE});
  const file = hostPath(root, MAC.path);
  const first = strayEscapeOffsets(SOURCE).at(-1).offset;
  const tampered = scan.command.replace(`O=(${first},`, `O=(${first + 1},`);
  assert.notEqual(tampered, scan.command);
  const refused = run(tampered, path.dirname(file));
  assert.notEqual(refused.status, 0);
  assert.match(refused.stderr, /offsets altered; not modified/);
  assert.equal(fs.readFileSync(file, 'utf8'), SOURCE);

  // A file that already parses is never changed, even when the digest and
  // the offsets agree (here \n sits inside a string at the scanned offset,
  // standing in for any source the scan misreads).
  const stray = 'ab=1\\nc=2\n', parsing = "x='1\\n'\nc=2\n";
  const strayScan = escapedLineBreakScan('project/valid.py', stray);
  assert.deepEqual(strayEscapeOffsets(stray).map(({offset}) => offset), [4]);
  const digest = text => createHash('sha256').update(text).digest('hex').slice(0, 16);
  const rebound = strayScan.command.replace(digest(stray), digest(parsing));
  assert.notEqual(rebound, strayScan.command);
  const validRoot = workspaceWith({'project/valid.py': parsing});
  const validResult = run(rebound, path.join(validRoot, 'project'));
  assert.notEqual(validResult.status, 0);
  assert.match(validResult.stderr, /valid\.py already parses; not modified/);
  assert.equal(fs.readFileSync(path.join(validRoot, 'project', 'valid.py'), 'utf8'), parsing);

  // Replacing the escape would still not parse (an unclosed bracket): nothing is written.
  const broken = 'a = 1\\nb = (\n';
  const brokenRoot = workspaceWith({'project/broken.py': broken});
  const brokenResult = run(escapedLineBreakScan('project/broken.py', broken).command, path.join(brokenRoot, 'project'));
  assert.notEqual(brokenResult.status, 0);
  assert.match(brokenResult.stderr, /SyntaxError/);
  assert.equal(fs.readFileSync(path.join(brokenRoot, 'project', 'broken.py'), 'utf8'), broken);
});

test('a traceback diagnosis with the current file carries the same command for every place', () => {
  const written = new Map([[MAC.path, SOURCE]]);
  const failed = execResult(MAC.output, 1);
  const diagnosis = escapedLineBreakDiagnosis(failed, written, '/unused', () => SOURCE);
  assert.equal(diagnosis.text, escapedLineBreakText(escapedLineBreakScan(MAC.path, SOURCE), ESCAPED_LINE_BREAK_RERUN_NEXT));
  assert.doesNotMatch(diagnosis.text, /Rewrite that file/);
  // Current bytes that no longer show the reported line give the plain note.
  const moved = `# moved\n${SOURCE}`;
  assert.match(escapedLineBreakDiagnosis(failed, written, '/unused', () => moved).text, /^\[ODS Pixel Python syntax\] Line 132 of .* Do not rewrite the file;/);
  assert.match(escapedLineBreakDiagnosis(failed, written, '/unused', () => { throw new Error('unreadable'); }).text,
    /using a targeted edit; keep escapes inside string literals unchanged\.$/);
});

// The unittest output the Mac model received before compaction: framework
// frames, then the recorded traceback tail (at least 600 characters, so the
// compacted-unittest projection applies, as it did on the Mac).
const RAW_OUTPUT = 'Traceback (most recent call last):\n' +
  '  File "<frozen runpy>", line 198, in _run_module_as_main\n' +
  '  File "<frozen runpy>", line 88, in _run_code\n' +
  '  File "/usr/lib/python3.12/unittest/__main__.py", line 18, in <module>\n    main(module=None)\n' +
  '  File "/usr/lib/python3.12/unittest/main.py", line 104, in __init__\n    self.parseArgs(argv)\n' +
  '  File "/usr/lib/python3.12/unittest/main.py", line 153, in parseArgs\n    self.createTests()\n' +
  '  File "/usr/lib/python3.12/unittest/main.py", line 164, in createTests\n' +
  '    self.test = self.testLoader.loadTestsFromNames(self.testNames,\n' +
  '  File "/usr/lib/python3.12/unittest/loader.py", line 137, in loadTestsFromName\n    module = __import__(module_name)\n' +
  `  ${MAC.output}`;

function macRun({wrapped, root}) {
  const replay = guardReplay({wrapped, root, prompt: PROMPT});
  const unittest = respond => replay.exec(MAC.exec, respond);
  return {...replay, unittest};
}

for (const wrapped of [false, true]) {
  test(`Mac replay: the write names the repair, a re-write is refused with it, and the traceback keeps it over the generic hint (wrapped=${wrapped})`, () => {
    const root = workspaceWith();
    const {write, exec, unittest} = macRun({wrapped, root});
    const scan = escapedLineBreakScan(MAC.path, SOURCE);
    const writeNote = escapedLineBreakText(scan, ESCAPED_LINE_BREAK_WRITE_NEXT);

    const first = write(MAC.write.path, SOURCE);
    assert.notEqual(first.decision?.block, true);
    assert.ok(first.text.includes(writeNote), first.text);
    // The one next step is the repair, not the usual next-file or publication coaching.
    assert.doesNotMatch(first.text, /\[ODS Pixel next step\]/);

    // An identical re-write is refused, citing the command instead of rewrite advice.
    const again = write(MAC.write.path, SOURCE);
    assert.deepEqual(again.decision, {block: true, blockReason: `${REPEATED_WRITE_REQUIRES_PATCH_REASON} ${writeNote}`});

    // The test run's traceback: the bound diagnosis with the command, and no
    // generic [ODS Pixel repair] hint (on the Mac that hint shadowed it).
    const failed = unittest(() => execResult(RAW_OUTPUT, 1));
    assert.match(failed.text, /\[Earlier unittest framework frames compacted\.\]/);
    assert.ok(failed.text.includes(escapedLineBreakText(scan, ESCAPED_LINE_BREAK_RERUN_NEXT)), failed.text);
    assert.doesNotMatch(failed.text, /\[ODS Pixel repair\]/);
    assert.equal(failed.text.split('[ODS Pixel Python syntax]').length - 1, 1, 'stated once');
    assert.equal(failed.inner.details.exitCode, 1, 'the exit status is untouched');

    // The model runs the command it was given; the guard admits it unchanged.
    const repair = exec({command: scan.command, workdir: scan.workdir}, args =>
      PYTHON_MINOR === undefined ? execResult(`replaced 8 escaped line breaks in test_totals.py`, 0)
        : (result => execResult(`${result.stdout}${result.stderr}`, result.status))(run(args.command, hostPath(root, args.workdir))));
    assert.notEqual(repair.decision?.block, true, repair.decision?.blockReason);
    assert.equal(repair.executed.command, scan.command);
    if (PYTHON_MINOR !== undefined) {
      assert.equal(repair.inner.details.exitCode, 0, repair.text);
      assert.ok(parses(hostPath(root, MAC.path)));
    }
  });
}

test('without a configured workspace the bound diagnosis still wins over the generic hint', () => {
  // The guard has no workspace root, so it cannot read the file back: no
  // write-time scan and no command, but the traceback diagnosis still binds
  // to the recorded write and replaces the generic compacted-unittest hint.
  const {write, exec} = guardReplay({root: workspaceWith(), workspaceRoot: null, prompt: PROMPT});
  assert.doesNotMatch(write(MAC.write.path, SOURCE).text, /\[ODS Pixel Python syntax\]/);
  const failed = exec(MAC.exec, () => execResult(RAW_OUTPUT, 1));
  assert.ok(failed.text.includes('[ODS Pixel Python syntax] Line 132 of fleet-qualification-bf541c4bc8e3-coding/test_totals.py ' +
    'contains literal \\n escape sequences outside string literals where real line breaks belong (the file content was escaped twice). ' +
    'Do not rewrite the file;'), failed.text);
  assert.doesNotMatch(failed.text, /\[ODS Pixel repair\]/);
});

test('the write-time note is absent for clean Python, unsure scans and other files', () => {
  const root = workspaceWith();
  const {write} = guardReplay({root, prompt: PROMPT});
  for (const [file, content] of [
    [`${DIRECTORY}/clean.py`, 'print("a\\nb")\n'],
    [`${DIRECTORY}/unsure.py`, 'a = 1\\nb = 2 \\x\n'],
    [`${DIRECTORY}/notes.txt`, 'a = 1\\nb = 2\n'],
    [`${DIRECTORY}/data.json`, '{"a": "x\\ny"}\n'],
  ]) {
    const result = write(file, content);
    assert.notEqual(result.decision?.block, true, file);
    assert.doesNotMatch(result.text, /\[ODS Pixel Python syntax\]/, file);
  }
});
