import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { generateAll } from './generate.mjs';
import { checkLinks } from './check-links.mjs';
import { checkMetadata } from './check-metadata.mjs';
import { checkNavigation } from './check-navigation.mjs';
import { checkRootPolicy } from './check-root-policy.mjs';
import { checkSnippets } from './check-snippets.mjs';
import { checkSourceMap } from './check-source-map.mjs';
import { checkTerminology } from './check-terminology.mjs';
import { repoRoot } from './lib.mjs';

export function checkAll(root = repoRoot) {
  return {
    generated: generateAll('check', root),
    metadata: checkMetadata(root),
    links: checkLinks(root),
    navigation: checkNavigation(root),
    sourceMap: checkSourceMap(root),
    snippets: checkSnippets(root),
    rootPolicy: checkRootPolicy(root),
    terminology: checkTerminology(root),
  };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.stdout.write(`${JSON.stringify(checkAll(), null, 2)}\n`);
