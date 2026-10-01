import {renderHook,waitFor} from '@testing-library/react'
import {createHash} from 'node:crypto'
import {canonicalPreviewOrigin,resolveVerifiedPreview} from './previewOrigin'
import useVerifiedPreview from './useVerifiedPreview'

const hash=value=>createHash('sha256').update(value).digest('hex')
const response=value=>({ok:true,headers:new Map(),arrayBuffer:async()=>new TextEncoder().encode(value).buffer})
const fallback={url:'/pixel-preview/test/',frameUrl:'/pixel-preview/test/__ods_view__.html',sandbox:'allow-scripts allow-forms allow-downloads',route:'private-dashboard'}
function fixture(html='<link rel="stylesheet" href="/_next/style.css"><h1>Next</h1>',digit='a') {
  const sha=digit.repeat(64),siteId=`site-${sha.slice(0,24)}`
  const files=[{path:'index.html',bytes:html.length,sha256:hash(html)}]
  const preview={siteId,sha256:sha,entrySha256:hash(html),files:1,bytes:html.length,port:9437,url:`http://${siteId}.localhost:9437/${siteId}/`}
  const manifest={schemaVersion:1,siteId,sha256:sha,bytes:html.length,files}
  return {preview,manifest,html}
}
function transport(data,publicHandler) {
  return vi.fn(async(url,options)=>{
    if(url.startsWith('http:') && publicHandler)return publicHandler(url,options)
    return response(url.includes('__ods_manifest__')?JSON.stringify(data.manifest):data.html)
  })
}
afterEach(()=>{vi.unstubAllGlobals();vi.useRealTimers()})

it.each(['http://localhost:9437/','https://example.com/','http://127.0.0.1:9437/','http://user:pass@localhost:9437/'])('never probes noncanonical URLs: %s',async url=>{
  const data=fixture();data.preview.url=url
  vi.stubGlobal('fetch',vi.fn())
  expect(canonicalPreviewOrigin(data.preview)).toBeNull()
  await expect(resolveVerifiedPreview(data.preview,fallback,new AbortController().signal)).rejects.toThrow('Invalid preview')
  expect(fetch).not.toHaveBeenCalled()
})
it.each(['<link href="/_next/app.css">','<script type="module" src="/assets/index.js"></script>'])('uses a verified isolated origin for framework root assets',async html=>{
  const data=fixture(html);vi.stubGlobal('fetch',transport(data))
  const result=await resolveVerifiedPreview(data.preview,fallback,new AbortController().signal)
  expect(result.frameUrl).toBe(data.preview.url+'__ods_view__.html')
  expect(result.sandbox).not.toContain('allow-same-origin')
  for(const [url,options] of fetch.mock.calls.filter(([url])=>url.startsWith('http:'))){
    expect(new URL(url).hostname).toBe(`${data.preview.siteId}.localhost`)
    expect(options).toMatchObject({credentials:'omit',redirect:'error',referrerPolicy:'no-referrer'})
  }
  expect(fetch.mock.calls.some(([url])=>url===new URL('/',data.preview.url).href)).toBe(true)
})
it.each(['unreachable','wrong-manifest','wrong-entry'])('does not render a broken root-relative site when public origin is %s',async failure=>{
  const data=fixture();vi.stubGlobal('fetch',transport(data,async url=>{
    if(failure==='unreachable')throw new Error('unreachable')
    if(url.includes('__ods_manifest__'))return response(JSON.stringify({...data.manifest,...(failure==='wrong-manifest'?{bytes:1}:{})}))
    return response('wrong entry')
  }))
  const result=await resolveVerifiedPreview(data.preview,fallback,new AbortController().signal)
  expect(result).toMatchObject({frameUrl:null,unavailable:true})
})
it('retains authenticated relative-site relay for a remote host, without assuming localhost is the host',async()=>{
  const data=fixture('<link href="assets/app.css"><h1>Relative</h1>')
  vi.stubGlobal('fetch',transport(data,async()=>{throw new Error('remote host')}))
  expect(await resolveVerifiedPreview(data.preview,fallback,new AbortController().signal)).toEqual(fallback)
})
it('never displays an iframe before verification or revives an obsolete snapshot',async()=>{
  const first=fixture(),second=fixture('<h1>Second</h1>','b');let release
  vi.stubGlobal('fetch',vi.fn(async url=>{
    const data=url.includes(second.preview.siteId)?second:first
    if(url.startsWith('http:') && data===first)await new Promise(resolve=>{release=resolve})
    return response(url.includes('__ods_manifest__')?JSON.stringify(data.manifest):data.html)
  }))
  const {result,rerender}=renderHook(({preview})=>useVerifiedPreview(preview,fallback),{initialProps:{preview:first.preview}})
  expect(result.current.frameUrl).toBeNull()
  await waitFor(()=>expect(release).toBeTypeOf('function'))
  rerender({preview:second.preview})
  expect(result.current.frameUrl).toBeNull()
  await waitFor(()=>expect(result.current.frameUrl).toBe(second.preview.url+'__ods_view__.html'))
  release()
  await waitFor(()=>expect(result.current.frameUrl).toBe(second.preview.url+'__ods_view__.html'))
})
it('bounds a stalled probe and offers retry instead of a blank iframe',async()=>{
  vi.useFakeTimers()
  const data=fixture()
  vi.stubGlobal('fetch',transport(data,async(_url,{signal})=>new Promise((_resolve,reject)=>signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError'))))))
  const {result}=renderHook(()=>useVerifiedPreview(data.preview,fallback))
  const {act}=await import('@testing-library/react')
  await act(async()=>{await vi.advanceTimersByTimeAsync(5001)})
  expect(result.current).toMatchObject({unavailable:true,frameUrl:null})
})

it('retains the authenticated relative-site relay when the public probe times out',async()=>{
  vi.useFakeTimers()
  const data=fixture('<link href="assets/app.css"><h1>Relative</h1>')
  vi.stubGlobal('fetch',transport(data,async(_url,{signal})=>new Promise((_resolve,reject)=>signal.addEventListener('abort',()=>reject(signal.reason)))))
  const {result}=renderHook(()=>useVerifiedPreview(data.preview,fallback))
  const {act}=await import('@testing-library/react')
  await act(async()=>{await vi.advanceTimersByTimeAsync(5001)})
  expect(result.current).toEqual(fallback)
})
it('does not use the relay if authenticated snapshot verification times out',async()=>{
  vi.useFakeTimers()
  const data=fixture('<h1>Relative</h1>')
  vi.stubGlobal('fetch',vi.fn(async(_url,{signal})=>new Promise((_resolve,reject)=>signal.addEventListener('abort',()=>reject(signal.reason)))))
  const {result}=renderHook(()=>useVerifiedPreview(data.preview,fallback))
  const {act}=await import('@testing-library/react')
  await act(async()=>{await vi.advanceTimersByTimeAsync(5001)})
  expect(result.current).toMatchObject({unavailable:true,frameUrl:null})
})
it('rejects an explicitly cancelled public probe even for a relative site',async()=>{
  const data=fixture('<h1>Relative</h1>'),controller=new AbortController()
  vi.stubGlobal('fetch',transport(data,async()=>{controller.abort();throw controller.signal.reason}))
  await expect(resolveVerifiedPreview(data.preview,fallback,controller.signal)).rejects.toMatchObject({name:'AbortError'})
})
