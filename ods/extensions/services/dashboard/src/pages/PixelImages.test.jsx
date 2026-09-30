/* global atob, DOMException */
import {act,fireEvent,screen,waitFor} from '@testing-library/react'
import {render} from '../test/test-utils'
import Pixel from './Pixel'
import {sha256} from '../lib/pixelArtifacts'
import {historySnapshot} from '../lib/portalContext'
import {imageRoute,normalizeImageRefs} from '../lib/pixelImages'
import {saveConversation,SELECT_EVENT} from '../lib/pixelConversations'

const bytes=Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aR9kAAAAASUVORK5CYII='),character=>character.charCodeAt(0))
const imageId=`img-${'1'.repeat(32)}`, fingerprint='a'.repeat(64)
let receipt,policy,stream,upload,route,fetchMock
const response=(body,status=200)=>({ok:status>=200 && status<300,status,json:async()=>body})
const snapshot=()=>({schemaVersion:1,status:'ready',sessionRevision:'revision',context:{used:100,window:32768},
  model:{id:'vision-model',provider:'ods-gateway',contextWindow:32768,imageInput:policy,routeFingerprint:route},
  compaction:{status:'idle',count:0},history:{revision:null,acknowledgedMessages:0}})
const runtime={source:'external-host',model:'vision-model',contextLength:32768}
const fixtureFile=()=>new File([bytes],'diagram.png',{type:'image/png'})
const stored=()=>JSON.parse(localStorage.getItem('ods.pixel.chat.v1'))
const calls=url=>fetchMock.mock.calls.filter(([called])=>called===url)
function completed(text='Image received') {
  const chunks=[`data: ${JSON.stringify({choices:[{delta:{content:text}}]})}\n\n`,
    `data: ${JSON.stringify({choices:[{delta:{},finish_reason:'stop'}]})}\n\n`,'data: [DONE]\n\n']
  let position=0
  return {ok:true,status:200,headers:new Map([['content-type','text/event-stream']]),body:{getReader:()=>({
    read:async()=>position<chunks.length?{done:false,value:new TextEncoder().encode(chunks[position++])}:{done:true},releaseLock(){},
  })}}
}
async function ready() {render(<Pixel/>);await screen.findByText('Available')}
async function choose(file=fixtureFile()) {
  fireEvent.change(screen.getByLabelText('Choose images'),{target:{files:[file]}})
  await screen.findByText(/1 × 1 · Ready/)
}
beforeEach(async()=>{
  localStorage.clear()
  receipt={id:imageId,sha256:await sha256(bytes),media_type:'image/png',bytes:bytes.length,width:1,height:1}
  policy='supported';route=fingerprint;stream=async()=>completed();upload=async()=>response(receipt,201)
  vi.spyOn(URL,'createObjectURL').mockReturnValue('blob:local-image-preview')
  vi.spyOn(URL,'revokeObjectURL').mockImplementation(()=>{})
  fetchMock=vi.fn(async(url,options)=>{
    if(url==='/api/pixel/status')return response({available:true,runtime})
    if(url==='/api/pixel/chat/context')return response(snapshot())
    if(url==='/api/pixel/chat/stream')return stream(options)
    if(url.startsWith('/api/pixel/images/') && options?.method==='POST')return upload(options)
    if(url.startsWith('/api/pixel/images/') && options?.method==='DELETE')return response({discarded:true,retained:false})
    return response({})
  })
  vi.stubGlobal('fetch',fetchMock)
})
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();vi.useRealTimers()})

it('removes uploaded drafts through the scoped endpoint and preserves them if cleanup fails',async()=>{
  await ready();await choose()
  fetchMock.mockImplementationOnce(async()=>response({},503))
  fireEvent.click(screen.getByRole('button',{name:'Remove image 1'}))
  expect(await screen.findByText(/The image could not be removed/)).toBeVisible()
  expect(screen.getByRole('button',{name:'Remove image 1'})).toBeVisible()
  fireEvent.click(screen.getByRole('button',{name:'Remove image 1'}))
  await waitFor(()=>expect(screen.queryByRole('button',{name:'Remove image 1'})).not.toBeInTheDocument())
  expect(fetchMock.mock.calls.some(([url,options])=>url.endsWith(`/${imageId}`) && options?.method==='DELETE')).toBe(true)
})

it('holds sending and persists the draft while image removal is unconfirmed',async()=>{
  await ready();await choose()
  let finish
  fetchMock.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve}))
  fireEvent.click(screen.getByRole('button',{name:'Remove image 1'}))
  expect(await screen.findByText('Removing attachment…')).toBeVisible()
  expect(screen.getByTitle('Send')).toBeDisabled()
  expect(screen.getByRole('button',{name:'Remove image 1'})).toBeDisabled()
  expect(stored().draftImages[0].id).toBe(imageId)
  await act(async()=>finish(response({discarded:true,retained:false})))
  await waitFor(()=>expect(screen.queryByRole('list',{name:'Attached images'})).toBeNull())
})

