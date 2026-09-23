#!/usr/bin/env node
// Check every Markdown file that Git would publish, including new nonignored files.
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { checkLinks } from '../vendor/pixel/scripts/docs/check-links.mjs';

export function publicFiles(root) {
  return [...new Set(execFileSync('git', ['ls-files', '-z', '--cached', '--others', '--exclude-standard'], { cwd: root, encoding: 'utf8' }).split('\0'))]
    .filter((file) => file && !/(?:^|\/)(?:\.git|node_modules)(?:\/|$)/u.test(file) && !file.startsWith('output/'))
    // Deleted tracked files will not ship; links to them must still fail.
    .filter((file) => fs.existsSync(path.join(root, file)))
    .sort();
}

export function checkPublicDocumentation(root) {
  const publishedFiles = publicFiles(root);
  const files = publishedFiles.filter((file) => /\.md$/iu.test(file));
  if (!files.length) throw new Error('No publishable Markdown files found');
  return checkLinks(root, { files, publishedFiles, requireDirectoryReadme: false });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
  try { process.stdout.write(`${JSON.stringify(checkPublicDocumentation(root))}\n`); }
  catch (error) { process.stderr.write(`${error.message}\n`); process.exitCode = 1; }
}
