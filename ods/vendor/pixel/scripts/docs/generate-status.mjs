import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  compareSemver,
  generatedNotice,
  markdownEscape,
  modeFromArgs,
  readJson,
  readText,
  repoRoot,
  writeGenerated,
} from './lib.mjs';

export function buildStatus(root = repoRoot) {
  const version = readText('VERSION', root).trim();
  const manifest = readJson('RELEASE-MANIFEST.json', root);
  const qualification = readJson('QUALIFICATION-MATRIX.json', root);
  const compatibility = readJson('OPENCLAW-COMPATIBILITY.json', root);
  if (manifest.pixel !== version) throw new Error(`VERSION ${version} does not match RELEASE-MANIFEST.json ${manifest.pixel}`);
  if (JSON.stringify(manifest.supportedHosts) !== JSON.stringify(qualification.supportedHosts)) {
    throw new Error('supportedHosts differs between RELEASE-MANIFEST.json and QUALIFICATION-MATRIX.json');
  }
  const currentRows = compatibility.combinations.filter((row) => row.pixel === version);
  if (currentRows.length !== 1) throw new Error(`expected one compatibility row for Pixel ${version}; found ${currentRows.length}`);
  const current = currentRows[0];
  if (current.openclaw !== manifest.openclaw) throw new Error('current compatibility row does not match the manifest OpenClaw version');
  const supportedRows = compatibility.combinations.filter((row) => row.status === 'supported');
  if (supportedRows.length === 0) throw new Error('compatibility matrix has no supported row');
  const latestSupported = supportedRows.sort((a, b) => compareSemver(b.pixel, a.pixel))[0];
  const runtimeEnabled = manifest.deepWorkCapability?.runtimeEnabled;
  if (typeof runtimeEnabled !== 'boolean') throw new Error('RELEASE-MANIFEST.json deepWorkCapability.runtimeEnabled must be boolean');
  return {
    schemaVersion: 1,
    generatedFrom: ['VERSION', 'RELEASE-MANIFEST.json', 'QUALIFICATION-MATRIX.json', 'OPENCLAW-COMPATIBILITY.json'],
    repository: {
      pixelVersion: version,
      compatibilityStatus: current.status,
      qualifiedAt: current.qualifiedAt,
      evidenceSourceCommit: current.evidence?.sourceCommit,
      evidenceDocument: current.evidence?.liveAudit,
    },
    latestSupportedCompatibility: {
      pixelVersion: latestSupported.pixel,
      qualifiedAt: latestSupported.qualifiedAt,
      evidenceSourceCommit: latestSupported.evidence?.sourceCommit,
      evidenceDocument: latestSupported.evidence?.liveAudit,
    },
    documentedHostScope: qualification.supportedHosts,
    capabilityProfiles: qualification.capabilityProfiles.map((profile) => profile.id),
    deepWork: {
      runtimeEnabled,
      featureStatus: runtimeEnabled ? 'source-enabled-unqualified' : 'development-disabled',
      admissionBoundary: manifest.deepWorkCapability.admissionBoundary,
    },
    releaseUpdate: {
      channel: manifest.releaseUpdate?.channel,
      qualificationAuthority: manifest.releaseUpdate?.qualificationAuthority,
      publicationRequiresAllGates: manifest.qualification?.publicationRequiresAllGates,
    },
  };
}

export function renderStatus(status) {
  const currentEvidence = path.posix.join('..', status.repository.evidenceDocument);
  const supportedEvidence = path.posix.join('..', status.latestSupportedCompatibility.evidenceDocument);
  return `---
title: Pixel status
doc_type: reference
audience: [owner, operator, contributor, security-reviewer, maintainer]
feature_status: mixed
owners: [documentation, release]
sources_of_truth: [VERSION, RELEASE-MANIFEST.json, QUALIFICATION-MATRIX.json, OPENCLAW-COMPATIBILITY.json]
generated_by: scripts/docs/generate-status.mjs
last_verified_at: generated
---

${generatedNotice}

# Pixel status

This page reports repository and release-contract facts. It does **not** report what is installed or active on any live host.

| Surface | Source-derived status | Evidence boundary |
|---|---|---|
| Repository version | \`${markdownEscape(status.repository.pixelVersion)}\` | \`VERSION\` and \`RELEASE-MANIFEST.json\` agree |
| Repository compatibility row | **${markdownEscape(status.repository.compatibilityStatus)}** | [${markdownEscape(status.repository.evidenceDocument)}](${currentEvidence}) binds source \`${markdownEscape(status.repository.evidenceSourceCommit)}\` |
| OpenClaw compatibility | Canonical release pin is maintained in [OPENCLAW-COMPATIBILITY.json](../OPENCLAW-COMPATIBILITY.json) | This page does not duplicate an authored release pin |
| Latest Supported compatibility row | Pixel \`${markdownEscape(status.latestSupportedCompatibility.pixelVersion)}\` | [${markdownEscape(status.latestSupportedCompatibility.evidenceDocument)}](${supportedEvidence}) |
| Deep Work runtime | **${markdownEscape(status.deepWork.featureStatus)}** | \`deepWorkCapability.runtimeEnabled\` is \`${status.deepWork.runtimeEnabled}\`; source presence and admission do not imply runtime authority |
| Documented host scope | ${status.documentedHostScope.map(markdownEscape).join('; ')} | Manifest and qualification matrix agree; this is not a fresh clean-host qualification |

## Capability profiles

${status.capabilityProfiles.map((profile) => `- \`${markdownEscape(profile)}\``).join('\n')}

## Reading status words

- **Supported** applies only where the compatibility and qualification evidence says it does.
- **Candidate** is not merged, deployed, activated, or live-accepted merely because source or tests exist.
- **Development-disabled** means the source surface may be inspectable while runtime authority remains disabled.
- Generated, synthetic, or content-free evidence proves only the facts named by its contract.

For installed-host truth, use the documented local status and verification commands on that host. Do not infer live state from this repository page.
`;
}

export function generateStatus(mode, root = repoRoot) {
  const status = buildStatus(root);
  writeGenerated('docs/status.json', JSON.stringify(status, null, 2), mode, root);
  writeGenerated('docs/status.md', renderStatus(status), mode, root);
  return status;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) generateStatus(modeFromArgs());
