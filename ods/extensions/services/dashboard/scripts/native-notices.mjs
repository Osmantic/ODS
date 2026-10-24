import fs from 'node:fs/promises'
import path from 'node:path'
import {fileURLToPath} from 'node:url'

const root=fileURLToPath(new URL('../',import.meta.url))
const lock=JSON.parse(await fs.readFile(path.join(root,'package-lock.json'),'utf8'))
const notices=['ODS native Portal frontend dependency notices.\nInstalled dependency versions follow package-lock.json. This inventory also retains notices for build/test dependencies; it is not a claim that all are included in the runtime bundle.\n']
for(const entry of Object.keys(lock.packages).filter(name=>name.startsWith('node_modules/')).sort()) {
  const directory=path.join(root,entry)
  let files
  try {files=await fs.readdir(directory)} catch(error) {if(error.code==='ENOENT')continue;throw error}
  const metadata=JSON.parse(await fs.readFile(path.join(directory,'package.json'),'utf8'))
  notices.push(`\n=== ${metadata.name} ${metadata.version} (${metadata.license || 'see package notices'}) ===\n`)
  for(const file of files.filter(name=>/^(?:licen[cs]e|copying|notice)(?:[.-]|$)/i.test(name))) {
    const info=await fs.stat(path.join(directory,file))
    if(info.isFile())notices.push(`${file}\n${await fs.readFile(path.join(directory,file),'utf8')}\n`)
  }
}
await fs.writeFile(path.join(root,'dist/native/LICENSE-FRONTEND-DEPENDENCIES.txt'),notices.join('\n'),'utf8')
