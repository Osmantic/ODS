import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  compareSemver,
  generatedNotice,
  markdownEscape,
  modeFromArgs,
  readJson,
  repoRoot,
  writeGenerated,
} from './lib.mjs';

export function buildReleaseEvidenceIndex(root = repoRoot) {
  const manifest = readJson('RELEASE-MANIFEST.json', root);
  const compatibility = readJson('OPENCLAW-COMPATIBILITY.json', root);
  const rows = [...(compatibility.combinations ?? [])].sort((left, right) => compareSemver(right.pixel, left.pixel));
  if (rows.length === 0) throw new Error('OPENCLAW-COMPATIBILITY.json has no compatibility rows');

  const referencedAudits = new Set();
  for (const row of rows) {
    if (!row.pixel || !row.openclaw || !row.status || !row.qualifiedAt || !row.evidence?.sourceCommit || !row.evidence?.liveAudit) {
      throw new Error(`compatibility row is missing evidence identity: ${JSON.stringify(row)}`);
    }
    if (!/^[0-9a-f]{40}$/u.test(row.evidence.sourceCommit)) throw new Error(`invalid evidence source commit for Pixel ${row.pixel}`);
    const auditPath = path.join(root, row.evidence.liveAudit);
    if (!fs.existsSync(auditPath) || !fs.statSync(auditPath).isFile()) throw new Error(`missing live audit for Pixel ${row.pixel}: ${row.evidence.liveAudit}`);
    referencedAudits.add(row.evidence.liveAudit);
  }

  const retainedAudits = fs.readdirSync(root)
    .filter((name) => /^LIVE-AUDIT(?:-[0-9]+\.[0-9]+\.[0-9]+)?\.md$/u.test(name))
    .sort((left, right) => left.localeCompare(right));
  const unreferencedAudits = retainedAudits.filter((name) => !referencedAudits.has(name));
  const currentRows = rows.filter((row) => row.pixel === manifest.pixel);
  if (currentRows.length !== 1) throw new Error(`expected one compatibility row for manifest Pixel ${manifest.pixel}; found ${currentRows.length}`);

  return { rows, retainedAudits, unreferencedAudits };
}

export function renderReleaseEvidenceIndex(index) {
  const rows = index.rows.map((row) => {
    const audit = `../../${row.evidence.liveAudit}`;
    return `| \`${markdownEscape(row.pixel)}\` | **${markdownEscape(row.status)}** | ${markdownEscape(row.qualifiedAt)} | [${markdownEscape(row.evidence.liveAudit)}](${audit}) | \`${markdownEscape(row.evidence.sourceCommit)}\` |`;
  }).join('\n');
  const unreferenced = index.unreferencedAudits.length === 0
    ? '- None.'
    : index.unreferencedAudits.map((name) => `- [${markdownEscape(name)}](../../${name})`).join('\n');

  return `---
title: Release evidence index
doc_type: release-evidence
audience: [owner, operator, contributor, security-reviewer, maintainer]
feature_status: mixed
owners: [documentation, release]
sources_of_truth: [RELEASE-MANIFEST.json, OPENCLAW-COMPATIBILITY.json]
generated_by: scripts/docs/generate-release-evidence-index.mjs
last_verified_at: generated
---

${generatedNotice}

# Release evidence index

This is a repository index, not a live-host report. Each row reproduces the compatibility status and evidence identity recorded in [OPENCLAW-COMPATIBILITY.json](../../OPENCLAW-COMPATIBILITY.json). The linked audit must be read at its exact source commit; a retained file, date, or compatibility label does not by itself prove current installation, activation, recovery, publication, or owner acceptance.

| Pixel | Recorded status | Qualified at | Retained audit | Evidence source commit |
|---|---|---|---|---|
${rows}

## Retained audits not referenced by a compatibility row

These files remain historical evidence. Their presence must not be promoted into a compatibility claim.

${unreferenced}

For current source-derived repository status, use [Pixel status](../status.md). For the release process, use [the maintainer runbook](maintainer-runbook.md).
`;
}

export function generateReleaseEvidenceIndex(mode, root = repoRoot) {
  const index = buildReleaseEvidenceIndex(root);
  writeGenerated('docs/releases/evidence-index.md', renderReleaseEvidenceIndex(index), mode, root);
  return index;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) generateReleaseEvidenceIndex(modeFromArgs());
