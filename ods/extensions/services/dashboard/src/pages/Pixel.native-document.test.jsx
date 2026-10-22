import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {act} from '@testing-library/react'
import {createElement} from 'react'
import {createHash} from 'node:crypto'
import {Buffer} from 'node:buffer'
import {fireEvent, render, screen, waitFor} from '../test/test-utils'
import Pixel from './Pixel'
import {readConversations, saveConversation, SELECT_EVENT} from '../lib/pixelConversations'

const bytes=new TextEncoder().encode('# A useful document\n\nGenerated local content.\n')
const name='document.md'
const nameLength=Buffer.alloc(4);nameLength.writeUInt32BE(Buffer.byteLength(name))
const fileLength=Buffer.alloc(8);fileLength.writeBigUInt64BE(BigInt(bytes.length))
const digest=createHash('sha256').update(nameLength).update(name).update(fileLength).update(bytes).digest('hex')
const artifact={schemaVersion:1,kind:'ods-pixel-workspace-artifact',relativePath:name,
  siteId:`site-${digest.slice(0,24)}`,sha256:digest,file:{path:name,bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')}}
const documentFrame={choices:[{delta:{content:'Your document is ready.'},finish_reason:'stop'}],pixel_artifacts:{schemaVersion:1,artifacts:[artifact]}}
const incomplete='The document could not be completed. No file was published. Your saved conversation is preserved. Try a shorter document request.'
const storageFull='The document could not be saved because storage is full. No file was published. Export conversations you want to keep, delete an older conversation, then try again. Your earlier history is kept.'
const renderPixel=(props={})=>render(createElement(Pixel,props))
const response=(body,status=200)=>({ok:status>=200 && status<300,status,json:async()=>body})
const events=frame=>'data: '+JSON.stringify(frame)+'\n\ndata: [DONE]\n\n'
const stream=frame=>({ok:true,status:200,body:new globalThis.ReadableStream({start(controller) {
  controller.enqueue(new TextEncoder().encode(events(frame)));controller.close()
}})})
const fault=(code,type='native_document_error')=>({error:{type,code,message:'private-model-error [unsafe link](https://invalid.example)'}})
const requests=()=>fetch.mock.calls.filter(([url])=>url==='/api/pixel/chat/stream').map(([,options])=>JSON.parse(options.body))
let reply, retained, downloadedBytes

beforeEach(()=>{
  localStorage.clear()
  reply=async()=>stream(documentFrame)
  retained={state:'complete',events:events(documentFrame)}
  downloadedBytes=bytes
  vi.stubGlobal('fetch',vi.fn(async (url,options)=>{
    if(url==='/api/pixel/status')return response({available:true,runtime:{source:'external-host',model:'Native test model',contextLength:4096}})
    if(url==='/api/pixel/chat/context')return response({schemaVersion:1,status:'missing',context:null,compaction:{status:'idle'},history:{acknowledgedMessages:0,revision:null,status:'ready'}})
    if(url==='/api/pixel/chat/stream')return reply(options)
    if(url==='/api/pixel/chat/result')return response(retained)
    if(url===`/pixel-preview/${artifact.siteId}/${name}`)return {ok:true,status:200,
      headers:{get:()=>String(downloadedBytes.length)},arrayBuffer:async()=>downloadedBytes.buffer}
    throw new Error(`Unexpected native document request: ${url}`)
  }))
  vi.spyOn(URL,'createObjectURL').mockReturnValue('blob:native-document')
  vi.spyOn(URL,'revokeObjectURL').mockImplementation(()=>{})
  vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{})
})
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks()})

async function send(text='Create a Markdown guide') {
  const input=screen.getByPlaceholderText('Message Portal...')
  fireEvent.change(input,{target:{value:text}})
  fireEvent.keyDown(input,{key:'Enter'})
}
function savedAttempt(code) {
  localStorage.setItem('ods.pixel.chat.v1',JSON.stringify({schema:1,chatId:'document-chat',requestId:'document-request',inFlight:true,
    messages:[{role:'user',content:'Earlier question'},{role:'assistant',content:'Earlier answer'},
      {role:'user',content:'Create a Markdown guide'},{role:'assistant',content:''}]}))
  retained=code?{state:'interrupted',events:events(fault(code))}:{state:'complete',events:events(documentFrame)}
}

