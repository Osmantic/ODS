import {act, fireEvent, screen, waitFor} from '@testing-library/react'
import {render} from '../test/test-utils'
import Pixel from './Pixel'
import {CHAT_KEY, saveConversation, SELECT_EVENT} from '../lib/pixelConversations'

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('fetch', vi.fn(async () => ({ok:true,json:async () => ({available:true,model:'pixel/default'})})))
})
afterEach(() => {vi.unstubAllGlobals(); vi.restoreAllMocks()})

it('retains an oversized unsent draft across reload and permits editing it back under the send limit', async () => {
  const text = 'x'.repeat(16384) + '\nDo not lose this ending.'
  const first = render(<Pixel/> )
  await screen.findByText('Available')
  fireEvent.change(screen.getByPlaceholderText('Message Portal...'), {target:{value:text}})
  await waitFor(() => expect(JSON.parse(localStorage.getItem(CHAT_KEY)).draft).toBe(text))
  expect(screen.getByTitle('Send')).toBeDisabled()
  first.unmount()
  render(<Pixel/> )
  await screen.findByText('Available')
  expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue(text)
  expect(JSON.parse(localStorage.getItem(CHAT_KEY)).draft).toBe(text)
  expect(screen.getByTitle('Send')).toBeDisabled()
  fireEvent.change(screen.getByPlaceholderText('Message Portal...'), {target:{value:'Edited draft'}})
  expect(screen.getByTitle('Send')).toBeEnabled()
  expect(fetch.mock.calls.some(([url]) => url === '/api/pixel/chat')).toBe(false)
})

it('retains the complete oversized draft when selecting an existing conversation', async () => {
  const text = 'a'.repeat(16384) + 'END'
  saveConversation({schema:1, chatId:'large', messages:[], draft:text})
  saveConversation({schema:1, chatId:'current', messages:[], draft:'Current'})
  render(<Pixel/> )
  await screen.findByText('Available')
  act(() => window.dispatchEvent(new CustomEvent(SELECT_EVENT, {detail:'large'})))
  expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue(text)
  expect(screen.getByTitle('Send')).toBeDisabled()
})
