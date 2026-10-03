import {act, fireEvent, screen, waitFor} from '@testing-library/react'
import {render} from '../test/test-utils'
import {CHAT_KEY, readConversations, saveConversation, SELECT_EVENT} from '../lib/pixelConversations'
// eslint-disable-next-line no-unused-vars
import Pixel from './Pixel'

const original = {schema:1, chatId:'shared-chat', messages:[{role:'user',content:'Original turn'}], draft:'Initial draft'}
const stored = () => JSON.parse(localStorage.getItem(CHAT_KEY))

beforeEach(() => {
  localStorage.clear()
  saveConversation(original)
  vi.stubGlobal('fetch', vi.fn(async url => {
    if (url === '/api/pixel/status') return {ok:true,json:async()=>({available:true,model:'pixel/default'})}
    return {ok:false,status:404,json:async()=>({})}
  }))
})
afterEach(() => {vi.unstubAllGlobals(); vi.restoreAllMocks()})

async function openChat() {
  render(<Pixel/>)
  await screen.findByText('Available')
  return screen.getByPlaceholderText(/Message Portal/)
}

it('preserves newer saved text when an older mounted tab edits its draft', async () => {
  const input = await openChat()
  const newer = {...stored(), messages:[...original.messages,{role:'assistant',content:'New response from another tab'}], draft:'Newer saved draft'}
  act(() => saveConversation(newer))
  const before = localStorage.getItem(CHAT_KEY)
  fireEvent.change(input,{target:{value:'Unsent old-tab draft'}})
  expect(localStorage.getItem(CHAT_KEY)).toBe(before)
  expect(readConversations()[0].draft).toBe('Newer saved draft')
  expect(input).toHaveValue('Unsent old-tab draft')
  expect(await screen.findByRole('button',{name:'Download recovery copy'})).toBeEnabled()
})

it('checks the original conversation even after another tab switches the current pointer', async () => {
  const input = await openChat()
  act(() => {
    saveConversation({...stored(),draft:'Newer library draft'})
    saveConversation({schema:1,chatId:'different-chat',messages:[],draft:'Other task'})
  })
  const before = localStorage.getItem(CHAT_KEY)
  fireEvent.change(input,{target:{value:'Stale edit'}})
  expect(localStorage.getItem(CHAT_KEY)).toBe(before)
  expect(readConversations().find(chat=>chat.chatId==='shared-chat').draft).toBe('Newer library draft')
})

it('does not start a backend task when a newer saved revision appeared before Send', async () => {
  const input = await openChat()
  fireEvent.change(input,{target:{value:'Local send draft'}})
  act(() => saveConversation({...stored(),draft:'Other tab owns this revision'}))
  const before = localStorage.getItem(CHAT_KEY)
  fireEvent.click(screen.getByTitle('Send'))
  await waitFor(()=>expect(screen.queryByText('Working')).not.toBeInTheDocument())
  expect(fetch.mock.calls.some(([url])=>url==='/api/pixel/chat/stream')).toBe(false)
  expect(localStorage.getItem(CHAT_KEY)).toBe(before)
  expect(screen.getByText('This conversation changed in another tab. No task was started. Download a recovery copy, then reload to read the saved version.')).toBeVisible()
  expect(screen.queryByText(/Check browser storage and try again/)).not.toBeInTheDocument()
})

it('starts and saves a new task after another active tab moves the shared pointer', async () => {
  const input = await openChat()
  const originalFetch = fetch.getMockImplementation()
  fetch.mockImplementation(async (url, options) => {
    if (url !== '/api/pixel/chat/stream') return originalFetch(url, options)
    const bytes = new TextEncoder().encode('data: {"choices":[{"delta":{"content":"New task answer"}}]}\n\ndata: [DONE]\n\n')
    let read = false
    return {ok:true,status:200,body:{getReader:()=>({read:async()=>read?{done:true}:(read=true,{done:false,value:bytes}),releaseLock(){}})}}
  })
  fireEvent.click(screen.getByRole('button', {name:'New chat'}))
  const newId = stored().chatId
  expect(newId).not.toBe(original.chatId)
  act(() => saveConversation({...original, draft:'Other active tab'}))
  fireEvent.change(input, {target:{value:'Make a forest page'}})
  fireEvent.click(screen.getByTitle('Send'))
  expect(await screen.findByText('New task answer')).toBeVisible()
  const posts = fetch.mock.calls.filter(([url]) => url === '/api/pixel/chat/stream')
  expect(posts).toHaveLength(1)
  expect(JSON.parse(posts[0][1].body).chat_id).toBe(newId)
  expect(readConversations().find(chat => chat.chatId === original.chatId)).toMatchObject({messages:original.messages,draft:'Other active tab'})
  expect(readConversations().find(chat => chat.chatId === newId).messages.at(-1).content).toBe('New task answer')
  expect(screen.queryByRole('button', {name:'Download recovery copy'})).not.toBeInTheDocument()
})

it('can explicitly select a different saved conversation and continue saving', async () => {
  const input = await openChat()
  act(() => saveConversation({schema:1,chatId:'selected-chat',messages:[],draft:'Selected draft'}))
  act(() => window.dispatchEvent(new CustomEvent(SELECT_EVENT,{detail:'selected-chat'})))
  expect(input).toHaveValue('Selected draft')
  fireEvent.change(input,{target:{value:'Intentional new edit'}})
  expect(stored().draft).toBe('Intentional new edit')
})