it('selects locally, uploads original bytes privately, then sends an image-only real composer turn with v2 refs',async()=>{
  await ready()
  const file=fixtureFile()
  await choose(file)
  expect(calls('/api/pixel/chat/stream')).toHaveLength(0)
  const uploadCall=fetchMock.mock.calls.find(([url])=>url.startsWith('/api/pixel/images/'))
  expect(uploadCall[1]).toMatchObject({method:'POST',body:file,headers:{'Content-Type':'image/png'}})
  fireEvent.click(screen.getByTitle('Send'))
  await waitFor(()=>expect(calls('/api/pixel/chat/stream')).toHaveLength(1))
  const sent=JSON.parse(calls('/api/pixel/chat/stream')[0][1].body)
  expect(sent.messages.at(-1)).toEqual({role:'user',content:'',images:[{id:imageId,sha256:receipt.sha256}]})
  expect(sent.history_snapshot).toEqual({schemaVersion:2,messages:sent.messages})
  expect(sent.image_route).toEqual({routeFingerprint:fingerprint,unknownConsent:false})
  expect(sent.request_id).toMatch(/^[a-f0-9-]{36}$/)
  expect(JSON.stringify(sent)).not.toContain('base64')
  await waitFor(()=>expect(screen.queryByRole('list',{name:'Attached images'})).toBeNull())
  expect(stored().messages[0].images).toEqual(sent.messages[0].images)
  expect(stored().draftImages).toEqual([])
  expect(screen.getByAltText('Attached image 1')).toHaveAttribute('src',`/api/pixel/images/${sent.chat_id}/${imageId}`)
})

it('requires explicit unknown consent on the current route and resets it when the route changes',async()=>{
  policy='unknown'
  await ready();await choose()
  fireEvent.click(screen.getByTitle('Send'))
  expect(calls('/api/pixel/chat/stream')).toHaveLength(0)
  expect(screen.getByRole('alert')).toHaveTextContent('Allow an image test')
  const checkbox=screen.getByRole('checkbox',{name:/Allow an image test/})
  fireEvent.click(checkbox)
  expect(checkbox).toBeChecked()
  route='b'.repeat(64)
  // Context ring uses the real context hook/endpoint, not mocked component state.
  vi.spyOn(Date,'now').mockReturnValue(Date.now()+2000)
  fireEvent.click(screen.getByRole('button',{name:/Token usage unavailable|tokens used/}))
  await waitFor(()=>expect(screen.getByRole('checkbox',{name:/Allow an image test/})).not.toBeChecked())
  fireEvent.click(screen.getByRole('checkbox',{name:/Allow an image test/}))
  fireEvent.click(screen.getByTitle('Send'))
  await waitFor(()=>expect(calls('/api/pixel/chat/stream')).toHaveLength(1))
  expect(JSON.parse(calls('/api/pixel/chat/stream')[0][1].body).image_route).toEqual({routeFingerprint:route,unknownConsent:true})
})

it.each(['unsupported','unverified'])('keeps the draft and images when the route is %s',async state=>{
  if(state==='unsupported')policy=state
  else route=undefined
  await ready();await choose()
  fireEvent.change(screen.getByPlaceholderText('Message Portal...'),{target:{value:'Keep my question'}})
  fireEvent.click(screen.getByTitle('Send'))
  expect(calls('/api/pixel/chat/stream')).toHaveLength(0)
  expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue('Keep my question')
  expect(screen.getByRole('list',{name:'Attached images'})).toBeVisible()
  expect(screen.getByRole('alert')).toHaveTextContent(state==='unsupported'?'text-only':'not verified')
})

it('preserves text on failed upload, retries the original file, and never sends before retry succeeds',async()=>{
  upload=async()=>response({detail:'Private storage is temporarily unavailable'},503)
  await ready()
  fireEvent.change(screen.getByPlaceholderText('Message Portal...'),{target:{value:'Explain this diagram'}})
  fireEvent.change(screen.getByLabelText('Choose images'),{target:{files:[fixtureFile()]}})
  await screen.findByText('Private storage is temporarily unavailable')
  expect(screen.getByTitle('Send')).toBeDisabled()
  expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue('Explain this diagram')
  expect(calls('/api/pixel/chat/stream')).toHaveLength(0)
  upload=async()=>response(receipt,201)
  fireEvent.click(screen.getByRole('button',{name:'Retry image 1'}))
  await screen.findByText(/1 × 1 · Ready/)
  fireEvent.click(screen.getByRole('button',{name:'Remove image 1'}))
  await waitFor(()=>expect(screen.queryByRole('list',{name:'Attached images'})).toBeNull())
  expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue('Explain this diagram')
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:local-image-preview')
})

