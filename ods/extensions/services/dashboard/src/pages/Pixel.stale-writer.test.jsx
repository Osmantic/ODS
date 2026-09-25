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
})

it('can explicitly select a different saved conversation and continue saving', async () => {
  const input = await openChat()
  act(() => saveConversation({schema:1,chatId:'selected-chat',messages:[],draft:'Selected draft'}))
  act(() => window.dispatchEvent(new CustomEvent(SELECT_EVENT,{detail:'selected-chat'})))
  expect(input).toHaveValue('Selected draft')
  fireEvent.change(input,{target:{value:'Intentional new edit'}})
  expect(stored().draft).toBe('Intentional new edit')
})
