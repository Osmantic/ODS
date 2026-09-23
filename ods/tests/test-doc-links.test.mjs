import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import test from 'node:test';
import { checkPublicDocumentation, publicFiles } from '../scripts/check-doc-links.mjs';
import { extractMarkdownLinks, markdownAnchors } from '../vendor/pixel/scripts/docs/check-links.mjs';

function repository(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-doc-links-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  execFileSync('git', ['init', '--quiet', root]);
  execFileSync('git', ['config', 'core.autocrlf', 'false'], { cwd: root });
  return root;
}

function write(root, file, contents) {
  fs.mkdirSync(path.dirname(path.join(root, file)), { recursive: true });
  fs.writeFileSync(path.join(root, file), contents);
}

test('Git inventory covers root, security, extensions, vendor and untracked docs, excluding build artifacts', (t) => {
  const root = repository(t);
  write(root, '.gitignore', 'ignored/\n');
  const files = ['README.md', 'SECURITY.md', 'ods/extensions/new/README.md', 'ods/vendor/pixel/README.md'];
  for (const file of files) write(root, file, '# Good\n');
  execFileSync('git', ['add', '.'], { cwd: root });
  write(root, 'new doc.md', '[security](SECURITY.md)\n');
  for (const file of ['output/report.md', 'node_modules/pkg/README.md', 'ods/node_modules/pkg/README.md', 'ignored/private.md']) write(root, file, '[bad](missing.md)');
  assert.deepEqual(publicFiles(root).filter((file) => file.endsWith('.md')), [...files, 'new doc.md'].sort());
  assert.deepEqual(checkPublicDocumentation(root), { files: 5, relativeLinks: 1 });
  for (const file of files) {
    write(root, file, '[bad](missing.md)\n');
    assert.throws(() => checkPublicDocumentation(root), /missing relative link target missing.md/u);
    write(root, file, '# Good\n');
  }
});