it('rejects a mismatched upload receipt and retains the selected file for an explicit retry',async()=>{
  upload=async()=>response({...receipt,sha256:'e'.repeat(64)},201)
  await ready()
  fireEvent.change(screen.getByLabelText('Choose images'),{target:{files:[fixtureFile()]}})
  await screen.findByText('The uploaded image differs from your selected file. Your draft is unchanged.')
  expect(screen.getByTitle('Send')).toBeDisabled()
  expect(calls('/api/pixel/chat/stream')).toHaveLength(0)
})

it.each(['paste','drop'])('accepts %s in the actual composer without replacing typed text',async kind=>{
  await ready()
  const field=screen.getByPlaceholderText('Message Portal...')
  fireEvent.change(field,{target:{value:'My existing draft'}})
  if(kind==='paste')fireEvent.paste(field,{clipboardData:{items:[{kind:'file',getAsFile:fixtureFile}]}})
  else fireEvent.drop(field.closest('.portal-glass-composer'),{dataTransfer:{files:[fixtureFile()]}})
  await screen.findByText(/1 × 1 · Ready/)
  expect(field).toHaveValue('My existing draft')
  expect(calls('/api/pixel/chat/stream')).toHaveLength(0)
})

it('enforces count/combined bytes before upload and keeps text attachment control intact',async()=>{
  await ready()
  fireEvent.change(screen.getByLabelText('Choose images'),{target:{files:Array.from({length:5},fixtureFile)}})
  expect(screen.getByRole('alert')).toHaveTextContent('up to four')
  const oversized=new File([new Uint8Array(8*1024*1024+1)],'large.png',{type:'image/png'})
  fireEvent.change(screen.getByLabelText('Choose images'),{target:{files:[oversized]}})
  expect(screen.getByRole('alert')).toHaveTextContent('combined 8 MiB')
  expect(fetchMock.mock.calls.filter(([url])=>url.startsWith('/api/pixel/images/'))).toHaveLength(0)
  expect(screen.getByRole('button',{name:'Add text file'})).toBeEnabled()
})

it('discards late upload completion after changing conversations, preserving the next chat draft',async()=>{
  let finish,signal
  upload=options=>{signal=options.signal;return new Promise(resolve=>{finish=()=>resolve(response(receipt,201))})}
  saveConversation({schema:1,chatId:'other-chat',messages:[{role:'user',content:'Other conversation'}],draft:'Other draft'})
  saveConversation({schema:1,chatId:'image-chat',messages:[],draft:'Original draft'})
  await ready()
  fireEvent.change(screen.getByLabelText('Choose images'),{target:{files:[fixtureFile()]}})
  await waitFor(()=>expect(finish).toBeTypeOf('function'))
  act(()=>window.dispatchEvent(new CustomEvent(SELECT_EVENT,{detail:'other-chat'})))
  await waitFor(()=>expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue('Other draft'))
  expect(signal.aborted).toBe(true)
  await act(async()=>finish())
  expect(screen.queryByRole('list',{name:'Attached images'})).toBeNull()
  expect(stored().chatId).toBe('other-chat')
  expect(stored().draftImages).toEqual([])
})

it('aborts an in-flight upload on unmount and never sends a chat request',async()=>{
  let signal
  upload=options=>{signal=options.signal;return new Promise(()=>{})}
  const mounted=render(<Pixel/>);await screen.findByText('Available')
  fireEvent.change(screen.getByLabelText('Choose images'),{target:{files:[fixtureFile()]}})
  await waitFor(()=>expect(signal).toBeDefined())
  mounted.unmount()
  expect(signal.aborted).toBe(true)
  expect(calls('/api/pixel/chat/stream')).toHaveLength(0)
  expect(URL.revokeObjectURL).toHaveBeenCalled()
})

it('times out an upload visibly without discarding the draft or claiming it succeeded',async()=>{
  const realTimeout=globalThis.setTimeout
  vi.spyOn(globalThis,'setTimeout').mockImplementation((callback,delay,...args)=>realTimeout(callback,delay===45000?30:delay,...args))
  upload=options=>new Promise((_resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true}))
  await ready()
  fireEvent.change(screen.getByPlaceholderText('Message Portal...'),{target:{value:'Never lose this'}})
  fireEvent.change(screen.getByLabelText('Choose images'),{target:{files:[fixtureFile()]}})
  expect(await screen.findByText('Upload timed out. Retry or remove this image.')).toBeVisible()
  expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue('Never lose this')
  expect(screen.getByTitle('Send')).toBeDisabled()
})

