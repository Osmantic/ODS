import {screen} from '@testing-library/react'
import {render} from '../test/test-utils'
import PixelSnapshotChanges, {validateSnapshotChanges} from './PixelSnapshotChanges'
const preview={siteId:`site-${'a'.repeat(24)}`,sha256:'a'.repeat(64)}
const before={siteId:`site-${'b'.repeat(24)}`,sha256:'b'.repeat(64)}
const value={schemaVersion:1,scope:'published-snapshots',siteId:preview.siteId,sha256:preview.sha256,beforeSiteId:before.siteId,beforeSha256:before.sha256,changes:[{path:'index.html',change:'modified',additions:1,deletions:1,truncated:false,diff:[{type:'remove',oldLine:1,newLine:null,text:'Old'},{type:'add',oldLine:null,newLine:1,text:'New'}]}]}
afterEach(()=>vi.unstubAllGlobals())
it('binds comparison to both verified publications',()=>{
  expect(validateSnapshotChanges(value,preview,before)).toEqual(value.changes)
  for(const invalid of [{...value,beforeSha256:preview.sha256},{...value,siteId:before.siteId},{...value,changes:[{...value.changes[0],path:'../secret'}]},{...value,changes:[{...value.changes[0],additions:-1}]}]) expect(()=>validateSnapshotChanges(invalid,preview,before)).toThrow()
})
it('loads verified counts rather than parsing the reply',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,headers:new Map(),arrayBuffer:async()=>new TextEncoder().encode(JSON.stringify(value)).buffer}))
  render(<PixelSnapshotChanges preview={preview} before={before}/> )
  expect(await screen.findByText('Edited index.html')).toBeVisible()
  expect(screen.getAllByLabelText('1 lines added, 1 lines removed')).toHaveLength(2)
})

it('rejects multiline and NUL-bearing diff rows without rejecting ordinary source',()=>{
  for (const text of ['a\nb', 'a\rb', 'a\0b']) {
    const changes = [{...value.changes[0], diff:[{type:'add',oldLine:null,newLine:1,text}]}]
    expect(()=>validateSnapshotChanges({...value,changes},preview,before)).toThrow('Invalid diff')
  }
  expect(validateSnapshotChanges(value,preview,before)).toEqual(value.changes)
})
