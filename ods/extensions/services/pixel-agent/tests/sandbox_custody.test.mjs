import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const host=path.join(path.dirname(fileURLToPath(import.meta.url)),'../host');
test('distributed Docker repair contains the reviewed helper exactly',()=>{
 const manifest=JSON.parse(fs.readFileSync(path.join(host,'openclaw-sandbox-custody-backend.json'),'utf8'));
 const replacement=manifest.replacements.find(([,after])=>after.includes('const odsExecCustodyScript = '))[1];
 const embedded=replacement.match(/^const odsExecCustodyScript = (.+);\n/)[1];
 assert.equal(JSON.parse(embedded),fs.readFileSync(path.join(host,'sandbox-exec-custody.py'),'utf8').replaceAll('\r\n','\n'));
});
