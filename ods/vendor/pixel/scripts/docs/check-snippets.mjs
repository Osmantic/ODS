import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { extractCli } from './generate-cli-reference.mjs';
import { readText, repoRoot, walkFiles } from './lib.mjs';

const secretPatterns = [
  /-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----/u,
  /\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b/u,
  /\bsk-[A-Za-z0-9]{20,}\b/u,
  /\bAKIA[0-9A-Z]{16}\b/u,
];

export function checkSnippets(root = repoRoot) {
  const commands = new Set(extractCli(root).map((entry) => entry.command));
  const files = walkFiles('docs', root).filter((file) => file.endsWith('.md'));
  const errors = [];
  let snippets = 0;
  for (const file of files) {
    const text = readText(file, root);
    for (const pattern of secretPatterns) if (pattern.test(text)) errors.push(`${file}: documentation appears to contain a credential or private key`);
    for (const match of text.matchAll(/^```([^\n]*)\n([\s\S]*?)^```\s*$/gmu)) {
      snippets += 1;
      const language = match[1].trim().split(/\s+/u)[0].toLowerCase();
      const body = match[2];
      if (language === 'json') {
        try { JSON.parse(body); } catch (error) { errors.push(`${file}: invalid JSON snippet: ${error.message}`); }
      }
      for (const commandMatch of body.matchAll(/(?:^|\s)\.\/pixel\s+([a-z0-9][a-z0-9-]*)/gmu)) {
        if (!commands.has(commandMatch[1])) errors.push(`${file}: snippet uses unknown Pixel command ${commandMatch[1]}`);
      }
      if (/\bsudo\b|\brm\s+-r|--confirm\b/u.test(body) && !/non-executable|review before running|requires exact confirmation/iu.test(match[1] + body)) {
        errors.push(`${file}: privileged or destructive snippet must be explicitly marked for review`);
      }
    }
  }
  if (errors.length) throw new Error(`documentation snippet check failed:\n${errors.map((error) => `- ${error}`).join('\n')}`);
  return { files: files.length, snippets };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.stdout.write(`${JSON.stringify(checkSnippets())}\n`);
