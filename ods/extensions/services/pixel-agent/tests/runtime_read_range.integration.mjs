// OPENCLAW_PACKAGE_DIR must be an absolute path to the pinned runtime package.
// Both wrappers run from hash-verified runtime bytes; candidate edits stay in memory.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {isAbsolute, join} from 'node:path';
import {pathToFileURL} from 'node:url';

const packageDir = process.env.OPENCLAW_PACKAGE_DIR;
if (!packageDir || !isAbsolute(packageDir)) {
  throw new Error('Set OPENCLAW_PACKAGE_DIR to the absolute path of the pinned OpenClaw package.');
}
const manifest = JSON.parse(readFileSync(new URL('../host/openclaw-read-range.json', import.meta.url), 'utf8'));
assert.equal(manifest.version, '2026.6.33');
assert.equal(JSON.parse(readFileSync(join(packageDir, 'package.json'), 'utf8')).version, manifest.version);
const modulePath = join(packageDir, 'dist/openclaw-tools-iHHy99PD.js');
const sessionsPath = join(packageDir, 'dist/sessions-CZbwb3_c.js');
const installedSource = readFileSync(modulePath, 'utf8');
const sha256 = source => createHash('sha256').update(source).digest('hex');
const sessionsHash = sha256(readFileSync(sessionsPath));

function transform(source, reverse = false) {
  assert.ok(Array.isArray(manifest.replacements) && manifest.replacements.length > 0);
  const replacements = reverse ? [...manifest.replacements].reverse() : manifest.replacements;
  for (const pair of replacements) {
    assert.equal(pair.length, 2);
    const [before, after] = reverse ? [...pair].reverse() : pair;
    assert.equal(typeof before, 'string');
    assert.equal(typeof after, 'string');
    assert.ok(before.length > 0 && after.length > 0);
    assert.equal(source.split(before).length, 2, 'each reviewed replacement must match exactly once');
    source = source.replace(before, () => after);
  }
  return source;
}

const installedHash = sha256(installedSource);
assert.ok([manifest.sourceSha256, manifest.patchedSha256].includes(installedHash),
  `Unreviewed runtime module SHA-256: ${installedHash}`);
const originalSource = installedHash === manifest.sourceSha256 ? installedSource : transform(installedSource, true);
const candidateSource = transform(originalSource);
assert.equal(sha256(originalSource), manifest.sourceSha256);
assert.equal(sha256(candidateSource), manifest.patchedSha256);
assert.equal(transform(candidateSource, true), originalSource, 'the manifest must reverse byte for byte');

function extractPaging(source) {
  const start = 'const DEFAULT_READ_PAGE_MAX_BYTES =';
  const end = 'function rewriteReadImageHeader(';
  assert.equal(source.split(start).length, 2);
  assert.equal(source.split(end).length, 2);
  assert.ok(source.indexOf(end) > source.indexOf(start));
  const region = source.slice(source.indexOf(start), source.indexOf(end));
  return new Function(`${region}\nreturn {executeReadPage, executeReadWithAdaptivePaging, resolveAdaptiveReadMaxBytes};`)();
}

const original = extractPaging(originalSource);
const candidate = extractPaging(candidateSource);
const textOf = result => result.content.filter(part => part.type === 'text').map(part => part.text).join('\n');
const lines = count => Array.from({length: count}, (_, index) => `line ${index + 1}`);

function read(runtime, base, args, options = {}) {
  return runtime.executeReadWithAdaptivePaging({
    base, args, toolCallId: 'read-range-fixture', signal: options.signal,
    maxBytes: options.maxBytes ?? runtime.resolveAdaptiveReadMaxBytes(),
  });
}

function observed(base, beforeRead = () => {}) {
  const calls = [];
  const results = [];
  return {
    calls, results,
    tool: {...base, async execute(id, args, signal) {
      calls.push({...args});
      await beforeRead(calls.length, args, signal);
      const result = await base.execute(id, args, signal);
      results.push(result);
      return result;
    }},
  };
}

