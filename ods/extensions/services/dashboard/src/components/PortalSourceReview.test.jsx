import {act,fireEvent,render,screen,waitFor,within} from '@testing-library/react'
import {createHash} from 'node:crypto'
import PortalWorkspace from './PortalWorkspace'
import PortalSourceReview from './PortalSourceReview'
import {loadSourceReview,validSourceReview} from '../lib/pixelSourceReview'
import {parseVerifiedPreviewFrame} from '../pages/Pixel'

const hash=text=>createHash('sha256').update(text).digest('hex')
const response=text=>({ok:true,headers:new Map(),arrayBuffer:async()=>new TextEncoder().encode(text).buffer})
const built='<h1>Compiled preview</h1>',siteId='site-'+'a'.repeat(24)
const sourceFiles=Object.entries({'package.json':'{"name":"financas"}', 'src/main.jsx':'// Finanças 日本語\r\nexport const App = () => <h1>Original source</h1>;\r\n'})
 .map(([path,text])=>({path,text,bytes:new TextEncoder().encode(text).length,sha256:hash(text)}))
function fixture(files=sourceFiles) {
 const value={schemaVersion:1,scope:'captured-project-source',siteId,relativeDirectory:'Playground/financas',files,bytes:files.reduce((n,f)=>n+f.bytes,0),omitted:{directories:2,files:1,sensitiveFiles:1}}
 const raw=JSON.stringify(value),sha256=hash(raw)
 return {raw,value,preview:{schemaVersion:1,kind:'ods-pixel-workspace-preview',siteId,sha256:'a'.repeat(64),entrySha256:hash(built),relativeDirectory:'Playground/financas/dist',files:1,bytes:built.length,port:9437,url:`http://${siteId}.localhost:9437/${siteId}/`,source:{schemaVersion:1,sourceId:'source-'+sha256.slice(0,24),sha256,relativeDirectory:value.relativeDirectory,files:files.length,bytes:value.bytes,omitted:value.omitted}}}
}
const data=fixture()
function transport(url){
 if(url.includes('__ods_source__'))return Promise.resolve(response(data.raw))
 if(url.includes('__ods_manifest__'))return Promise.resolve(response(JSON.stringify({schemaVersion:1,siteId,sha256:data.preview.sha256,files:[{path:'index.html',bytes:built.length,sha256:hash(built)}],bytes:built.length})))
 if(url.includes('__ods_changes__'))return Promise.resolve(response(JSON.stringify({schemaVersion:1,scope:'published-snapshots',siteId,sha256:data.preview.sha256,beforeSiteId:null,beforeSha256:null,changes:[]})))
 return Promise.resolve(response(built))
}
beforeEach(()=>vi.stubGlobal('fetch',vi.fn(transport)))
afterEach(()=>vi.unstubAllGlobals())

it('keeps complete source separate from compiled output without reloading the preview',async()=>{
 const {container}=render(<PortalWorkspace preview={data.preview} access={{frameUrl:'/preview',url:'/preview',sandbox:'allow-scripts'}} title="Built site" request={{kind:'review',siteId}}/>)
 const frame=await screen.findByTitle('Built site')
 expect(await screen.findByRole('navigation',{name:'Project source files'})).toBeVisible()
 fireEvent.click(screen.getByRole('button',{name:'Open src/main.jsx'}))
 expect(screen.getByLabelText('Source code for src/main.jsx')).toHaveTextContent('Original source')
 expect(screen.getByText(/2 source files captured at publication/)).toBeVisible()
 expect(screen.getByText(/Omitted: 2 directories, 1 files or hidden entries, 1 sensitive files/)).toBeVisible()
 fireEvent.click(screen.getByRole('button',{name:'Published output'}))
 expect(await screen.findByRole('button',{name:'Open index.html'})).toBeVisible()
 fireEvent.click(screen.getByRole('tab',{name:'Preview'}))
 expect(container.querySelector('iframe')).toBe(frame)
 expect(frame).toBeVisible()
})

it('preserves requested output review file even when a source snapshot exists',async()=>{
 render(<PortalWorkspace preview={data.preview} access={{frameUrl:'/preview',url:'/preview',sandbox:'allow-scripts'}} request={{kind:'review',siteId,path:'index.html'}}/>)
 expect(screen.getByRole('button',{name:'Published output'})).toHaveAttribute('aria-pressed','true')
 await waitFor(()=>expect(screen.getByRole('button',{name:'Open index.html'})).toBeVisible())
})

it('keeps old publications usable and explicitly identifies their scope',async()=>{
 const {source,...old}=data.preview
 render(<PortalWorkspace preview={old} access={{frameUrl:'/preview',url:'/preview',sandbox:'allow-scripts'}} request={{kind:'review',siteId}}/>)
 expect(screen.queryByRole('button',{name:'Source files'})).toBeNull()
 expect(screen.queryByText(/No separate project source snapshot was attached/)).toBeNull()
 expect(await screen.findByRole('button',{name:'Open index.html'})).toBeVisible()
 expect(fetch.mock.calls.some(([url])=>url.includes('__ods_source__'))).toBe(false)
})

it('accepts source metadata in completed delivery but rejects unknown project scope',()=>{
 const frame=preview=>({choices:[{finish_reason:'stop'}],pixel:{schemaVersion:1,preview}})
 expect(parseVerifiedPreviewFrame(frame(data.preview))).toEqual(data.preview)
 expect(parseVerifiedPreviewFrame(frame({...data.preview,source:{...data.preview.source,relativeDirectory:'other'}}))).toBeNull()
 expect(validSourceReview({...data.preview.source,extra:true},data.preview.relativeDirectory)).toBe(false)
})

