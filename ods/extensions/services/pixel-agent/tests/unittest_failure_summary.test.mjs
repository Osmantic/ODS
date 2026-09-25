import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import {execResult, guardReplay, removeWorkspaces, workspaceWith} from './fixtures/guard-replay.mjs';

// Fleet evidence: windows-laptop-wsl-beta, round 081, Qwen3.5-9B, coding_v1.
// The suite reported "FAILED (failures=1, errors=1)", but the compacted result
// kept only the last block (the FAIL). The model then made 19 single-test runs
// (A#6-A#24) to find the hidden ERROR in test_rounding. The FAIL block below
// is the recorded one; the ERROR block is reconstructed in CPython's format.
const DIRECTORY = 'fleet-qualification-5abc4de4e9e3-coding';
const MODULE = `${DIRECTORY}.test_totals`;
const PROMPT = `CODE TASK ${DIRECTORY}-v1: In a new workspace project ${DIRECTORY}, actually implement a useful stdlib-only Python CSV ` +
  'expense-report CLI using exactly three Python files: totals.py (logic), report.py (CLI), test_totals.py (unittest). ' +
  'Put a browser report in public/index.html.';
const COMMAND = {command: `python3 -m unittest -v ${MODULE} 2>&1`};
const RULE = '='.repeat(70), DASH = '-'.repeat(70);
const passing = Array.from({length: 21}, (_, index) =>
  `test_case_${String(index).padStart(2, '0')} (${MODULE}.TestParseAmount.test_case_${String(index).padStart(2, '0')}) ... ok`);
const ERROR_BLOCK = [
  RULE,
  `ERROR: test_rounding (${MODULE}.TestAggregateExpenses.test_rounding)`,
  'Round half up to cents.',
  DASH,
  'Traceback (most recent call last):',
  `  File "/workspace/${DIRECTORY}/test_totals.py", line 120, in test_rounding`,
  '    result = aggregate_expenses(path)',
  '             ^^^^^^^^^^^^^^^^^^^^^^^^',
  `  File "/workspace/${DIRECTORY}/totals.py", line 58, in aggregate_expenses`,
  '    totals[category] += amount',
  "TypeError: unsupported operand type(s) for +=: 'int' and 'decimal.Decimal'",
  '',
];
const FAIL_BLOCK = [
  RULE,
  `FAIL: test_large_amounts (${MODULE}.TestAggregateExpenses.test_large_amounts)`,
  DASH,
  'Traceback (most recent call last):',
  `  File "/workspace/${DIRECTORY}/test_totals.py", line 146, in test_large_amounts`,
  '    self.assertEqual(result, {"food": "9224.68"})',
  "AssertionError: {'food': '9124.68'} != {'food': '9224.68'}",
  "- {'food': '9124.68'}",
  '?            ^',
  '',
  "+ {'food': '9224.68'}",
  '?            ^',
  '',
  '',
];
const LAPTOP_OUTPUT = [...passing,
  `test_rounding (${MODULE}.TestAggregateExpenses.test_rounding)`, 'Round half up to cents. ... ERROR',
  `test_large_amounts (${MODULE}.TestAggregateExpenses.test_large_amounts) ... FAIL`, '',
  ...ERROR_BLOCK, ...FAIL_BLOCK, DASH, 'Ran 23 tests in 0.004s', '', 'FAILED (failures=1, errors=1)'].join('\n');

after(removeWorkspaces);

function runOnce(output, {wrapped = false} = {}) {
  const {exec} = guardReplay({wrapped, root: workspaceWith({[`${DIRECTORY}/test_totals.py`]: 'import unittest\n'}), prompt: PROMPT});
  return exec(COMMAND, () => execResult(output, 1));
}