function assertRangeError(result, message) {
  assert.equal(result.isError, true);
  assert.equal(result.details?.status, 'error');
  assert.equal(result.details?.code, 'READ_OFFSET_BEYOND_EOF');
  const text = textOf(result);
  assert.ok(text.includes(message), `must preserve the native bound: ${message}`);
  assert.match(text, /1-based line number/);
  assert.match(text, /range within the file/);
  assert.match(text, /increasing the offset will not return more text/);
}

test('pinned read-range regression with real session file tools', {timeout: 30000}, async t => {
  const temporary = mkdtempSync(join(tmpdir(), 'ods-read-range-'));
  const environment = {
    OPENCLAW_STATE_DIR: temporary,
    OPENCLAW_CONFIG_PATH: join(temporary, 'openclaw.json'),
  };
  const previousEnvironment = Object.fromEntries(Object.keys(environment).map(key => [key, process.env[key]]));
  Object.assign(process.env, environment);
  t.after(() => {
    for (const [key, value] of Object.entries(previousEnvironment)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    rmSync(temporary, {recursive: true, force: true});
    assert.equal(sha256(readFileSync(modulePath)), installedHash, 'installed wrapper bytes must remain unchanged');
    assert.equal(sha256(readFileSync(sessionsPath)), sessionsHash, 'installed base tool bytes must remain unchanged');
  });
  writeFileSync(environment.OPENCLAW_CONFIG_PATH, '{}', {mode: 0o600});
  const {V: createReadTool} = await import(pathToFileURL(sessionsPath).href);
  assert.equal(typeof createReadTool, 'function');
  const base = createReadTool(temporary);
  const fixture = (name, content) => {
    writeFileSync(join(temporary, name), content, {mode: 0o600});
    return name;
  };
  const fileLines = lines(341);
  const lf = fixture('341-lines-lf.txt', fileLines.join('\n'));
  const crlf = fixture('341-lines-crlf.txt', fileLines.join('\r\n'));
  const eofMessage = 'Offset 400 is beyond end of file (341 lines total)';

  await t.test('real base reports 341 lines; original wrapper falsely succeeds with empty text', async () => {
    for (const path of [lf, crlf]) {
      for (const mode of [{limit: 20}, {}]) {
        const args = {path, offset: 400, ...mode};
        await assert.rejects(base.execute('base-eof', args), error => error.message === eofMessage);
        const tracked = observed(base);
        const result = await read(original, tracked.tool, args);
        assert.deepEqual(result, {content: [{type: 'text', text: ''}], details: undefined});
        assert.notEqual(result.isError, true);
        assert.deepEqual(tracked.calls, [args]);
      }
    }
  });

  await t.test('candidate direct page and both paging modes retain actionable EOF evidence', async () => {
    for (const path of [lf, crlf]) {
      for (const mode of [{limit: 20}, {}]) {
        const args = {path, offset: 400, ...mode};
        const page = await candidate.executeReadPage({base, args, toolCallId: 'direct-page'});
        assertRangeError(page, eofMessage);
        const tracked = observed(base);
        const result = await read(candidate, tracked.tool, args);
        assert.deepEqual(result, page);
        assertRangeError(result, eofMessage);
        assert.deepEqual(tracked.calls, [args], 'EOF must not trigger another page');
      }
      assertRangeError(await read(candidate, base, {path, offset: 342}),
        'Offset 342 is beyond end of file (341 lines total)');
    }
  });

  await t.test('valid LF/CRLF reads, explicit limits and the final line stay unchanged', async () => {
    for (const path of [lf, crlf]) {
      for (const selection of [{}, {offset: 1}, {offset: 12, limit: 3}, {offset: 341}, {offset: 341, limit: 1}]) {
        const args = {path, ...selection};
        const direct = await base.execute('ordinary-read', args);
        const result = await read(candidate, base, args);
        assert.deepEqual(result, direct);
        assert.deepEqual(result, await read(original, base, args));
        assert.notEqual(result.isError, true);
        if (selection.offset === 341) assert.equal(textOf(result), 'line 341');
        if (selection.limit === 3) assert.match(textOf(result), /Use offset=15 to continue/);
      }
    }
  });

  await t.test('empty and blank files are successful reads, with native bounds only past EOF', async () => {
    for (const [name, content] of [['empty.txt', ''], ['blank.txt', '\n\n'], ['blank-crlf.txt', '\r\n\r\n']]) {
      const path = fixture(name, content);
      const total = content.split('\n').length;
      for (const mode of [{}, {limit: 10}]) {
        for (const offset of [1, total]) {
          const args = {path, offset, ...mode};
          const result = await read(candidate, base, args);
          assert.deepEqual(result, await base.execute('empty-read', args));
          assert.deepEqual(result, await read(original, base, args));
          assert.notEqual(result.isError, true);
          assert.notEqual(result.details?.code, 'READ_OFFSET_BEYOND_EOF');
        }
        assertRangeError(await read(candidate, base, {path, offset: 400, ...mode}),
          `Offset 400 is beyond end of file (${total} lines total)`);
      }
    }
  });

  await t.test('ordinary file content resembling an EOF error stays successful', async () => {
    const path = fixture('eof-message.txt', eofMessage);
    const result = await read(candidate, base, {path});
    assert.equal(textOf(result), eofMessage);
    assert.notEqual(result.isError, true);
    assert.deepEqual(result, await read(original, base, {path}));
  });

  await t.test('real multi-page text retains every line, page offsets and explicit-limit behavior', async () => {
    const allLines = lines(4501);
    const path = fixture('multiple-pages.txt', allLines.join('\n'));
    const maxBytes = candidate.resolveAdaptiveReadMaxBytes({modelContextWindowTokens: 1000000});
    const tracked = observed(base);
    const result = await read(candidate, tracked.tool, {path}, {maxBytes});
    assert.deepEqual(result, await read(original, base, {path}, {maxBytes}));
    assert.deepEqual(tracked.calls.map(args => args.offset), [1, 2001, 4001]);
    assert.deepEqual(textOf(result).split('\n').filter(Boolean), allLines);
    assert.notEqual(result.isError, true);
    assert.doesNotMatch(textOf(result), /Use offset=\d+ to continue/);
    const limited = observed(base);
    const args = {path, offset: 1, limit: 4501};
    assert.deepEqual(await read(candidate, limited.tool, args), await base.execute('limited', args));
    assert.equal(limited.calls.length, 1, 'an explicit limit must not start adaptive paging');
  });

  await t.test('byte and four-page bounds retain their original continuation behavior', async () => {
    for (const [name, count, maxBytes, expectedOffsets, nextOffset] of [
      ['byte-cap.txt', 4501, candidate.resolveAdaptiveReadMaxBytes(), [1, 2001], 2001],
      ['page-cap.txt', 9001, 128 * 1024, [1, 2001, 4001, 6001], 8001],
    ]) {
      const path = fixture(name, lines(count).join('\n'));
      const tracked = observed(base);
      const result = await read(candidate, tracked.tool, {path}, {maxBytes});
      assert.deepEqual(result, await read(original, base, {path}, {maxBytes}));
      assert.deepEqual(tracked.calls.map(args => args.offset), expectedOffsets);
      assert.ok(textOf(result).includes(`Use offset=${nextOffset} to continue.`));
      assert.notEqual(result.isError, true);
    }
  });

  await t.test('a first line beyond the base byte limit keeps its original diagnostic', async () => {
    const path = fixture('long-line.txt', 'x'.repeat(60 * 1024));
    const tracked = observed(base);
    const result = await read(candidate, tracked.tool, {path});
    assert.deepEqual(result, await read(original, base, {path}));
    assert.equal(result.details.truncation.firstLineExceedsLimit, true);
    assert.equal(tracked.calls.length, 1);
    assert.notEqual(result.isError, true);
  });

  await t.test('real PNG reads preserve image bytes, MIME, ordering and text with either paging mode', async () => {
    const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jXioAAAAASUVORK5CYII=', 'base64');
    const path = fixture('one-pixel.png', png);
    const imageBase = createReadTool(temporary, {autoResizeImages: false});
    for (const mode of [{}, {limit: 1}, {offset: 400}]) {
      const args = {path, ...mode};
      const tracked = observed(imageBase);
      const result = await read(candidate, tracked.tool, args);
      assert.deepEqual(result, await imageBase.execute('image', args));
      assert.deepEqual(result, await read(original, imageBase, args));
      assert.deepEqual(result.content.map(part => part.type), ['text', 'image']);
      assert.equal(result.content[1].mimeType, 'image/png');
      assert.deepEqual(Buffer.from(result.content[1].data, 'base64'), png);
      assert.equal(result.content[1], tracked.results[0].content[1]);
      assert.equal(tracked.calls.length, 1);
      assert.notEqual(result.isError, true);
    }
  });

  await t.test('missing-file and pre-aborted reads still reject in direct and implicit paging', async () => {
    for (const runtime of [original, candidate]) {
      for (const mode of [{limit: 10}, {}]) {
        await assert.rejects(read(runtime, base, {path: 'missing.txt', offset: 400, ...mode}),
          error => error.code === 'ENOENT');
        const controller = new AbortController();
        controller.abort();
        await assert.rejects(read(runtime, base, {path: lf, offset: 400, ...mode}, {signal: controller.signal}),
          error => error.message === 'Operation aborted');
      }
    }
  });

  await t.test('permission errors from real tool operations are not converted to EOF', async () => {
    const path = fixture('unreadable.txt', fileLines.join('\n'));
    // Inject access failures because Windows and root do not enforce chmod(0) alike.
    for (const code of ['EACCES', 'EPERM']) {
      const permission = Object.assign(new Error(`${code}: permission denied`), {code});
      const denied = createReadTool(temporary, {operations: {
        access: async () => { throw permission; },
        readFile: async absolutePath => readFileSync(absolutePath),
      }});
      for (const runtime of [original, candidate]) {
        for (const mode of [{limit: 10}, {}]) {
          await assert.rejects(read(runtime, denied, {path, offset: 400, ...mode}),
            error => error === permission && error.code === code);
        }
      }
    }
  });

  await t.test('optional missing daily-memory behavior is unchanged', async () => {
    const args = {path: 'memory/2026-06-01.md', offset: 400};
    const result = await read(candidate, base, args);
    assert.deepEqual(result, await read(original, base, args));
    assert.equal(result.details.status, 'not_found');
    assert.equal(result.details.optional, true);
    assert.notEqual(result.isError, true);
  });

  await t.test('internal EOF after a real file shrinks preserves partial text and remains an error even over budget', async () => {
    const before = lines(2001);
    const partialText = before.slice(0, 2000).join('\n');
    for (const maxBytes of [128 * 1024, Buffer.byteLength(partialText) + 1]) {
      for (const runtime of [original, candidate]) {
        const path = fixture('shrinking.txt', before.join('\n'));
        const tracked = observed(base, count => {
          if (count === 2) writeFileSync(join(temporary, path), fileLines.join('\n'));
        });
        const result = await read(runtime, tracked.tool, {path}, {maxBytes});
        assert.deepEqual(tracked.calls.map(args => args.offset), [1, 2001]);
        assert.equal(tracked.results.length, 1, 'the second real read must throw');
        assert.equal(readFileSync(join(temporary, path), 'utf8'), fileLines.join('\n'));
        if (runtime === original) {
          assert.equal(textOf(result), partialText);
          assert.notEqual(result.isError, true, 'baseline loses the internal EOF error');
        } else {
          assertRangeError(result, 'Offset 2001 is beyond end of file (341 lines total)');
          assert.ok(textOf(result).startsWith(`${partialText}\n\nOffset 2001`));
          assert.deepEqual(textOf(result).slice(0, partialText.length).split('\n'), before.slice(0, 2000));
          assert.doesNotMatch(textOf(result), /Use offset=\d+ to continue|Read output capped/);
        }
      }
    }
  });
});
