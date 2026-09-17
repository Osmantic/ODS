import {act,fireEvent,render,screen,waitFor,within} from '@testing-library/react'
import {createHash,webcrypto} from 'node:crypto'
import PortalWorkspace from './PortalWorkspace'
import PixelSnapshotChanges from './PixelSnapshotChanges'
const hash=text=>createHash('sha256').update(text).digest('hex')
const sources={'index.html':'<h1>Demo</h1>','src/app.js':'const answer = 42;','README.md':'# Project\n\n[Source](src/app.js)'}
const files=Object.entries(sources).map(([path,text])=>({path,bytes:new TextEncoder().encode(text).length,sha256:hash(text)}))
const preview={siteId:`site-${'a'.repeat(24)}`,sha256:'a'.repeat(64),entrySha256:hash(sources['index.html']),relativeDirectory:'demo',files:files.length,bytes:files.reduce((n,f)=>n+f.bytes,0)}
const changes=files.map(file=>({path:file.path,change:'published',additions:1,deletions:0,truncated:false,diff:[{type:'add',text:sources[file.path].split('\n')[0],newLine:1,oldLine:null}]}))
const manifest={schemaVersion:1,siteId:preview.siteId,sha256:preview.sha256,files,bytes:preview.bytes}
const comparison={schemaVersion:1,scope:'published-snapshots',siteId:preview.siteId,sha256:preview.sha256,beforeSiteId:null,beforeSha256:null,changes}
const access={url:'/pixel-preview/demo/',frameUrl:'/pixel-preview/demo/',sandbox:'allow-scripts',route:'test'}
const props={preview,previews:[preview],access,title:'Interactive Portal preview',onRefresh:vi.fn(),onClose:vi.fn(),onCollapse:vi.fn(),onExpand:vi.fn()}
const agents={teams:[{id:'a'.repeat(32),goal:'Build a page',status:'completed',agents:[{id:'0',name:'Builder',task:'Build a page',status:'completed',turn:0,conversation:[{role:'assistant',content:'Built the page.'}]}]}],selected:null,select:vi.fn(),answer:vi.fn(),stop:vi.fn(),retry:vi.fn()}
beforeEach(()=>{
 vi.stubGlobal('crypto',webcrypto)
 vi.stubGlobal('fetch',vi.fn(async url=>({ok:true,headers:new Map(),arrayBuffer:async()=>new TextEncoder().encode(url.includes('__ods_manifest__')?JSON.stringify(manifest):url.includes('__ods_changes__')?JSON.stringify(comparison):sources[Object.keys(sources).find(path=>url.endsWith('/'+path))]).buffer})))
})
afterEach(()=>vi.unstubAllGlobals())
it('opens Subagents without a publication and keeps it independent of preview files',async()=>{
 const {rerender}=render(<PortalWorkspace {...props} preview={null} access={null} agents={agents} request={{kind:'agents'}}/>)
 expect(screen.getByRole('tabpanel',{name:'Subagents'})).toBeVisible()
 expect(screen.queryByText('No files to show yet')).toBeNull()
 expect(screen.queryByRole('dialog')).toBeNull()
 expect(fetch).not.toHaveBeenCalled()
 rerender(<PortalWorkspace {...props} agents={agents} request={{kind:'agents'}}/>)
 expect(screen.getByRole('tab',{name:'Subagents'})).toHaveAttribute('aria-selected','true')
 fireEvent.click(screen.getByRole('tab',{name:'Preview'}))
 expect(screen.getByTitle('Interactive Portal preview')).toBeVisible()
 fireEvent.click(screen.getByRole('tab',{name:'Subagents'}))
 expect(screen.getByRole('tabpanel',{name:'Subagents'})).toBeVisible()
 fireEvent.click(screen.getByRole('button',{name:'Close Subagents'}))
 expect(screen.queryByRole('tab',{name:'Subagents'})).toBeNull()
 expect(screen.getByRole('tab',{name:'Preview'})).toHaveAttribute('aria-selected','true')
 rerender(<PortalWorkspace {...props} agents={agents} request={{kind:'agents'}}/>)
 expect(screen.getByRole('tabpanel',{name:'Subagents'})).toBeVisible()
})
it('retains the web frame and agent detail when moving between workspace tabs',async()=>{
 const controller={...agents,selected:{teamId:agents.teams[0].id,agentId:'0'}}
 const {container,rerender}=render(<PortalWorkspace {...props} agents={controller}/>)
 const frame=container.querySelector('iframe')
 rerender(<PortalWorkspace {...props} agents={controller} request={{kind:'agents'}}/>)
 expect(screen.getByRole('tabpanel',{name:'Subagents'})).toHaveTextContent('Built the page.')
 expect(frame).not.toBeVisible()
 fireEvent.click(screen.getByRole('tab',{name:'Review'}))
 expect(await screen.findByLabelText('Diff for index.html')).toBeVisible()
 fireEvent.click(screen.getByRole('tab',{name:'Subagents'}))
 expect(screen.getByRole('tabpanel',{name:'Subagents'})).toHaveTextContent('Built the page.')
 expect(container.querySelector('iframe')).toBe(frame)
})
it('acknowledges an agent request once so closing and remounting cannot replay it',()=>{
 const request={kind:'agents'},onRequestHandled=vi.fn()
 const view=render(<PortalWorkspace {...props} preview={null} agents={agents} request={request} onRequestHandled={onRequestHandled}/>)
 expect(onRequestHandled).toHaveBeenCalledExactlyOnceWith(request)
 view.rerender(<PortalWorkspace {...props} preview={null} agents={agents} request={request} onRequestHandled={onRequestHandled}/>)
 expect(onRequestHandled).toHaveBeenCalledTimes(1)
 fireEvent.click(screen.getByRole('button',{name:'Close Subagents'}))
 view.unmount()
 render(<PortalWorkspace {...props} preview={null} agents={agents}/>)
 expect(screen.queryByRole('tab',{name:'Subagents'})).toBeNull()
})
it('leaves Subagents to expose retry when an incoming file request cannot load its manifest',async()=>{
 const original=fetch;let failed=true
 globalThis.fetch=vi.fn(url=>url.includes('__ods_manifest__') && failed?Promise.reject(new Error('offline')):original(url))
 const {rerender}=render(<PortalWorkspace {...props} agents={agents} request={{kind:'agents'}}/>)
 await waitFor(()=>expect(fetch).toHaveBeenCalled())
 rerender(<PortalWorkspace {...props} agents={agents} request={{siteId:preview.siteId,kind:'file',path:'src/app.js'}}/>)
 expect(await screen.findByText('Files unavailable.')).toBeVisible()
 expect(screen.getByRole('tab',{name:'Review'})).toHaveAttribute('aria-selected','true')
 failed=false
 fireEvent.click(screen.getByRole('button',{name:'Retry',exact:true}))
 expect(await screen.findByLabelText('Code for src/app.js')).toBeVisible()
})
it('opens the clicked review file, then its source in one closable tab, without reloading the web frame',async()=>{
 const {container,rerender}=render(<PortalWorkspace {...props} request={{siteId:preview.siteId,kind:'review',path:'src/app.js'}}/>)
 expect(await screen.findByLabelText('Diff for src/app.js')).toBeVisible()
 expect(screen.getByRole('button',{name:'Folder demo'})).toBeVisible()
 const frame=container.querySelector('iframe')
 fireEvent.click(screen.getByRole('button',{name:'Open file src/app.js'}))
 expect(await screen.findByLabelText('Code for src/app.js')).toHaveTextContent('const answer = 42;')
 expect(screen.getByRole('navigation',{name:'File path'})).toHaveAttribute('title','demo/src/app.js')
 expect(screen.getAllByRole('tab',{name:'app.js'})).toHaveLength(1)
 fireEvent.click(screen.getByRole('tab',{name:'Review'}))
 fireEvent.click(await screen.findByRole('button',{name:'Open file src/app.js'}))
 expect(screen.getAllByRole('tab',{name:'app.js'})).toHaveLength(1)
 expect(container.querySelector('iframe')).toBe(frame)
 rerender(<PortalWorkspace {...props} collapsed/>)
 expect(container.querySelector('iframe')).toBe(frame)
 rerender(<PortalWorkspace {...props}/>)
 fireEvent.click(screen.getByRole('button',{name:'Close src/app.js'}))
 expect(screen.queryByRole('tab',{name:'app.js'})).toBeNull()
 expect(screen.getByRole('tab',{name:'Review'})).toHaveAttribute('aria-selected','true')
 expect(await screen.findByLabelText('Diff for src/app.js')).toBeVisible()
})
it('opens verified Markdown and its relative source link, with keyboard tab navigation',async()=>{
 render(<PortalWorkspace {...props}/>)
 fireEvent.click(screen.getByRole('button',{name:'Browse files'}))
 fireEvent.click(await screen.findByRole('button',{name:'Open README.md'}))
 expect(await screen.findByRole('heading',{name:'Project'})).toBeVisible()
 fireEvent.click(screen.getByRole('button',{name:'Source',exact:true}))
 expect(await screen.findByLabelText('Code for src/app.js')).toBeVisible()
 fireEvent.keyDown(screen.getByRole('tab',{name:'app.js'}),{key:'Home'})
 expect(screen.getByRole('tab',{name:'Preview'})).toHaveFocus()
 expect(screen.getByRole('tab',{name:'Preview'})).toHaveAttribute('aria-selected','true')
 expect(screen.queryByRole('button',{name:'Expand all changes'})).toBeNull()
})
it('a chat summary opens the actual selected filename instead of an unrelated preview',async()=>{
 const review=vi.fn(),open=vi.fn()
 render(<PixelSnapshotChanges preview={preview} variant="summary" onReview={review} onPreview={open}/>)
 fireEvent.click(await screen.findByRole('button',{name:/src\/app.js/}))
 expect(review).toHaveBeenCalledWith('src/app.js')
 fireEvent.click(screen.getByRole('button',{name:'Review',exact:true}));expect(review).toHaveBeenLastCalledWith(null)
 fireEvent.click(screen.getByRole('button',{name:/Web preview/}));expect(open).toHaveBeenCalledOnce()
 expect(screen.queryByRole('searchbox')).toBeNull()
})
it('waits for the new publication manifest before opening its requested file',async()=>{
 const nextSource='export const ready = true;',nextFile={path:'src/new.js',bytes:nextSource.length,sha256:hash(nextSource)}
 const nextFiles=[files[0],nextFile],next={...preview,siteId:`site-${'b'.repeat(24)}`,sha256:'b'.repeat(64),files:2,bytes:nextFiles.reduce((n,f)=>n+f.bytes,0)}
 const response=text=>({ok:true,headers:new Map(),arrayBuffer:async()=>new TextEncoder().encode(text).buffer})
 const original=fetch;let release
 globalThis.fetch=vi.fn(url=>{
  if(!url.includes(next.siteId))return original(url)
  if(!url.includes('__ods_manifest__'))return Promise.resolve(response(nextSource))
  return new Promise(resolve=>{release=()=>resolve(response(JSON.stringify({...manifest,siteId:next.siteId,sha256:next.sha256,files:nextFiles,bytes:next.bytes})))})
 })
 const {rerender}=render(<PortalWorkspace {...props} request={{siteId:preview.siteId,kind:'file',path:'src/app.js'}}/>)
 expect(await screen.findByLabelText('Code for src/app.js')).toBeVisible()
 rerender(<PortalWorkspace {...props} preview={next} request={{siteId:next.siteId,kind:'file',path:nextFile.path}}/>)
 await waitFor(()=>expect(release).toBeTypeOf('function'))
 expect(screen.queryByText('This file is not available in this publication.')).toBeNull()
 expect(screen.queryByRole('tab',{name:'app.js'})).toBeNull()
 await act(async()=>release())
 expect(await screen.findByLabelText('Code for src/new.js')).toHaveTextContent(nextSource)
})
it('preserves an open-file action while a failed manifest is retried',async()=>{
 const original=fetch;let failManifest=true
 globalThis.fetch=vi.fn(url=>url.includes('__ods_manifest__') && failManifest?Promise.reject(new Error('offline')):original(url))
 render(<PortalWorkspace {...props} request={{siteId:preview.siteId,kind:'review',path:'src/app.js'}}/>)
 fireEvent.click(await screen.findByRole('button',{name:'Open file src/app.js'}))
 expect(await screen.findByText('Files unavailable.')).toBeVisible()
 failManifest=false
 fireEvent.click(screen.getByRole('button',{name:'Retry',exact:true}))
 expect(await screen.findByLabelText('Code for src/app.js')).toHaveTextContent('const answer = 42;')
})
it('does not reopen a pending file after the user has switched tabs',async()=>{
 const original=fetch;let release
 globalThis.fetch=vi.fn(url=>url.includes('__ods_manifest__')?new Promise(resolve=>{release=()=>resolve(original(url))}):original(url))
 render(<PortalWorkspace {...props} request={{siteId:preview.siteId,kind:'file',path:'src/app.js'}}/>)
 await waitFor(()=>expect(release).toBeTypeOf('function'))
 fireEvent.click(screen.getByRole('tab',{name:'Review'}))
 await act(async()=>release())
 expect(screen.getByRole('tab',{name:'Review'})).toHaveAttribute('aria-selected','true')
 expect(screen.queryByRole('tab',{name:'app.js'})).toBeNull()
})
it('offers a retry for an initial file request when the manifest is unavailable',async()=>{
 const original=fetch;let failed=true
 globalThis.fetch=vi.fn(url=>url.includes('__ods_manifest__') && failed?Promise.reject(new Error('offline')):original(url))
 render(<PortalWorkspace {...props} request={{siteId:preview.siteId,kind:'file',path:'src/app.js'}}/>)
 expect(await screen.findByText('Files unavailable.')).toBeVisible()
 failed=false
 fireEvent.click(screen.getByRole('button',{name:'Retry',exact:true}))
 expect(await screen.findByLabelText('Code for src/app.js')).toBeVisible()
})
it('shares the resizable file tree between preview and source and uses a drawer when narrow',async()=>{
 const observers=new Map();let width=900
 const clientWidth=vi.spyOn(HTMLElement.prototype,'clientWidth','get').mockImplementation(()=>width)
 vi.stubGlobal('ResizeObserver',class {
  constructor(callback){this.callback=callback}
  observe(element){observers.set(element,this.callback)}
  disconnect(){}
 })
 try {
  const {container}=render(<PortalWorkspace {...props}/>)
  fireEvent.click(screen.getByRole('button',{name:'Browse files'}))
  const readme=await screen.findByRole('button',{name:'Open README.md'})
  fireEvent.keyDown(screen.getByRole('separator',{name:'Resize file list'}),{key:'ArrowLeft',shiftKey:true})
  const frame=container.querySelector('iframe')
  fireEvent.click(readme)
  expect(await screen.findByRole('heading',{name:'Project'})).toBeVisible()
  expect(screen.getByRole('separator',{name:'Resize file list'})).toHaveAttribute('aria-valuenow','264')
  fireEvent.click(screen.getByRole('tab',{name:'Preview'}))
  expect(screen.getByRole('separator',{name:'Resize file list'})).toHaveAttribute('aria-valuenow','264')
  expect(container.querySelector('iframe')).toBe(frame)
  width=400
  act(()=>observers.get(container.querySelector('.portal-workbench'))([{contentRect:{width}}]))
  expect(screen.queryByRole('separator')).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'Open README.md'}))
  expect(await screen.findByRole('heading',{name:'Project'})).toBeVisible()
  expect(screen.queryByRole('navigation',{name:'Published files'})).toBeNull()
 } finally {clientWidth.mockRestore()}
})

