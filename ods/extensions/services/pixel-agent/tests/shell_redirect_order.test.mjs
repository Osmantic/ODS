import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdtempSync, readdirSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {REDIRECT_ORDER_NOTE, stderrRedirectedBeforeStdoutFile} from '../plugin/shell-redirect-order.mjs';

// tower1 7402eb38, 2026-09-25 coding journey: unittest wrote to stderr, the
// exec result showed "Ran 29 tests ... OK", and the published file was empty.
const FLEET_COMMAND = 'cd fleet-qualification-489210351f87-coding && mkdir -p public && python3 -m unittest -v 2>&1 > public/test-results.txt';

const flagged = [
  ['fleet replay', FLEET_COMMAND],
  ['plain', 'python3 -m unittest -v 2>&1 > out.txt'],
  ['append', 'pytest 2>&1 >> log.txt'],
  ['explicit fd 1', 'pytest 2>&1 1> log.txt'],
  ['explicit fd 1 append', 'pytest 2>&1 1>>log.txt'],
  ['noclobber override', 'pytest 2>&1 >| log.txt'],
  ['attached target', 'pytest 2>&1 >log.txt'],
  ['no blanks at all', 'pytest 2>&1>log.txt'],
  ['quoted target with a space', 'pytest 2>&1 > "test results.txt"'],
  ['variable target', 'pytest 2>&1 > "$OUT/results.txt"'],
  ['redirects before the command word', '2>&1 > out.txt python3 -m unittest'],
  ['later segment of an and-chain', 'make && ./run-tests 2>&1 > out.txt'],
  ['before a semicolon', 'npm test 2>&1 > out.txt; cat out.txt'],
  ['before an or-chain', 'npm test 2>&1 > out.txt || echo failed'],
  ['last stage of a pipeline', 'printf x | python3 check.py 2>&1 > out.txt'],
  ['second line of a script', 'mkdir -p public\npython3 -m unittest 2>&1 > public/out.txt'],
  ['line continuation', 'python3 -m unittest \\\n  2>&1 > out.txt'],
  ['after a heredoc body', "cat > t.py <<'EOF'\nprint(1)\nEOF\npython3 t.py 2>&1 > out.txt"],
  ['substitution argument stays opaque', 'python3 run.py "$(date)" 2>&1 > out.txt'],
  ['subshell redirected as a whole', '(cd app && npm test) 2>&1 > out.txt'],
  ['brace group redirected as a whole', '{ npm test; } 2>&1 > out.txt'],
  ['loop redirected as a whole', 'for f in a b; do echo $f; done 2>&1 > out.txt'],
  ['comment after the command', 'pytest 2>&1 > out.txt # capture'],
  ['exec with the mistake', 'exec 2>&1 > out.txt'],
  ['dup through another fd', 'pytest 3>&1 2>&3 > out.txt'],
  ['after a double-bracket test', '[[ -f setup.py ]] && pytest 2>&1 > out.txt'],
];

