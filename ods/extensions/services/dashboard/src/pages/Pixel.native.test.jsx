import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {fireEvent, render, screen, waitFor} from '../test/test-utils'
import Pixel from './Pixel'
import {readConversations} from '../lib/pixelConversations'

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('fetch', vi.fn(async url => {
    if (url === '/api/pixel/status') return {ok:true,json:async()=>({available:true,
      runtime:{source:'external-host',model:'Native test model',contextLength:4096}})}
    if (url === '/api/pixel/chat/context') return {ok:true,json:async()=>({schemaVersion:1,status:'missing',context:null,
      compaction:{status:'idle'},history:{acknowledgedMessages:0,revision:null,status:'ready'}})}
    if (url === '/api/pixel/chat/stream') return {ok:true,status:200,body:new globalThis.ReadableStream({start(controller) {
      controller.enqueue(new TextEncoder().encode('data: {"choices":[{"delta":{"content":"Native answer"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'))
      controller.close()
    }})}
    throw new Error(`Unsupported native request: ${url}`)
  }))
})
afterEach(() => {vi.unstubAllGlobals();vi.restoreAllMocks()})

const contextGuidance='This message is too large for the local AI context. Shorten the message or start a new chat. Your saved conversation was kept.'
function response(body,status=200) {return {ok:status>=200 && status<300,status,json:async()=>body}}
function eventResponse(events) {
  return {ok:true,status:200,body:new globalThis.ReadableStream({start(controller) {
    controller.enqueue(new TextEncoder().encode(events));controller.close()
  }})}
}
function contextEvents(code='native_context_limit') {
  return 'data: '+JSON.stringify({error:{type:'native_context_error',code,message:'private-provider-secret [unsafe link](https://invalid.example)'}})+'\n\ndata: [DONE]\n\n'
}

it('uses the existing Portal send/history path and exposes only native text capabilities', async () => {
  const view=render(<Pixel nativeChat />)
  await screen.findByText('Available')
  expect(screen.getByText('Windows native text chat')).toBeInTheDocument()
  expect(screen.getByText(/Context uses the recent messages that fit; full conversation history stays saved/)).toBeInTheDocument()
  expect(screen.queryByText('Permissions')).not.toBeInTheDocument()
  expect(screen.queryByRole('button',{name:'Workspace'})).not.toBeInTheDocument()
  expect(screen.queryByTitle('Dictate')).not.toBeInTheDocument()
  const input=screen.getByPlaceholderText('Message Portal...')
  fireEvent.change(input,{target:{value:'Keep this conversation'}})
  fireEvent.keyDown(input,{key:'Enter'})
  await screen.findByText('Native answer')
  await waitFor(()=>expect(readConversations()[0]?.messages.at(-1)?.status).toBe('done'))
  const request=fetch.mock.calls.find(([url])=>url==='/api/pixel/chat/stream')
  const body=JSON.parse(request[1].body)
  expect(body.messages).toEqual([{role:'user',content:'Keep this conversation'}])
  expect(body.history_snapshot).toEqual({schemaVersion:1,messages:body.messages})
  expect(fetch.mock.calls.every(([url])=>['/api/pixel/status','/api/pixel/chat/context','/api/pixel/chat/stream'].includes(url))).toBe(true)
  view.unmount()
  render(<Pixel nativeChat />)
  await screen.findByText('Native answer')
  expect(screen.getByText('Keep this conversation')).toBeInTheDocument()
})

it.each(['/agents change my computer','/agentes change my computer','/goal change my computer','/compact','/extensions','/extension'])('refuses unsupported command %s without clearing the draft or starting a task', async command => {
  render(<Pixel nativeChat />)
  await screen.findByText('Available')
  const input=screen.getByPlaceholderText('Message Portal...')
  fireEvent.change(input,{target:{value:command}})
  fireEvent.keyDown(input,{key:'Enter'})
  await screen.findByText('Agent commands are not available in this native text-chat preview. Your draft is preserved.')
  expect(input).toHaveValue(command)
  expect(fetch.mock.calls.some(([url])=>url==='/api/pixel/chat/stream' || url.includes('/agents/'))).toBe(false)
})

it('preserves an exact rejected draft and clears its request identity when native saved-result storage is full', async () => {
  const base=fetch.getMockImplementation()
  let full=true
  fetch.mockImplementation(async (url,options)=>url==='/api/pixel/chat/stream' && full
    ? response({detail:{code:'native_storage_full',message:'private-provider-secret'}},507) : base(url,options))
  const view=render(<Pixel nativeChat />)
  await screen.findByText('Available')
  const draft='  Keep this exact draft  '
  const input=screen.getByPlaceholderText('Message Portal...')
  fireEvent.change(input,{target:{value:draft}})
  fireEvent.keyDown(input,{key:'Enter'})
  await screen.findByText(/Saved-result storage is full\. Export conversations/)
  expect(input).toHaveValue(draft)
  await waitFor(()=>expect(readConversations()[0]).toMatchObject({requestId:null,inFlight:false,interrupted:false,draft}))
  expect(screen.queryByText(/Completion was not confirmed|activity is unknown|Restart local AI to recover/)).not.toBeInTheDocument()
  expect(screen.queryByText(/private-provider-secret/)).not.toBeInTheDocument()
  expect(fetch.mock.calls.some(([url])=>['/api/pixel/chat/result','/api/pixel/chat/activity'].includes(url))).toBe(false)
  const rejected=JSON.parse(fetch.mock.calls.find(([url])=>url==='/api/pixel/chat/stream')[1].body)
  view.unmount()
  render(<Pixel nativeChat />)
  expect(await screen.findByText(/Saved-result storage is full\. Export conversations/)).toBeVisible()
  expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue(draft)
  expect(fetch.mock.calls.filter(([url])=>url==='/api/pixel/chat/stream')).toHaveLength(1)
  full=false
  await screen.findByText('Available')
  fireEvent.keyDown(screen.getByPlaceholderText('Message Portal...'),{key:'Enter'})
  await screen.findByText('Native answer')
  const accepted=JSON.parse(fetch.mock.calls.filter(([url])=>url==='/api/pixel/chat/stream')[1][1].body)
  expect(accepted.request_id).not.toBe(rejected.request_id)
  expect(accepted.messages.at(-1)).toEqual({role:'user',content:draft.trim()})
})

