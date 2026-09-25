import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { selectEvidenceWindow } from '../plugin/web-extract.mjs';

const fixture = fileURLToPath(new URL('./fixtures/web-extract-performance.mjs', import.meta.url));
for (const [name, text, query, matched] of [
  ['missing second keyword', 'alpha '.repeat(166666), 'alpha missing', false],
  ['separated keyword regions', 'alpha '.repeat(80000) + 'z'.repeat(10000) + 'bravo '.repeat(80000), 'alpha bravo', false],
  ['repeated complete evidence', 'alpha and bravo\n'.repeat(62500), 'alpha bravo', true],
]) {
  test(`bounded public extraction settles for ${name}`, () => {
    assert.ok(Buffer.byteLength(text) <= 1_000_000);
    // Separate process so synchronous work cannot block the test's own timer.
    // The base spends ~70 s on the first <1 MB page. This generous 8 s budget
    // includes Node startup; fixed extraction takes milliseconds locally.
    const processResult = spawnSync(process.execPath, [fixture], {
      input: JSON.stringify({ text, query }), encoding: 'utf8', timeout: 8000,
    });
    assert.ifError(processResult.error);
    assert.equal(processResult.status, 0, processResult.stderr);
    const result = JSON.parse(processResult.stdout);
    assert.equal(result.released, true);
    assert.equal(result.result.details.matched, matched);
    if (matched) {
      assert.match(result.result.content[0].text, /alpha and bravo/);
      assert.match(result.result.content[0].text, /untrusted webpage evidence/);
    } else assert.match(result.result.content[0].text, /Do not infer/);
  });
}

test('prefers the window with more query terms and retains original Unicode offsets', () => {
  const complete = 'Needle alpha has bravo, charlie and delta together.';
  const text = 'alpha and bravo with charlie\n' + 'İstanbul background\n'.repeat(1000) + complete + '\n' + 'tail\n'.repeat(1500);
  const result = selectEvidenceWindow(text, 'alpha bravo charlie delta');
  assert.ok(result.text.includes(complete));
  assert.equal(result.matchedQuery, 'alpha + bravo + charlie + delta');
  assert.ok(result.text.length <= 6000);
});

test('counts an overlapping keyword occurrence entirely inside the window', () => {
  const text = 'bravo' + 'x'.repeat(6793) + 'aaaaaa' + 'x'.repeat(1196) + 'bravo and charlie';
  const result = selectEvidenceWindow(text, 'bravo aaaa charlie');
  assert.ok(result.text.startsWith('aaaa'), 'the second bravo window starts inside the earlier overlapping match');
  assert.equal(result.matchedQuery, 'bravo + aaaa + charlie');
});

test('a term beginning after the evidence boundary cannot be counted', () => {
  assert.equal(selectEvidenceWindow('alpha ' + 'x'.repeat(5993) + 'bravo', 'alpha bravo'), null);
  // Moving the second term inside a window must still produce evidence.
  assert.ok(selectEvidenceWindow('alpha ' + 'x'.repeat(100) + 'bravo', 'alpha bravo'));
});