const clean = [
  ['correct order', 'python3 -m unittest -v > out.txt 2>&1'],
  ['correct order append', 'python3 -m unittest -v >> out.txt 2>&1'],
  ['correct order explicit fd', 'python3 -m unittest -v 1> out.txt 2>&1'],
  ['bash both-streams', 'python3 -m unittest -v &> out.txt'],
  ['bash both-streams append', 'python3 -m unittest -v &>> out.txt'],
  ['bash >& shorthand', 'python3 -m unittest -v >& out.txt'],
  ['stderr merged into a tee pipe', 'python3 -m unittest -v 2>&1 | tee out.txt'],
  ['bash |& pipe', 'python3 -m unittest -v |& tee out.txt'],
  ['stderr-only pipe idiom', 'pytest 2>&1 >/dev/null | grep ERROR'],
  ['device target', 'pytest 2>&1 > /dev/null'],
  ['stdout file piped onwards', 'pytest 2>&1 > out.txt | grep ERROR'],
  ['backgrounded', 'pytest 2>&1 > out.txt &'],
  ['stderr re-pointed afterwards', 'pytest 2>&1 > out.txt 2>&1'],
  ['stderr to its own file', 'pytest 2>&1 2> err.txt > out.txt'],
  ['separate files', 'pytest > out.txt 2> err.txt'],
  ['stdout file only', 'pytest > out.txt'],
  ['merge only', 'pytest 2>&1'],
  ['stdout to stderr', 'pytest 1>&2'],
  ['swap through a closed fd', 'pytest 3>&1 1>&2 2>&3 3>&-'],
  ['unknown dup source', 'pytest 2>&4 > out.txt'],
  ['double-quoted text', 'echo "run: pytest 2>&1 > out.txt"'],
  ['single-quoted text', "echo 'pytest 2>&1 > out.txt'"],
  ['ansi-c quoted text', "echo $'pytest 2>&1 > out.txt\\n'"],
  ['escaped operators', 'echo pytest 2\\>\\&1 \\> out.txt'],
  ['quoted merge word', "pytest '2>&1' > out.txt"],
  ['quoted shell script', 'bash -c "pytest 2>&1 > out.txt"'],
  ['python one-liner', "python3 -c 'import os; os.system(\"pytest 2>&1 > out.txt\")'"],
  ['here-string', 'cat <<< "pytest 2>&1 > out.txt"'],
  ['heredoc body', "cat > run.sh <<'EOF'\npytest 2>&1 > out.txt\nEOF"],
  ['unquoted heredoc body', 'cat > run.sh <<EOF\npytest 2>&1 > out.txt\nEOF\nchmod +x run.sh'],
  ['tab-stripped heredoc body', 'cat <<-END > run.sh\n\tpytest 2>&1 > out.txt\n\tEND\nbash run.sh > log.txt 2>&1'],
  ['unterminated heredoc body', 'cat > run.sh <<EOF\npytest 2>&1 > out.txt'],
  ['comment', 'ls # pytest 2>&1 > out.txt'],
  ['command substitution', 'x=$(pytest 2>&1 > out.txt); echo "$x"'],
  ['arithmetic substitution', 'echo $(( 2 > 1 ))'],
  ['backticks are not parsed', 'x=`pytest 2>&1 > out.txt`'],
  ['process substitution is not parsed', 'diff <(pytest 2>&1 > out.txt) expected.txt'],
  ['inside a subshell', '(pytest 2>&1 > out.txt)'],
  ['inside a group redirected elsewhere', '{ pytest 2>&1 > out.txt; } > all.txt'],
  ['inside a loop', 'for t in a b; do pytest $t 2>&1 > $t.txt; done > all.txt'],
  ['inside an if', 'if pytest 2>&1 > out.txt; then echo ok; fi'],
  ['case is not parsed', 'case $x in a) pytest 2>&1 > out.txt;; esac'],
  ['double-bracket comparison', '[[ a > b ]] && echo 2>&1'],
  ['unterminated quote', 'echo "pytest 2>&1 > out.txt'],
  ['dangling redirect', 'pytest 2>&1 >'],
  ['not a string', undefined],
  ['empty', ''],
];

test('fixed note text', () => {
  assert.equal(REDIRECT_ORDER_NOTE,
    'Note: `2>&1 > FILE` sends stderr to this output, not to FILE; FILE receives only stdout. Use `> FILE 2>&1` to capture both.');
});
for (const [name, command] of flagged) test(`flags stderr dup before a stdout file: ${name}`, () => {
  assert.equal(stderrRedirectedBeforeStdoutFile(command), true, JSON.stringify(command));
});
for (const [name, command] of clean) test(`no note: ${name}`, () => {
  assert.equal(stderrRedirectedBeforeStdoutFile(command), false, JSON.stringify(command));
});
test('oversized commands are not scanned', () => {
  assert.equal(stderrRedirectedBeforeStdoutFile(`${'x'.repeat(16384)} 2>&1 > out.txt`), false);
});

// Ground truth from bash itself: the note is emitted only when stderr really
// reached the exec output while the file received stdout without it.
const bash = spawnSync('bash', ['-c', 'true']).status === 0;
test('every note agrees with real bash redirection semantics', {skip: !bash && 'bash unavailable'}, () => {
  for (const command of [
    'emit 2>&1 > out.txt', 'emit 2>&1 >> out.txt', 'emit 2>&1 1> out.txt', 'emit 2>&1>out.txt',
    '2>&1 > out.txt emit', 'true && emit 2>&1 > out.txt', 'printf x | emit 2>&1 > out.txt',
    '(emit) 2>&1 > out.txt', '{ emit; } 2>&1 > out.txt', 'emit 3>&1 2>&3 > out.txt',
    'mkdir -p public && emit 2>&1 > public/test-results.txt',
    'emit > out.txt 2>&1', 'emit &> out.txt', 'emit >& out.txt', 'emit 2>&1 | tee out.txt',
    'emit |& tee out.txt', 'emit 2>&1 > out.txt 2>&1', 'emit 2>&1 2> err.txt > out.txt',
    'emit 2>&1 > /dev/null', '{ emit 2>&1 > out.txt; } > all.txt',
  ]) {
    const dir = mkdtempSync(join(tmpdir(), 'redirect-order-'));
    try {
      const run = spawnSync('bash', ['-c', `exec 2>&1\nemit() { echo OUT; echo ERR >&2; }\n${command}`],
        {cwd: dir, encoding: 'utf8'});
      const files = readdirSync(dir, {recursive: true}).filter(name => name.endsWith('.txt'))
        .map(name => readFileSync(join(dir, name), 'utf8'));
      const misrouted = run.stdout.includes('ERR') && files.some(text => text.includes('OUT') && !text.includes('ERR'));
      assert.equal(stderrRedirectedBeforeStdoutFile(command), misrouted, command);
    } finally {
      rmSync(dir, {recursive: true, force: true});
    }
  }
});
