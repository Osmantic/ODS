import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { checkAll } from '../scripts/docs/check.mjs';
import { checkLinks } from '../scripts/docs/check-links.mjs';
import { checkMetadata } from '../scripts/docs/check-metadata.mjs';
import { checkNavigation } from '../scripts/docs/check-navigation.mjs';
import { checkRootPolicy } from '../scripts/docs/check-root-policy.mjs';
import { checkSnippets } from '../scripts/docs/check-snippets.mjs';
import { checkTerminology } from '../scripts/docs/check-terminology.mjs';
import { extractCli } from '../scripts/docs/generate-cli-reference.mjs';
import { extractConfigExamples } from '../scripts/docs/generate-config-reference.mjs';
import { extractSchemas } from '../scripts/docs/generate-schema-catalog.mjs';
import { markdownEscape, parseFrontMatter } from '../scripts/docs/lib.mjs';

function temporaryRepository(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pixel-doc-tests-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

function write(root, relative, content) {
  const target = path.join(root, relative);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, content, 'utf8');
}

test('documentation inventories cover the audited source surface', () => {
  const commands = extractCli();
  assert.equal(commands.length, 111);
  assert.equal(commands.filter((entry) => entry.command !== 'help').length, 110);
  assert.equal(new Set(commands.map((entry) => entry.command)).size, commands.length);
  assert.equal(extractSchemas().length, 251);
  assert.equal(extractConfigExamples().length, 42);
});

test('preview-capable mutating commands retain mode and confirmation semantics', () => {
  const reference = JSON.parse(fs.readFileSync(new URL('../docs/reference/cli.json', import.meta.url), 'utf8'));
  for (const command of [
    'action-journal', 'frontier-authority', 'ops-authority', 'update-activate',
    'update-reactivate', 'update-reactivation-rollback', 'update-reactivation-recover',
  ]) {
    const entry = reference.commands.find((candidate) => candidate.command === command);
    assert.ok(entry, `missing generated CLI entry for ${command}`);
    assert.equal(entry.effect, 'mode-dependent; includes non-mutating and state-writing modes');
    assert.equal(entry.confirmation, 'mode/subcommand-dependent; inspect help');
  }
});

test('front matter rejects multiline YAML and accepts the restricted contract', () => {
  const parsed = parseFrontMatter('---\ntitle: Example\naudience: [owner, maintainer]\n---\n\nBody\n', 'fixture.md');
  assert.equal(parsed.metadata.title, 'Example');
  assert.deepEqual(parsed.metadata.audience, ['owner', 'maintainer']);
  assert.throws(() => parseFrontMatter('---\ntitle: Example\naudience:\n  - owner\n---\n', 'fixture.md'), /unsupported front-matter syntax/u);
});

test('Markdown table escaping handles backslashes before pipes and newlines', () => {
  assert.equal(markdownEscape('\\'), '\\\\');
  assert.equal(markdownEscape('|'), '\\|');
  assert.equal(markdownEscape(String.raw`a\|b`), String.raw`a\\\|b`);
  assert.equal(markdownEscape('a\nb'), 'a b');
});

test('the complete documentation contract is green', () => {
  const result = checkAll();
  const compatibility = JSON.parse(fs.readFileSync(new URL('../OPENCLAW-COMPATIBILITY.json', import.meta.url), 'utf8'));
  assert.equal(result.generated.commands, 111);
  assert.equal(result.generated.schemas, 251);
  assert.equal(result.generated.configurationExamples, 42);
  assert.ok(Array.isArray(compatibility.combinations));
  assert.equal(result.generated.releaseEvidenceRows, compatibility.combinations.length);
  assert.ok(result.generated.releaseEvidenceRows >= 25);
  assert.equal(result.generated.unreferencedReleaseAudits, 1);
  assert.equal(result.generated.servicePathEntries, 48);
  assert.equal(result.generated.hostLanes, 4);
  assert.ok(result.generated.indexedMarkdownDocuments > 160);
  assert.equal(result.navigation.files, result.navigation.reachable);
});

test('link checking rejects missing targets and anchors', (t) => {
  const root = temporaryRepository(t);
  write(root, 'docs/index.md', '# Index\n\n[missing](missing.md)\n[anchor](target.md#absent)\n');
  write(root, 'docs/target.md', '# Present\n');
  assert.throws(() => checkLinks(root), /missing relative link target missing\.md/u);
  write(root, 'docs/missing.md', '# Now present\n');
  assert.throws(() => checkLinks(root), /missing anchor #absent/u);
});

test('navigation requires every docs page to be reachable from the hub', (t) => {
  const root = temporaryRepository(t);
  write(root, 'docs/README.md', '# Hub\n\n[Reachable](reachable.md)\n');
  write(root, 'docs/reachable.md', '# Reachable\n');
  write(root, 'docs/island.md', '# Island\n');
  assert.throws(() => checkNavigation(root), /not reachable from docs\/README\.md: docs\/island\.md/u);
  write(root, 'docs/reachable.md', '# Reachable\n\n[Island](island.md)\n');
  assert.deepEqual(checkNavigation(root), { files: 3, reachable: 3 });
});

test('metadata checking rejects absent fields and nonexistent sources', (t) => {
  const root = temporaryRepository(t);
  write(root, 'docs/page.md', '---\ntitle: Incomplete\ndoc_type: reference\naudience: [owner]\nfeature_status: mixed\nowners: [documentation]\nsources_of_truth: [missing.txt]\nlast_verified_at: 2026-08-27\n---\n\n# Incomplete\n');
  assert.throws(() => checkMetadata(root), /source_of_truth does not exist: missing\.txt/u);
});

test('snippet checking rejects invalid JSON and invented Pixel commands', (t) => {
  const root = temporaryRepository(t);
  write(root, 'pixel', `usage() {\n  cat <<'EOF'\nCommands:\n  good Run a good command\n  help Show this help\nEOF\n}\ncase "$command" in\n  good) exec bash "$ROOT/scripts/good.sh" "$@" ;;\n  help|-h|--help) usage ;;\nesac\n`);
  write(root, 'docs/page.md', '# Snippets\n\n```json\n{"broken": }\n```\n\n```bash\n./pixel invented\n```\n');
  assert.throws(() => checkSnippets(root), /invalid JSON snippet/u);
  write(root, 'docs/page.md', '# Snippets\n\n```bash\n./pixel invented\n```\n');
  assert.throws(() => checkSnippets(root), /unknown Pixel command invented/u);
});

test('root and terminology policies reject unreviewed expansion and inflated claims', (t) => {
  const root = temporaryRepository(t);
  write(root, 'scripts/docs/inventory-baseline.json', JSON.stringify({ rootMarkdownAtBaseline: [] }));
  write(root, 'UNREVIEWED.md', '# Unreviewed\n');
  assert.throws(() => checkRootPolicy(root), /new root Markdown requires/u);
  write(root, 'docs/page.md', '---\ntitle: Claim\ndoc_type: concept\naudience: [owner]\nfeature_status: candidate\nowners: [documentation]\nsources_of_truth: [UNREVIEWED.md]\nlast_verified_at: 2026-08-27\n---\n\n# Claim\n\nThis surface is production-ready.\n');
  assert.throws(() => checkTerminology(root), /prohibited status inflation/u);
});
