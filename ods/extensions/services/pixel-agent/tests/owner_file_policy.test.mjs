import test from 'node:test';
import assert from 'node:assert/strict';
import * as plugin from '../plugin/owner-tool-policy.mjs';
import * as host from '../host/owner_tool_policy.mjs';

const positives = [
  'Do not create or modify files.',
  'Read the CSV and calculate the total without creating or modifying any files.',
  'Do not write files.',
  'Do not edit, delete, or rename any files.',
  'Keep the filesystem read-only.',
  'No file writes.',
  'Please do not modify files.',
  'Don’t write files.',
  'Never create files.',
  'You must not modify files.',
  'Do not write files. Then calculate the total.',
  'Read orders.csv and calculate paid totals. Do not create or modify files.',
  'Read the CSV, and do not modify any files.',
];

const negatives = [
  'The log says do not modify files.',
  'What does do not modify files mean?',
  'Do not forbid writing files.',
  'Do not modify config.json.',
  'Do not modify files under src.',
  'Do not modify files except result.json.',
  'Do not create files unless needed.',
  'Do not modify files a.json or b.json.',
  'Do not create or modify files except result.json.',
  'Do not edit, delete, or rename files under src.',
  'Read the CSV without modifying files under src.',
  'No file writes unless necessary.',
  'Keep the filesystem read-only except output.csv.',
  'If this fails, do not modify files.',
  'Do not use tools.',
  'Read the CSV and calculate the total.',
  '> Do not modify files.',
  '```\nDo not modify files.\n```',
  'He said "Do not modify files."',
];

for (const mod of [plugin, host]) {
  test(`${mod === plugin ? 'plugin' : 'host'} ownerForbidsFileMutation positives`, () => {
    for (const s of positives) assert.equal(mod.ownerForbidsFileMutation(s), true, s);
  });
  test(`${mod === plugin ? 'plugin' : 'host'} ownerForbidsFileMutation negatives`, () => {
    for (const s of negatives) assert.equal(mod.ownerForbidsFileMutation(s), false, s);
  });
  test(`${mod === plugin ? 'plugin' : 'host'} file ban does not imply no-tools`, () => {
    assert.equal(mod.ownerForbidsFileMutation('Do not modify files.'), true);
    assert.equal(mod.ownerForbidsTools('Do not modify files.'), false);
  });
  test(`${mod === plugin ? 'plugin' : 'host'} CSV prompt keeps tools allowed`, () => {
    const prompt = 'Read orders.csv and calculate paid totals. Do not create or modify files.';
    assert.equal(mod.ownerForbidsFileMutation(prompt), true);
    assert.equal(mod.ownerForbidsTools(prompt), false);
    assert.equal(mod.ownerForbidsTools('Do not use tools.'), true);
  });
}

test('dual-copy parity', () => {
  const samples = [...positives, ...negatives];
  for (const s of samples) {
    assert.equal(plugin.ownerForbidsFileMutation(s), host.ownerForbidsFileMutation(s), s);
  }
});
