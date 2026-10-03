import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const manifest = JSON.parse(readFileSync(new URL('../host/openclaw-read-range.json', import.meta.url)));
const definition = manifest.replacements.find(([before]) => before.startsWith('function emptyReadResult()'));
assert.ok(definition, 'test the installed transformation, not a copied implementation');
const before = new Function(definition[0] + '; return emptyReadResult;')();
const after = new Function(definition[1] + '; return offsetBeyondEofReadResult;')();

test('baseline discards the file boundary and reports an empty success', () => {
  const result = before();
  assert.equal(result.content[0].text, '');
  assert.notEqual(result.isError, true);
});

test('out-of-range read preserves the actual offset and total line count as an error', () => {
  for (const [offset, lines] of [[400, 341], [500, 341], [2, 1], [9001, 9000]]) {
    const error = new Error(`Offset ${offset} is beyond end of file (${lines} lines total)`);
    const result = after(error);
    assert.equal(result.isError, true);
    assert.equal(result.details.code, 'READ_OFFSET_BEYOND_EOF');
    assert.equal(result.details.status, 'error');
    assert.ok(result.content[0].text.startsWith(error.message));
    assert.match(result.content[0].text, /1-based line number/);
    assert.match(result.content[0].text, /increasing the offset will not return more text/);
    assert.equal(result.content.length, 1);
  }
});

test('repair is selected by both Linux/WSL installation and native macOS composition', () => {
  const linux = readFileSync(new URL('../../../../installers/lib/pixel-host-install.sh', import.meta.url), 'utf8');
  const mac = readFileSync(new URL('../../../../installers/macos/lib/pixel-runtime-bundle.py', import.meta.url), 'utf8');
  assert.match(linux, /--openclaw-bin "\$openclaw_bin" --read-range/);
  assert.match(linux, /ods-runtime-patches\/read-range/);
  assert.match(mac, /\('openclaw-read-range\.json', 'openclaw-tools-iHHy99PD\.js'\)/);
});
