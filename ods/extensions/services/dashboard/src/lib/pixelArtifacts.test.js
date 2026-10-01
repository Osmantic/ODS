import {isArtifactPath} from './pixelArtifacts'

it('accepts framework asset paths without admitting traversal or reserved routes',()=>{
  for(const path of ['_next/static/app.js','app/[slug]/page.js','app/[[...slug]]/page.js'])
    expect(isArtifactPath(path)).toBe(true)
  for(const path of ['../file.js','app/../file.js','/file.js','app\\file.js',
    '__ods_manifest__.json','app/__ods_source__/file.js','app/__pycache__/file.js','.env','app/%2e%2e/file.js'])
    expect(isArtifactPath(path)).toBe(false)
})
