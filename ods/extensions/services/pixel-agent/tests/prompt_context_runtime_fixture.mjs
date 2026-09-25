// Build an owned runtime copy from enumerated source states; never patch the
// supplied installed package. This is fixture preparation, not an installer.
import {cpSync,existsSync,mkdirSync,readFileSync,symlinkSync,writeFileSync} from 'node:fs';
import {basename,dirname,join} from 'node:path';
import {createHash} from 'node:crypto';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const repairs = [
  ['openclaw-compaction-budget.json','selection-BEwSQKM-.js'],
  ['openclaw-prompt-context-hook.json','hook-runner-global-mWFYlTIy.js'],
  ['openclaw-prompt-context-forward.json','attempt.prompt-helpers-Cjcf83Hq.js'],
  ['openclaw-prompt-context-runtime.json','system-prompt-config-CK1eJh37.js'],
];
export function preparePromptContextRuntime(source,root) {
  const packageInfo=JSON.parse(readFileSync(join(source,'package.json'),'utf8'));
  if(packageInfo.name!=='openclaw'||packageInfo.version!=='2026.6.33')throw Error('Unknown context fixture runtime');
  const destination=join(root,'sdk','openclaw');mkdirSync(dirname(destination),{recursive:true});
  cpSync(source,destination,{recursive:true,filter:path=>path!==join(source,'node_modules')});
  if(existsSync(join(source,'node_modules')))symlinkSync(join(source,'node_modules'),join(destination,'node_modules'),'junction');
  const parentDependencies=basename(dirname(source))==='node_modules'?dirname(source):join(dirname(source),'node_modules');
  if(existsSync(parentDependencies))symlinkSync(parentDependencies,join(root,'sdk','node_modules'),'junction');
  const receipts=[];
  for(const [name,module] of repairs){
    const manifest=JSON.parse(readFileSync(new URL('../host/'+name,import.meta.url),'utf8'));
    const originalBytes=readFileSync(join(source,'dist',module));
    const originalHash=hash(originalBytes);let text=originalBytes.toString('utf8');
    const reverse=originalHash===manifest.patchedSha256?manifest.replacements:manifest.previousReplacements?.[originalHash];
    if(originalHash!==manifest.sourceSha256){
      if(!reverse)throw Error('Unknown context fixture module: '+module);
      for(const [before,after] of [...reverse].reverse()){
        if(text.split(after).length!==2)throw Error('Nonunique predecessor');text=text.replace(after,before);
      }
    }
    if(hash(text)!==manifest.sourceSha256)throw Error('Context fixture source hash mismatch');
    for(const [before,after] of manifest.replacements){
      if(text.split(before).length!==2)throw Error('Nonunique context repair');text=text.replace(before,after);
    }
    if(hash(text)!==manifest.patchedSha256)throw Error('Context fixture candidate hash mismatch');
    writeFileSync(join(destination,'dist',module),text);
    if(hash(readFileSync(join(source,'dist',module)))!==originalHash)throw Error('Source runtime changed');
    receipts.push({module,sourceObserved:originalHash,reviewedSource:manifest.sourceSha256,candidate:manifest.patchedSha256});
  }
  writeFileSync(join(root,'runtime-custody.json'),JSON.stringify(receipts,null,2));
  return destination;
}
