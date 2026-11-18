import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

// Execute the actual pinned and repaired function blocks, including their
// legacy UTF-8 tail helper. Only encoding discovery is replaced: these tests
// select the encoding explicitly and do not invoke a Windows shell.
const manifest = JSON.parse(readFileSync(new URL('../host/openclaw-utf8-output.json', import.meta.url)));
const [[pinnedBlock, repairedBlock]] = manifest.replacements;
function factory(block) {
  return vm.runInNewContext(`${block}\ncreateWindowsOutputDecoder`, {
    Buffer, TextDecoder, process,
    normalizeLowercaseStringOrEmpty: value => typeof value === 'string' ? value.trim().toLowerCase() : '',
    resolveWindowsConsoleEncoding: () => { throw new Error('test must select the Windows encoding'); },
  });
}
const pinned = factory(pinnedBlock), repaired = factory(repairedBlock);
const configurations = [
  {platform: 'linux'}, {platform: 'darwin'},
  {platform: 'win32', windowsEncoding: 'utf-8'},
];
const decode = (create, config, chunks) => {
  const decoder = create(config);
  return chunks.map(chunk => decoder.decode(chunk)).join('') + decoder.flush();
};

test('pinned decoder reproduces real café split while whole-buffer control passes', () => {
  const chunks = [Buffer.from('636166c3', 'hex'), Buffer.from('a90a', 'hex')];
  assert.equal(decode(pinned, configurations[0], chunks), 'caf��\n');
  assert.equal(decode(pinned, configurations[0], [Buffer.concat(chunks)]), 'café\n');
  assert.equal(decode(repaired, configurations[0], chunks), 'café\n');
});

for (const config of configurations) {
  const label = JSON.stringify(config);
  test(`${label}: all byte boundaries preserve 2/3/4-byte characters and BOMs`, () => {
    for (const value of ['café\n', '漢字', '🙂', '\uFEFFcafé漢🙂\uFEFF', 'a\u0000b']) {
      const bytes = Buffer.from(value);
      for (let split = 0; split <= bytes.length; split++) {
        assert.equal(decode(repaired, config, [bytes.subarray(0, split), bytes.subarray(split)]), value);
      }
      assert.equal(decode(repaired, config, [...bytes].map(byte => Uint8Array.of(byte))), value);
    }
  });

  test(`${label}: pending tail is emitted once on flush`, () => {
    const decoder = repaired(config);
    assert.equal(decoder.decode(Buffer.from('61e2', 'hex')), 'a');
    assert.equal(decoder.decode(Buffer.from('82', 'hex')), '');
    assert.equal(decoder.flush(), '�');
    assert.equal(decoder.flush(), '');
  });

  test(`${label}: invalid bytes match whole-buffer replacement semantics`, () => {
    for (const hex of ['80', 'c0af', 'eda080', 'f4908080', '61e228a1', 'c341e282', 'efbb']) {
      const bytes = Buffer.from(hex, 'hex');
      const expected = bytes.toString('utf8');
      for (let split = 0; split <= bytes.length; split++) {
        assert.equal(decode(repaired, config, [bytes.subarray(0, split), bytes.subarray(split)]), expected);
      }
    }
  });

  test(`${label}: stdout and stderr retain independent pending bytes`, () => {
    const stdout = repaired(config), stderr = repaired(config);
    assert.equal(stdout.decode(Buffer.from('636166c3', 'hex')), 'caf');
    assert.equal(stderr.decode(Buffer.from('f09f', 'hex')), '');
    assert.equal(stdout.decode(Buffer.from('a9', 'hex')), 'é');
    assert.equal(stderr.decode(Buffer.from('9982', 'hex')), '🙂');
    assert.equal(stdout.flush(), '');
    assert.equal(stderr.flush(), '');
  });
}

test('Windows legacy fallback, valid UTF-8 preference and incomplete-tail behavior stay unchanged', () => {
  for (const windowsEncoding of ['windows-1252', 'shift_jis']) {
    const config = {platform: 'win32', windowsEncoding};
    for (const hex of ['636166e9', 'c3a9', 'efbbbf61', '61e282', '82a0', 'c341e9']) {
      const bytes = Buffer.from(hex, 'hex');
      for (let split = 0; split <= bytes.length; split++) {
        const chunks = [bytes.subarray(0, split), bytes.subarray(split)];
        assert.equal(decode(repaired, config, chunks), decode(pinned, config, chunks));
      }
    }
  }
  assert.equal(decode(repaired, {platform: 'win32', windowsEncoding: 'windows-1252'}, [Buffer.from('636166e9', 'hex')]), 'café');
});