test('local links validate URI encoding, queries, root paths, images, directories and heading fragments', (t) => {
  const root = repository(t);
  write(root, 'README.md', '# Home\n[one](docs/My%20File.md?plain=1#hello-world)\n[two](docs/My%20File.md#hello-world-1)\n[three](docs/My%20File.md#custom)\n![image](assets/image(1).png)\n[root](/docs/My%20File.md#under_score)\n[dir](assets/)\n[self](#home)\n');
  write(root, 'docs/My File.md', '# Hello **World**\n# Hello World\n# `under_score`\n<a id="custom"></a>\n');
  write(root, 'assets/image(1).png', 'fixture');
  assert.deepEqual(checkPublicDocumentation(root), { files: 2, relativeLinks: 7 });
  write(root, 'README.md', '[bad](docs/My%20File.md#absent)');
  assert.throws(() => checkPublicDocumentation(root), /missing anchor #absent/u);
  write(root, 'README.md', '[bad](docs/%zz.md)');
  assert.throws(() => checkPublicDocumentation(root), /not valid percent-encoding/u);
});

test('reference links, escaped destinations, angle destinations and HTML assets are checked', (t) => {
  const root = repository(t);
  write(root, 'README.md', '[full][target]\n[target][]\n[target]\n[angle](<space name.md> "title")\n[escaped](file\\(1\\).md)\n<img src="image.png">\n\n[target]: <space name.md> "title"\n');
  for (const file of ['space name.md', 'file(1).md', 'image.png']) write(root, file, 'fixture');
  assert.equal(checkPublicDocumentation(root).relativeLinks, 6);
  fs.unlinkSync(path.join(root, 'image.png'));
  assert.throws(() => checkPublicDocumentation(root), /missing relative link target image.png/u);
  write(root, 'image.png', 'fixture');
  fs.unlinkSync(path.join(root, 'space name.md'));
  assert.throws(() => checkPublicDocumentation(root), /missing relative link target space name.md/u);
});

test('code, comments, front matter and URI schemes are excluded without hiding following prose', (t) => {
  const root = repository(t);
  write(root, 'README.md', '---\nexample: "[fake](missing.yaml)"\n---\n# Good\n`[fake](missing1.md)`\n`` [fake](missing2.md) ` ``\n<!-- [fake](missing3.md) -->\n````markdown\n```\n[fake](missing4.md)\n````\n~~~md\n[fake](missing5.md)\n~~~\n[web](https://example.org) [mail](mailto:help@example.org) [app](app:local) [cdn](//example.org/a)\n[real](#good)\n');
  assert.deepEqual(checkPublicDocumentation(root), { files: 1, relativeLinks: 1 });
  fs.appendFileSync(path.join(root, 'README.md'), '[bad](missing-real.md)\n');
  assert.throws(() => checkPublicDocumentation(root), /missing relative link target missing-real.md/u);
});

test('existing ignored/private files and traversal cannot satisfy public links', (t) => {
  const root = repository(t);
  write(root, '.gitignore', 'private/\n');
  write(root, 'private/secret.md', '# Secret\n');
  write(root, 'output/local.md', '# Local\n');
  for (const destination of ['private/secret.md', 'output/local.md']) {
    write(root, 'README.md', `[bad](${destination})\n`);
    assert.throws(() => checkPublicDocumentation(root), /not in the published file inventory/u);
  }
  write(root, 'README.md', '[bad](../outside.md)\n');
  assert.throws(() => checkPublicDocumentation(root), /missing relative link target/u);
});

test('Git tracked ignored files remain public; deleted tracked targets fail', (t) => {
  const root = repository(t);
  write(root, 'README.md', '[target](kept.md)\n');
  write(root, 'kept.md', '# Target\n');
  execFileSync('git', ['add', '.'], { cwd: root });
  write(root, '.gitignore', 'kept.md\n');
  assert.equal(checkPublicDocumentation(root).files, 2);
  fs.unlinkSync(path.join(root, 'kept.md'));
  assert.throws(() => checkPublicDocumentation(root), /missing relative link target kept.md/u);
});

test('heading anchors preserve underscores, repeated hyphens and duplicate IDs', () => {
  assert.deepEqual([...markdownAnchors('# Option B — Per-user Hermes\n# under_score\n# Repeat\n# Repeat\nSetext heading\n==============\n')], ['option-b--per-user-hermes', 'under_score', 'repeat', 'repeat-1', 'setext-heading']);
  assert.deepEqual(extractMarkdownLinks('[text](target(1).md "title")').map((link) => link.destination), ['target(1).md']);
});

test('reference definitions and leading thematic breaks do not hide following links', () => {
  assert.deepEqual(extractMarkdownLinks('[target]: good.md\n  [bad](missing.md)\n[target]\n').map((link) => link.destination), ['missing.md', 'good.md']);
  assert.deepEqual(extractMarkdownLinks('---\n[bad](missing.md)\n').map((link) => link.destination), ['missing.md']);
  assert.deepEqual(extractMarkdownLinks('[target]\n\n[target]:\n  missing.md\n').map((link) => link.destination), ['missing.md']);
  assert.deepEqual(extractMarkdownLinks('`unmatched\n\n[bad](missing.md)\n\n`\n').map((link) => link.destination), ['missing.md']);
  assert.deepEqual(extractMarkdownLinks('\\`[bad](missing.md)\\`\n').map((link) => link.destination), ['missing.md']);
  assert.deepEqual(extractMarkdownLinks('[invalid]: good.md [bad](missing.md)\n').map((link) => link.destination), ['missing.md']);
});

test('inline code context preserves heading links, URL backticks and raw reference labels', () => {
  assert.deepEqual(extractMarkdownLinks('`unmatched\n# [bad](missing.md)\n`').map((link) => link.destination), ['missing.md']);
  assert.deepEqual(extractMarkdownLinks('[bad](docs/`missing`.md)').map((link) => link.destination), ['docs/`missing`.md']);
  assert.deepEqual(extractMarkdownLinks('[bad]\n\n> [bad]: missing.md').map((link) => link.destination), ['missing.md']);
  assert.deepEqual(extractMarkdownLinks('[`good`]\n\n[`bad`]: missing.md').map((link) => link.destination), []);
  assert.deepEqual(extractMarkdownLinks('[`good`]\n\n[`good`]: missing.md').map((link) => link.destination), ['missing.md']);
  assert.deepEqual(extractMarkdownLinks('`<img src="missing.png">`').map((link) => link.destination), []);
});