function changedProject(changes) {
 const before={...preview,siteId:`site-${'c'.repeat(24)}`,sha256:'c'.repeat(64)}
 const original=fetch
 globalThis.fetch=vi.fn(url=>url.includes('__ods_changes__') ? Promise.resolve({ok:true,headers:new Map(),arrayBuffer:async()=>new TextEncoder().encode(JSON.stringify({...comparison,beforeSiteId:before.siteId,beforeSha256:before.sha256,changes})).buffer}) : original(url))
 return before
}
const modifiedIndex={...changes[0],change:'modified'}

it('reviews the complete current file tree, marks deletions, and opens unchanged verified source',async()=>{
 const deleted={path:'old.txt',change:'deleted',additions:0,deletions:1,truncated:false,diff:[{type:'remove',oldLine:1,newLine:null,text:'Removed'}]}
 const before=changedProject([modifiedIndex,deleted])
 render(<PortalWorkspace {...props} before={before} request={{siteId:preview.siteId,kind:'review',path:'index.html'}}/>)
 const tree=await screen.findByRole('navigation',{name:'Project files'})
 expect(await screen.findByLabelText('Diff for index.html')).toBeVisible()
 for(const path of ['index.html','src/app.js','README.md','old.txt'])expect(within(tree).getByRole('button',{name:`Open ${path}`})).toBeVisible()
 expect(screen.queryByText(/^2 changed files$/)).toBeNull()
 expect(screen.queryByText('Changes',{exact:true})).toBeNull()
 fireEvent.click(within(tree).getByRole('button',{name:'Open old.txt'}))
 expect(await screen.findByLabelText('Diff for old.txt')).toHaveTextContent('Removed')
 expect(screen.getByText('Deleted',{exact:true})).toBeVisible()
 expect(screen.queryByRole('button',{name:'Open file old.txt'})).toBeNull()
 fireEvent.click(within(tree).getByRole('button',{name:'Open src/app.js'}))
 expect(await screen.findByLabelText('Code for src/app.js')).toHaveTextContent('const answer = 42;')
 expect(fetch.mock.calls.some(([url])=>url.endsWith('/old.txt'))).toBe(false)
})