for (const wrapped of [false, true]) {
  test(`laptop replay: both the ERROR and the FAIL reach the model (wrapped=${wrapped})`, () => {
    const result = runOnce(LAPTOP_OUTPUT, {wrapped});
    const text = result.persisted.content[0].text;
    assert.match(text, /^\[Earlier unittest framework frames compacted\.\]\n/);
    // The first failing test keeps its detail block.
    assert.match(text, /ERROR: test_rounding \(.*\)\n[\s\S]*totals\.py", line 58, in aggregate_expenses\n    totals\[category\] \+= amount\nTypeError: unsupported operand type\(s\) for \+=: 'int' and 'decimal\.Decimal'/);
    // Every other failing test: its header, workspace frame and final error line.
    assert.ok(text.includes(['Also failing (1):',
      `FAIL: test_large_amounts (${MODULE}.TestAggregateExpenses.test_large_amounts)`,
      `  File "/workspace/${DIRECTORY}/test_totals.py", line 146, in test_large_amounts`,
      "AssertionError: {'food': '9124.68'} != {'food': '9224.68'}"].join('\n')), text);
    assert.match(text, /Ran 23 tests in 0\.004s\n\nFAILED \(failures=1, errors=1\)/);
    assert.doesNotMatch(text, /test_case_\d+ .* \.\.\. ok/, 'passing tests stay compacted');
    assert.ok(text.length < 2600, `${text.length} characters`);
    // Only the model-facing text changes: the exit status is untouched.
    assert.equal(result.inner.details.exitCode, 1);
  });
}

test('many failures are capped at six listed, then named', () => {
  const blocks = Array.from({length: 8}, (_, index) => [
    RULE, `FAIL: test_value_${index} (${MODULE}.TestValues.test_value_${index})`, DASH,
    'Traceback (most recent call last):',
    `  File "/workspace/${DIRECTORY}/test_totals.py", line ${200 + index}, in test_value_${index}`,
    `    self.assertEqual(value(${index}), ${index + 1})`,
    `AssertionError: ${index} != ${index + 1}`, '']).flat();
  const output = [...passing, '', ...blocks, DASH, 'Ran 29 tests in 0.010s', '', 'FAILED (failures=8)'].join('\n');
  const text = runOnce(output).persisted.content[0].text;
  assert.match(text, /FAIL: test_value_0 [\s\S]*AssertionError: 0 != 1\nAlso failing \(7\):\n/);
  for (let index = 1; index <= 6; index++) {
    assert.ok(text.includes(`FAIL: test_value_${index} (${MODULE}.TestValues.test_value_${index})\n` +
      `  File "/workspace/${DIRECTORY}/test_totals.py", line ${200 + index}, in test_value_${index}\nAssertionError: ${index} != ${index + 1}`), `test_value_${index}`);
  }
  assert.match(text, /\nAssertionError: 6 != 7\n\+1 more failing test: test_value_7\nRan 29 tests/);
  assert.match(text, /FAILED \(failures=8\)$/);
  assert.ok(text.length <= 2400 + 60, `${text.length} characters`);
});

test('a repeated failure is marked instead of repeated', () => {
  const block = index => [RULE, `ERROR: test_io_${index} (${MODULE}.TestIo.test_io_${index})`, DASH,
    'Traceback (most recent call last):', `  File "/workspace/${DIRECTORY}/totals.py", line 12, in load`,
    '    return open(path).read()', "FileNotFoundError: [Errno 2] No such file or directory: 'missing.csv'", ''];
  const output = [...passing, '', ...block(1), ...block(2), ...block(3), DASH, 'Ran 24 tests in 0.004s', '', 'FAILED (errors=3)'].join('\n');
  const text = runOnce(output).persisted.content[0].text;
  assert.ok(text.includes(`Also failing (2):\nERROR: test_io_2 (${MODULE}.TestIo.test_io_2) [same error as above]\n` +
    `ERROR: test_io_3 (${MODULE}.TestIo.test_io_3) [same error as above]\n`), text);
});

test('a single failure keeps the existing one-block summary', () => {
  const output = [...passing, '', ...FAIL_BLOCK, DASH, 'Ran 22 tests in 0.004s', '', 'FAILED (failures=1)'].join('\n');
  const text = runOnce(output).persisted.content[0].text;
  assert.match(text, /^\[Earlier unittest framework frames compacted\.\]\nFAIL: test_large_amounts/);
  assert.doesNotMatch(text, /Also failing/);
  assert.match(text, /FAILED \(failures=1\)/);
});