it('sends one explicit native opt-in and reuses the verified download card and byte hash checks',async()=>{
  const view=renderPixel({nativeChat:true})
  await screen.findByText('Available')
  expect(screen.getByRole('radio',{name:'Chat'})).toBeChecked()
  expect(screen.getByText('Choose Create document to request one Markdown (.md) file for download.')).toBeVisible()
  fireEvent.click(screen.getByRole('radio',{name:'Create document'}))
  await send()
  const download=await screen.findByRole('button',{name:'Download document.md'})
  expect(requests()).toHaveLength(1)
  expect(requests()[0]).toMatchObject({create_document:true,messages:[{role:'user',content:'Create a Markdown guide'}]})
  expect(Object.keys(requests()[0]).sort()).toEqual(['chat_id','create_document','history_snapshot','messages','request_id'])
  await waitFor(()=>expect(readConversations()[0]?.messages.at(-1)?.artifacts).toEqual([artifact]))
  expect(readConversations()[0]).not.toHaveProperty('documentMode')
  expect(readConversations()[0]).not.toHaveProperty('create_document')
  expect(fetch.mock.calls.some(([url])=>url.startsWith('/pixel-preview/'))).toBe(false)
  fireEvent.click(download)
  await screen.findByText('Verified download started')
  expect(fetch).toHaveBeenCalledWith(`/pixel-preview/${artifact.siteId}/${name}`,expect.objectContaining({cache:'no-store'}))
  expect(URL.createObjectURL).toHaveBeenCalledTimes(1)
  expect(HTMLAnchorElement.prototype.click.mock.instances[0].download).toBe(name)
  view.unmount();renderPixel({nativeChat:true})
  expect(await screen.findByRole('button',{name:'Download document.md'})).toBeEnabled()
  expect(screen.getByRole('radio',{name:'Chat'})).toBeChecked()
  expect(requests()).toHaveLength(1)
})

it('refuses corrupted download bytes through the existing verification component',async()=>{
  downloadedBytes=new Uint8Array(bytes)
  downloadedBytes[0]^=1
  renderPixel({nativeChat:true});await screen.findByText('Available')
  fireEvent.click(screen.getByRole('radio',{name:'Create document'}));await send()
  fireEvent.click(await screen.findByRole('button',{name:'Download document.md'}))
  await screen.findByText('Download could not be verified. Try again.')
  expect(URL.createObjectURL).not.toHaveBeenCalled()
  expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled()
})

it('disables the mode during a request and returns to Chat on New chat',async()=>{
  let finish
  reply=()=>new Promise(resolve=>{finish=resolve})
  renderPixel({nativeChat:true});await screen.findByText('Available')
  fireEvent.click(screen.getByRole('radio',{name:'Create document'}));await send()
  await waitFor(()=>expect(requests()).toHaveLength(1))
  expect(screen.getByRole('radio',{name:'Chat'})).toBeDisabled()
  expect(screen.getByRole('radio',{name:'Create document'})).toBeDisabled()
  expect(screen.getByRole('radio',{name:'Create document'})).toBeChecked()
  await act(async()=>{finish(stream(documentFrame))})
  await waitFor(()=>expect(screen.getByRole('radio',{name:'Create document'})).toBeEnabled())
  fireEvent.click(screen.getByTitle('Start a new chat'))
  expect(screen.getByRole('radio',{name:'Chat'})).toBeChecked()
})

it('resets document mode when the owner selects another saved conversation',async()=>{
  saveConversation({schema:1,chatId:'older-chat',messages:[{role:'user',content:'Older saved question'}],draft:'Older draft'})
  saveConversation({schema:1,chatId:'current-chat',messages:[{role:'user',content:'Current question'}],draft:''})
  renderPixel({nativeChat:true});await screen.findByText('Available')
  fireEvent.click(screen.getByRole('radio',{name:'Create document'}))
  act(()=>fireEvent(window,new CustomEvent(SELECT_EVENT,{detail:'older-chat'})))
  expect(await screen.findByText('Older saved question')).toBeVisible()
  expect(screen.getByRole('radio',{name:'Chat'})).toBeChecked()
  expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue('Older draft')
})

