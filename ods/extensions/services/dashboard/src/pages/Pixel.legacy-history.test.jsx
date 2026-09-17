import { cleanup, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import Pixel from './Pixel'
import { CHAT_KEY, readConversations } from '../lib/pixelConversations'

const LIBRARY_KEY = 'ods.pixel.conversations.v1'
const record = (text, draft = '') => ({
  schema: 1, chatId: 'retained-chat', messages: [{role: 'user', content: text}],
  draft, preview: null, inFlight: false, interrupted: false,
})

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('fetch', vi.fn(async url => ({
    ok: true,
    json: async () => url === '/api/pixel/chat/result'
      ? {state: 'active', events: ''} : {available: true},
  })))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

test.each([
  ['legacy library-first', record('Old pointer text', 'Old draft'), record('Latest saved answer', 'Latest draft')],
  ['current pointer-first', {...record('Latest saved answer', 'Latest draft'), persistenceVersion: 2}, record('Old library text', 'Old draft')],
  ['legacy pointer only', record('Latest saved answer', 'Latest draft'), null],
])('mount and autosave retain the authoritative %s record', async (_name, pointer, library) => {
  localStorage.setItem(CHAT_KEY, JSON.stringify(pointer))
  localStorage.setItem(LIBRARY_KEY, JSON.stringify(library ? [library] : []))
  render(<Pixel />)
  await screen.findByText('Available')
  expect(screen.getByPlaceholderText(/^Message .+\.\.\.$/)).toHaveValue('Latest draft')
  expect(screen.getByText('Latest saved answer')).toBeInTheDocument()
  expect(readConversations()[0]).toMatchObject({
    draft: 'Latest draft', messages: [{role: 'user', content: 'Latest saved answer'}],
  })
  expect(JSON.parse(localStorage.getItem(CHAT_KEY))).toMatchObject({
    persistenceVersion: 2, draft: 'Latest draft',
  })
  expect(fetch.mock.calls.some(([url, options]) => options?.method === 'POST' && url !== '/api/pixel/chat/context')).toBe(false)
})

test('restores the library-first request receipt before checking interrupted work', async () => {
  // Result recovery and service health run independently. Make the receipt
  // arrive first, as it can in CI, then verify the active-task lock after health.
  let resolveHealth
  const health = new Promise(resolve => { resolveHealth = resolve })
  const immediateFetch = fetch.getMockImplementation()
  fetch.mockImplementation((url, ...args) => url === '/api/pixel/status'
    ? health : immediateFetch(url, ...args))
  localStorage.setItem(CHAT_KEY, JSON.stringify(record('Old pointer text')))
  localStorage.setItem(LIBRARY_KEY, JSON.stringify([{
    ...record('Latest saved request'), messages: [
      {role: 'user', content: 'Latest saved request'},
      {role: 'assistant', content: '', status: 'streaming'},
    ], inFlight: true, requestId: 'latest-request',
  }]))
  render(<Pixel />)
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/pixel/chat/result', expect.objectContaining({
    body: JSON.stringify({chat_id: 'retained-chat', request_id: 'latest-request'}),
  })))
  expect(screen.getByText('Latest saved request')).toBeInTheDocument()
  await screen.findByText('Working in this chat')
  expect(screen.getByRole('textbox')).toBeDisabled()
  resolveHealth({ok: true, json: async () => ({available: true})})
  expect(await screen.findByPlaceholderText(/^Message .+\.\.\.$/)).toBeDisabled()
  expect(JSON.parse(localStorage.getItem(CHAT_KEY))).toMatchObject({requestId: 'latest-request', interrupted: true})
  expect(fetch.mock.calls.some(([url]) => url === '/api/pixel/chat/stream')).toBe(false)
})

test('an emptied pointer-first draft cannot be revived from the older library', async () => {
  localStorage.setItem(CHAT_KEY, JSON.stringify({...record(''), messages: [], persistenceVersion: 2}))
  localStorage.setItem(LIBRARY_KEY, JSON.stringify([{...record(''), messages: [], draft: 'Discarded draft'}]))
  render(<Pixel />)
  await screen.findByText('Available')
  expect(screen.getByPlaceholderText(/^Message .+\.\.\.$/)).toHaveValue('')
  expect(readConversations()).toEqual([])
})