it('keeps current files openable when comparison fails, without showing unverified changes',async()=>{
 const original=fetch
 globalThis.fetch=vi.fn(url=>url.includes('__ods_changes__') ? Promise.reject(new Error('offline')) : original(url))
 render(<PortalWorkspace {...props} request={{siteId:preview.siteId,kind:'review'}}/>)
 const tree=await screen.findByRole('navigation',{name:'Project files'})
 expect(screen.getByText('File comparison unavailable.')).toBeVisible()
 expect(screen.queryByLabelText(/Diff for/)).toBeNull()
 fireEvent.click(within(tree).getByRole('button',{name:'Open README.md'}))
 expect(await screen.findByRole('heading',{name:'Project'})).toBeVisible()
})

it('opens an unchanged requested file and never substitutes another diff for a missing old path',async()=>{
 const before=changedProject([modifiedIndex])
 const {rerender}=render(<PortalWorkspace {...props} before={before} request={{siteId:preview.siteId,kind:'review',path:'src/app.js'}}/>)
 expect(await screen.findByLabelText('Code for src/app.js')).toHaveTextContent('const answer = 42;')
 rerender(<PortalWorkspace {...props} before={before} request={{siteId:preview.siteId,kind:'review',path:'old-missing.js'}}/>)
 expect(await screen.findByText('This file is not part of the current project. Select another file.')).toBeVisible()
 expect(screen.queryByLabelText('Diff for index.html')).toBeNull()
 fireEvent.click(screen.getByRole('button',{name:'Open index.html'}))
 expect(await screen.findByLabelText('Diff for index.html')).toBeVisible()
})

it('rejects comparison paths outside the current manifest and exposes no publication selector',async()=>{
 const before=changedProject([{...modifiedIndex,path:'ghost.js'}])
 render(<PortalWorkspace {...props} before={before} request={{siteId:preview.siteId,kind:'review'}}/>)
 expect(await screen.findByText('File comparison unavailable.')).toBeVisible()
 const tree=await screen.findByRole('navigation',{name:'Project files'})
 expect(within(tree).queryByRole('button',{name:'Open ghost.js'})).toBeNull()
 expect(screen.queryByLabelText('Diff for ghost.js')).toBeNull()
 fireEvent.click(screen.getByRole('button',{name:'Workspace options'}))
 expect(screen.queryByRole('combobox',{name:'Published version'})).toBeNull()
 expect(screen.getByRole('link',{name:'Open preview in a new tab'})).toHaveAttribute('href',access.url)
 expect(screen.getByRole('button',{name:'Reload preview'})).toBeVisible()
})