it('keeps normal Portal controls and requests free of the native document mode',async()=>{
  reply=async()=>stream({choices:[{delta:{content:'Ordinary answer'},finish_reason:'stop'}]})
  renderPixel();await screen.findByText('Available')
  expect(screen.queryByRole('radio',{name:'Create document'})).not.toBeInTheDocument()
  expect(screen.queryByText(/Markdown \(\.md\) file for download/)).not.toBeInTheDocument()
  await send('Create a Markdown guide')
  await screen.findByText('Ordinary answer')
  expect(requests()).toHaveLength(1)
  expect(Object.keys(requests()[0]).sort()).toEqual(['chat_id','history_snapshot','messages','request_id'])
})

it('preserves document mode and the exact draft after a known admission HTTP 507',async()=>{
  reply=async()=>response({detail:{code:'native_storage_full',message:'private-model-error'}},507)
  renderPixel({nativeChat:true});await screen.findByText('Available')
  fireEvent.click(screen.getByRole('radio',{name:'Create document'}))
  const draft='  Create this exact document  '
  await send(draft)
  await screen.findByText(/Saved-result storage is full\. Export/)
  expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue(draft)
  expect(screen.getByRole('radio',{name:'Create document'})).toBeChecked()
  await waitFor(()=>expect(readConversations()[0]).toMatchObject({requestId:null,interrupted:false,inFlight:false,draft}))
  expect(fetch.mock.calls.some(([url])=>url==='/api/pixel/chat/result')).toBe(false)
  reply=async()=>stream(documentFrame)
  await send(draft)
  await screen.findByRole('button',{name:'Download document.md'})
  expect(requests()).toHaveLength(2)
  expect(requests().every(request=>request.create_document===true)).toBe(true)
  expect(requests()[1].request_id).not.toBe(requests()[0].request_id)
})

it('recovers a completed document by request ID without replaying creation or restoring document mode',async()=>{
  savedAttempt()
  renderPixel({nativeChat:true})
  expect(await screen.findByRole('button',{name:'Download document.md'})).toBeEnabled()
  await waitFor(()=>expect(readConversations()[0]).toMatchObject({requestId:null,interrupted:false,inFlight:false}))
  expect(screen.getByRole('radio',{name:'Chat'})).toBeChecked()
  expect(screen.getByText('Earlier question')).toBeVisible()
  expect(screen.getByText('Earlier answer')).toBeVisible()
  expect(requests()).toHaveLength(0)
  expect(fetch).toHaveBeenCalledWith('/api/pixel/chat/result',expect.objectContaining({body:JSON.stringify({chat_id:'document-chat',request_id:'document-request'})}))
})

it.each(['document_incomplete','document_storage_full'].flatMap(code=>[false,true].map(restored=>({code,restored}))))('shows fixed document failure guidance without a delivery card or automatic replay (%j)',async({code,restored})=>{
  if(restored)savedAttempt(code)
  else {reply=async()=>stream(fault(code));retained={state:'interrupted',events:events(fault(code))}}
  renderPixel({nativeChat:true});await screen.findByText('Available')
  if(!restored){fireEvent.click(screen.getByRole('radio',{name:'Create document'}));await send()}
  await screen.findByText(code==='document_incomplete'?incomplete:storageFull)
  await waitFor(()=>expect(readConversations()[0]).toMatchObject({requestId:null,interrupted:false,inFlight:false}))
  expect(screen.queryByRole('button',{name:'Download document.md'})).not.toBeInTheDocument()
  expect(screen.queryByText(/private-model-error/)).not.toBeInTheDocument()
  expect(screen.queryByRole('link',{name:'unsafe link'})).not.toBeInTheDocument()
  expect(requests()).toHaveLength(restored?0:1)
})

it.each([{nativeChat:true,type:'native_document_error',code:'unknown_document_error'},
  {nativeChat:true,type:'provider_error',code:'document_incomplete'},
  {nativeChat:false,type:'native_document_error',code:'document_incomplete'}])('does not turn unknown or non-native errors into publication claims (%j)',async({nativeChat,type,code})=>{
  const frame=fault(code,type)
  reply=async()=>stream(frame);retained={state:'interrupted',events:events(frame)}
  renderPixel({nativeChat});await screen.findByText('Available')
  if(nativeChat)fireEvent.click(screen.getByRole('radio',{name:'Create document'}))
  await send()
  await screen.findByText(/Portal could not complete the response/)
  expect(screen.queryByText(/No file was published/)).not.toBeInTheDocument()
  expect(screen.queryByText(/private-model-error/)).not.toBeInTheDocument()
  expect(screen.queryByRole('button',{name:'Download document.md'})).not.toBeInTheDocument()
})