it.each([false,true])('shows native context guidance for live and recovered results without replaying the request (restored=%s)', async restored => {
  const events=contextEvents()
  const base=fetch.getMockImplementation()
  if(restored) localStorage.setItem('ods.pixel.chat.v1',JSON.stringify({schema:1,chatId:'native-context-chat',requestId:'native-context-request',inFlight:true,
    messages:[{role:'user',content:'Keep my earlier question'},{role:'assistant',content:'Saved earlier answer'},
      {role:'user',content:'Large current prompt'},{role:'assistant',content:''}]}))
  fetch.mockImplementation(async (url,options)=>{
    if(url==='/api/pixel/chat/stream')return eventResponse(events)
    if(url==='/api/pixel/chat/result')return response({state:'interrupted',events})
    return base(url,options)
  })
  render(<Pixel nativeChat />)
  await screen.findByText('Available')
  if(!restored) {
    fireEvent.change(screen.getByPlaceholderText('Message Portal...'),{target:{value:'Large current prompt'}})
    fireEvent.keyDown(screen.getByPlaceholderText('Message Portal...'),{key:'Enter'})
  }
  await screen.findByText(contextGuidance)
  await waitFor(()=>expect(readConversations()[0]).toMatchObject({requestId:null,interrupted:false,inFlight:false}))
  expect(screen.queryByText(/private-provider-secret/)).not.toBeInTheDocument()
  expect(screen.queryByRole('link',{name:'unsafe link'})).not.toBeInTheDocument()
  expect(readConversations()[0].messages.at(-1)).toMatchObject({status:'error',content:contextGuidance})
  if(restored) {
    expect(screen.getByText('Keep my earlier question')).toBeVisible()
    expect(screen.getByText('Saved earlier answer')).toBeVisible()
  }
  expect(fetch.mock.calls.filter(([url])=>url==='/api/pixel/chat/stream')).toHaveLength(restored?0:1)
})

it.each([{nativeChat:false,code:'native_context_limit'},{nativeChat:true,code:'unknown_context_error'}])('does not expose arbitrary error text or apply native context behavior outside its contract (%j)', async ({nativeChat,code}) => {
  const base=fetch.getMockImplementation()
  fetch.mockImplementation(async (url,options)=>{
    if(url==='/api/pixel/chat/stream')return eventResponse(contextEvents(code))
    if(url==='/api/pixel/chat/result')return response({state:'interrupted',events:contextEvents(code)})
    return base(url,options)
  })
  render(<Pixel nativeChat={nativeChat} />)
  await screen.findByText('Available')
  fireEvent.change(screen.getByPlaceholderText('Message Portal...'),{target:{value:'Keep my question'}})
  fireEvent.keyDown(screen.getByPlaceholderText('Message Portal...'),{key:'Enter'})
  await screen.findByText(/Portal could not complete the response/)
  expect(screen.queryByText(contextGuidance)).not.toBeInTheDocument()
  expect(screen.queryByText(/private-provider-secret/)).not.toBeInTheDocument()
  if(!nativeChat)expect(screen.queryByText(/Context uses the recent messages that fit/)).not.toBeInTheDocument()
})

it.each([{nativeChat:false,code:'native_storage_full'},{nativeChat:true,code:'unknown_storage_error'}])('keeps unknown and non-native HTTP 507 failures uncertain (%j)', async ({nativeChat,code}) => {
  const base=fetch.getMockImplementation()
  fetch.mockImplementation(async (url,options)=>{
    if(url==='/api/pixel/chat/stream')return response({detail:{code,message:'private-provider-secret'}},507)
    if(url==='/api/pixel/chat/result')return response({},404)
    if(url==='/api/pixel/chat/activity')return response({state:'unknown'})
    return base(url,options)
  })
  render(<Pixel nativeChat={nativeChat} />)
  await screen.findByText('Available')
  fireEvent.change(screen.getByPlaceholderText('Message Portal...'),{target:{value:'Keep this request'}})
  fireEvent.keyDown(screen.getByPlaceholderText('Message Portal...'),{key:'Enter'})
  await screen.findByText('Activity unknown')
  await waitFor(()=>expect(readConversations()[0]).toMatchObject({interrupted:true,inFlight:false}))
  expect(readConversations()[0].requestId).toEqual(expect.any(String))
  expect(screen.queryByText(/Saved-result storage is full/)).not.toBeInTheDocument()
  expect(screen.queryByText(/private-provider-secret/)).not.toBeInTheDocument()
})
