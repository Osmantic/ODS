import {render} from '../test/test-utils'
import {screen, fireEvent, waitFor} from '@testing-library/react'
import Pixel from './Pixel'
import {saveConversation} from '../lib/pixelConversations'

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('fetch',vi.fn(async url => url === '/api/pixel/chat/stream'
    ? {ok:true,headers:new Map([['content-type','text/event-stream']]),body:{getReader:() => ({read:async()=>({done:true}),releaseLock(){}})}}
    : {ok:true,json:async()=>({available:true,model:'pixel/default'})}))
})
afterEach(() => {vi.unstubAllGlobals();vi.restoreAllMocks()})

it('reports the exact bounded payload, including Unicode bytes and shortened replies', async () => {
  const messages = Array.from({length:60},(_,i)=>({role:i%2?'assistant':'user',content:i === 59 ? 'Long '.repeat(4000) : `Message ${i}`}))
  saveConversation({schema:1,chatId:'context',messages,draft:' Việt '})
  render(<Pixel/>);await screen.findByText('Available')
  fireEvent.click(screen.getByRole('button',{name:'Request context',hidden:true}))
  expect(screen.getByText('48 earlier messages included; 12 omitted; 1 shortened.')).toBeInTheDocument()
  const byteText=screen.getByText(/UTF-8 text bytes including/).textContent
  fireEvent.keyDown(screen.getByPlaceholderText('Message Portal...'),{key:'Enter'})
  await waitFor(()=>expect(fetch.mock.calls.some(([url])=>url === '/api/pixel/chat/stream')).toBe(true))
  const body=JSON.parse(fetch.mock.calls.find(([url])=>url === '/api/pixel/chat/stream')[1].body)
  expect(body.messages).toHaveLength(49)
  const bytes=body.messages.reduce((sum,message)=>sum+new TextEncoder().encode(message.content).byteLength,0)
  expect(byteText).toBe(`${bytes.toLocaleString()} UTF-8 text bytes including the trimmed draft.`)
  expect(body.messages.at(-1).content).toBe('Việt')
})

it('honors a retained clean-context boundary and updates when the draft changes', async () => {
  saveConversation({schema:1,chatId:'clean',contextStart:2,messages:[{role:'user',content:'Old'},{role:'assistant',content:'Old reply'},{role:'user',content:'New'}]})
  render(<Pixel/>);await screen.findByText('Available')
  fireEvent.click(screen.getByRole('button',{name:'Request context',hidden:true}))
  expect(screen.getByText('1 earlier messages included; 2 omitted; 0 shortened.')).toBeInTheDocument()
  expect(screen.getByText('Enter a draft to preview a sendable request.')).toBeInTheDocument()
  fireEvent.change(screen.getByPlaceholderText('Message Portal...'),{target:{value:'😊'}})
  expect(screen.getByText('7 UTF-8 text bytes including the trimmed draft.')).toBeInTheDocument()
})