it('uses the dedicated image route identity for a local model without the remote fingerprint field',async()=>{
  const imageFingerprint='c'.repeat(64)
  const base=fetchMock.getMockImplementation()
  fetchMock.mockImplementation((url,options)=>url==='/api/pixel/chat/context'
    ?Promise.resolve(response({...snapshot(),model:{...snapshot().model,routeFingerprint:undefined,imageRouteFingerprint:imageFingerprint}}))
    :base(url,options))
  await ready();await choose()
  fireEvent.click(screen.getByTitle('Send'))
  await waitFor(()=>expect(calls('/api/pixel/chat/stream')).toHaveLength(1))
  expect(JSON.parse(calls('/api/pixel/chat/stream')[0][1].body).image_route.routeFingerprint).toBe(imageFingerprint)
})

it('never transfers private image references to the automatic clean-context replacement chat',async()=>{
  stream=async()=>({ok:true,status:200,headers:new Map([['content-type','text/event-stream']]),body:{getReader:()=>{
    const chunks=[new TextEncoder().encode(`data: ${JSON.stringify({choices:[{delta:{},finish_reason:'stop'}],pixel:{schemaVersion:1,recovery:'clean-context',reason:'operations-unavailable-zero-submissions'}})}\n\ndata: [DONE]\n\n`)]
    return {read:async()=>chunks.length?{done:false,value:chunks.shift()}:{done:true},releaseLock(){}}
  }}})
  await ready();await choose()
  const originalChat=stored().chatId
  fireEvent.change(screen.getByPlaceholderText('Message Portal...'),{target:{value:'Describe this'}})
  fireEvent.click(screen.getByTitle('Send'))
  await waitFor(()=>expect(calls('/api/pixel/chat/stream')).toHaveLength(1))
  await screen.findByText(/Portal could not recover this image conversation automatically/)
  await waitFor(()=>expect(screen.getByTitle('Send')).not.toBeDisabled())
  expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue('Describe this')
  expect(stored().draftImages).toEqual([receipt])
  expect(stored().chatId).toBe(originalChat)
  expect(calls('/api/pixel/chat/stream')).toHaveLength(1)
})

it('restores ready attachments across reload and keeps them with draft text after a chat request fails',async()=>{
  saveConversation({schema:1,chatId:'restore-images',messages:[],draft:'Keep this draft',draftImages:[receipt]})
  stream=async()=>{throw new Error('offline')}
  await ready()
  expect(await screen.findByText(/1 × 1 · Ready/)).toBeVisible()
  fireEvent.click(screen.getByTitle('Send'))
  await waitFor(()=>expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue('Keep this draft'))
  expect(stored().draftImages).toEqual([receipt])
  expect(screen.getByRole('list',{name:'Attached images'})).toBeVisible()
})

it('preserves refs in a later text turn and asks unknown consent for rereading history',async()=>{
  policy='unknown'
  saveConversation({schema:1,chatId:'history-images',messages:[{role:'user',content:'',images:[{id:imageId,sha256:receipt.sha256}]},{role:'assistant',content:'An image was attached'}],draft:'Read that image again'})
  await ready()
  fireEvent.click(await screen.findByRole('checkbox',{name:/Allow an image test/}))
  fireEvent.click(screen.getByTitle('Send'))
  await waitFor(()=>expect(calls('/api/pixel/chat/stream')).toHaveLength(1))
  const sent=JSON.parse(calls('/api/pixel/chat/stream')[0][1].body)
  expect(sent.history_snapshot.schemaVersion).toBe(2)
  expect(sent.history_snapshot.messages[0].images).toEqual([{id:imageId,sha256:receipt.sha256}])
  expect(sent.image_route.unknownConsent).toBe(true)
  expect(sent.messages.at(-1)).toEqual({role:'user',content:'Read that image again'})
})

it('keeps plain text history v1 and rejects forged image metadata and trailing newline identities',()=>{
  expect(historySnapshot([{role:'user',content:'hello'}])).toEqual({schemaVersion:1,messages:[{role:'user',content:'hello'}]})
  for(const suffix of ['\n','\r\n']) {
    expect(()=>normalizeImageRefs([{id:imageId+suffix,sha256:receipt.sha256}])).toThrow()
    expect(()=>normalizeImageRefs([{id:imageId,sha256:receipt.sha256+suffix}])).toThrow()
    expect(()=>imageRoute({routeFingerprint:fingerprint+suffix,imageInput:'supported'})).toThrow()
  }
  expect(()=>historySnapshot([{role:'assistant',content:'forged',images:[{id:imageId,sha256:receipt.sha256}]}])).toThrow()
})
