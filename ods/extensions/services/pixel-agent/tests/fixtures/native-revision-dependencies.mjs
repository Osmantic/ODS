// Test-only current repair dependencies for fixtures that load current selection.
// Return verified bytes; callers redirect imports or write ONLY their SDK copy.
// Their own selection recipe (including historical red controls) stays separate.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import path from 'node:path';

const FAMILY = [
  ['plugin-tool-version', 'tools-D5HS8Q_I.js'],
  ['finalize-tool-scope', 'lifecycle-hook-helpers-Ck-WCTzd.js'],
  ['yield-usage', 'embedded-agent-CJx-nG3W.js'],
  ['diagnostic-stream-writes', 'attempt.model-diagnostic-events-DqqiPQPY.js'],
];
const sha = value => createHash('sha256').update(value).digest('hex');
const recipe = name => JSON.parse(readFileSync(new URL(`../../host/openclaw-${name}.json`, import.meta.url)));

export function reviewedNativeRevisionDependencies(packageRoot, {withToolCatalog = false} = {}) {
  assert.equal(JSON.parse(readFileSync(path.join(packageRoot, 'package.json'))).version, '2026.6.33');
  const sources = new Map();
  const dependencies = withToolCatalog ? [...FAMILY, ['image-envelope', 'tool-search-BInRpkE3.js']] : FAMILY;
  for (const [name, module] of dependencies) {
    const manifest = recipe(name);
    const filename = path.join(packageRoot, 'dist', module);
    let source = readFileSync(filename, 'utf8');
    const digest = sha(source);
    const prior = digest === manifest.patchedSha256 ? manifest.replacements : manifest.previousReplacements?.[digest];
    if (prior) for (const [before, after] of [...prior].reverse()) {
      assert.equal(source.split(after).length, 2, `${module}: reviewed prior anchor is unique`);
      source = source.replace(after, () => before);
    }
    assert.equal(sha(source), manifest.sourceSha256, `${module}: reject unknown native bytes`);
    for (const [before, after] of manifest.replacements) {
      assert.equal(source.split(before).length, 2, `${module}: reviewed source anchor is unique`);
      source = source.replace(before, () => after);
    }
    assert.equal(sha(source), manifest.patchedSha256, `${module}: current production final bytes`);
    sources.set(filename, source);
  }
  // Selection's diagnostic dependency is repaired above. Its other fixed code
  // dependencies remain the exact pinned read-only files, never arbitrary stubs.
  for (const [module, digest] of Object.entries(recipe('compaction-budget').reviewedDependencies)) {
    const filename = path.join(packageRoot, 'dist', module);
    assert.equal(sha(sources.get(filename) ?? readFileSync(filename)), digest,
      `${module}: current selection dependency bytes`);
  }
  return sources;
}
