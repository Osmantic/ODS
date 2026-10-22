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

it('uses the existing Portal send/history path and exposes only native text capabilities', async () => {
  const view=render(<Pixel nativeChat />)
  await screen.findByText('Available')
  expect(screen.getByText('Windows native text chat')).toBeInTheDocument()
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

it('refuses unsupported agent commands without clearing the draft or starting a task', async () => {
  render(<Pixel nativeChat />)
  await screen.findByText('Available')
  const input=screen.getByPlaceholderText('Message Portal...')
  fireEvent.change(input,{target:{value:'/agents change my computer'}})
  fireEvent.keyDown(input,{key:'Enter'})
  await screen.findByText('Agent commands are not available in this native text-chat preview. Your draft is preserved.')
  expect(input).toHaveValue('/agents change my computer')
  expect(fetch.mock.calls.some(([url])=>url==='/api/pixel/chat/stream' || url.includes('/agents/'))).toBe(false)
})