it('rejects corrupt bytes, member hashes, duplicate paths and excessive source before displaying',async()=>{
 const badMembers=[{...sourceFiles[0],sha256:'f'.repeat(64)},sourceFiles[0]]
 for(const candidate of [ {...data,raw:data.raw+' '},fixture(badMembers),fixture([sourceFiles[0],sourceFiles[0]]),fixture([{...sourceFiles[0],bytes:256*1024+1}]) ]) {
  fetch.mockResolvedValue(response(candidate.raw))
  await expect(loadSourceReview(candidate.preview)).rejects.toThrow()
 }
})

it('cancels stale capture responses when a different source revision opens',async()=>{
 let release
 fetch.mockImplementationOnce(()=>new Promise(resolve=>{release=()=>resolve(response(data.raw))}))
 const next=fixture([{path:'src/new.js',text:'new revision',bytes:12,sha256:hash('new revision')}])
 fetch.mockResolvedValue(response(next.raw))
 const {rerender}=render(<PortalSourceReview preview={data.preview}/>)
 await waitFor(()=>expect(release).toBeTypeOf('function'))
 rerender(<PortalSourceReview preview={next.preview}/>)
 expect(await screen.findByLabelText('Source code for src/new.js')).toHaveTextContent('new revision')
 await act(async()=>release())
 expect(screen.queryByLabelText('Source code for package.json')).toBeNull()
 expect(screen.getByLabelText('Source code for src/new.js')).toBeVisible()
})


it('keeps completed source edit excerpts accessible after a built publication',async()=>{
 const date='2026-09-15T10:00:00.000Z'
 const task={schemaVersion:3,runId:'chatcmpl_11111111-2222-4333-8444-555555555555',startedAt:date,finishedAt:date,state:'completed',calls:1,failures:0,blocked:0,truncated:false,activities:[{kind:'unknown',calls:1,failures:0,blocked:0}],events:[{sequence:1,kind:'unknown',state:'completed',startedAt:date,finishedAt:date,display:{type:'tool',label:'Editing a file',detail:'main.jsx',sources:[],steps:[],change:{file:'main.jsx',kind:'write',before:'',after:'export const source = true',truncated:false}}}],context:null,goal:null}
 render(<PortalWorkspace preview={data.preview} access={{frameUrl:'/preview',url:'/preview',sandbox:'allow-scripts'}} task={task} request={{kind:'review',siteId}}/>)
 fireEvent.click(screen.getByRole('button',{name:'File edits'}))
 expect(await screen.findByRole('navigation',{name:'Completed file edits'})).toBeVisible()
 expect(within(screen.getByRole('tabpanel',{name:'Review'})).getByLabelText('Changes to main.jsx')).toHaveTextContent('export const source = true')
 expect(screen.queryByText('Changes recorded by tools in this response.')).toBeNull()
 expect(screen.queryByText('No completed file edits were recorded for this response.')).toBeNull()
})

it('keeps published files visible without offering an empty edits tab',async()=>{
 const {source,...old}=data.preview
 render(<PortalWorkspace preview={old} access={{frameUrl:'/preview',url:'/preview',sandbox:'allow-scripts'}} request={{kind:'review',siteId}}/>)
 expect(await screen.findByRole('button',{name:'Open index.html'})).toBeVisible()
 expect(screen.queryByRole('button',{name:'File edits'})).toBeNull()
 expect(screen.queryByText(/No separate project source snapshot/)).toBeNull()
 expect(screen.queryByText(/No completed file edits/)).toBeNull()
 expect(screen.getByRole('button',{name:'Published output'})).toHaveAttribute('aria-pressed','true')
})

it('groups repeated edits under one file and lets the user inspect each revision',async()=>{
 const date='2026-09-15T10:00:00.000Z'
 const task={schemaVersion:3,runId:'chatcmpl_11111111-2222-4333-8444-555555555555',startedAt:date,finishedAt:date,state:'completed',calls:1,failures:0,blocked:0,truncated:false,activities:[{kind:'unknown',calls:1,failures:0,blocked:0}],events:[{sequence:1,kind:'unknown',state:'completed',startedAt:date,finishedAt:date,display:{type:'tool',label:'Editing a file',detail:'main.jsx',sources:[],steps:[],change:{file:'main.jsx',kind:'write',before:'',after:'export const source = true',truncated:false}}}],context:null,goal:null}
 const original=task.events[0]
 task.events.push({...original,sequence:2,display:{...original.display,change:{...original.display.change,after:'export const second = true'}}})
 task.calls=2;task.activities[0].calls=2
 render(<PortalWorkspace preview={data.preview} access={{frameUrl:'/preview',url:'/preview',sandbox:'allow-scripts'}} task={task} request={{kind:'review',siteId}}/>)
 fireEvent.click(screen.getByRole('button',{name:'File edits'}))
 const tree=await screen.findByRole('navigation',{name:'Completed file edits'})
 const review=within(screen.getByRole('tabpanel',{name:'Review'}))
 expect(within(tree).getAllByRole('button',{name:'Open main.jsx'})).toHaveLength(1)
 expect(review.getByLabelText('Changes to main.jsx')).toHaveTextContent('second = true')
 fireEvent.change(review.getByLabelText('Edit revision'),{target:{value:task.runId+'/1'}})
 expect(review.getByLabelText('Changes to main.jsx')).toHaveTextContent('source = true')
 expect(review.queryByText(/Changes reported by this tool/)).toBeNull()
})
