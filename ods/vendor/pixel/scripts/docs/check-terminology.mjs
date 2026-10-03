import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseFrontMatter, readText, repoRoot, walkFiles } from './lib.mjs';

const inflatedClaims = [
  /\bproduction[- ]ready\b/iu,
  /\bfully supported\b/iu,
  /\blive[- ]accepted\b/iu,
  /\blive[- ]ready\b/iu,
  /\bsynthetic(?:ally)? (?:proves|establishes) (?:support|readiness|parity)\b/iu,
];

export function checkTerminology(root = repoRoot) {
  const files = walkFiles('docs', root).filter((file) => file.endsWith('.md'));
  const errors = [];
  for (const file of files) {
    const text = readText(file, root);
    const { metadata, body } = parseFrontMatter(text, file);
    const generated = text.includes('GENERATED FILE');
    if (!generated && !['historical', 'release-evidence'].includes(metadata.doc_type)) {
      const literalVersions = [...body.matchAll(/\b\d+\.\d+\.\d+\b/gu)].map((match) => match[0]);
      if (literalVersions.length) errors.push(`${file}: maintained prose hardcodes release/version values (${[...new Set(literalVersions)].join(', ')}); link to generated status or a source contract`);
    }
    if (!generated) for (const pattern of inflatedClaims) if (pattern.test(body)) errors.push(`${file}: contains prohibited status inflation matching ${pattern}`);
  }
  if (errors.length) throw new Error(`documentation terminology check failed:\n${errors.map((error) => `- ${error}`).join('\n')}`);
  return { files: files.length };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.stdout.write(`${JSON.stringify(checkTerminology())}\n`);
