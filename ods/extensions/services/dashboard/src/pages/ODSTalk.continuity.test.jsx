import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import ODSTalk from './ODSTalk' // eslint-disable-line no-unused-vars
import { TALK_HISTORY_KEY } from '../hooks/useTalkConversation'

const expiry = () => Math.floor(Date.now() / 1000) + 3600
const json = (body, status = 200) => ({ ok: status < 400, status, json: async () => body })
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
const sse = frames => ({ ok: true, status: 200, body: new globalThis.ReadableStream({ start(controller) {
  frames.forEach(frame => controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(frame)}\n\n`)))
  controller.close()
} }) })
const saved = (overrides = {}) => ({
  version: 1, id: 'previous', expiresAt: expiry(),
  messages: [{ id: 'user-1', role: 'user', text: 'Remember Juniper', status: 'done' },
    { id: 'reply-1', role: 'assistant', text: 'I remember Juniper.', status: 'done' }], ...overrides,
})
const seed = value => localStorage.setItem(TALK_HISTORY_KEY, JSON.stringify(value))
const input = () => screen.getByPlaceholderText('Message ODS')
const submit = text => {
  fireEvent.change(input(), { target: { value: text } })
  fireEvent.submit(input().closest('form'))
}

function setup(handler) {
  const fetcher = vi.fn(async (url, options = {}) => {
    if (url === '/api/talk/status') return json({ capabilities: { text_chat: true } })
    return handler(url, options)
  })
  vi.stubGlobal('fetch', fetcher)
  return fetcher
}

beforeEach(() => { localStorage.clear(); Object.defineProperty(window, 'isSecureContext', { configurable: true, value: false }) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('hides cached history until its cookie binding is validated, then sends with that ID', async () => {
  const history = saved()
  seed(history)
  const check = deferred()
  const fetcher = setup((url, options) => {
    if (url === '/api/talk/session/previous') return check.promise
    expect(url).toBe('/api/talk/message/stream')
    expect(options.headers['X-ODS-Talk-Conversation']).toBe('previous')
    return sse([{ type: 'complete', conversation_id: 'previous', text: 'Juniper again', status: 'ok' }, { type: 'done' }])
  })
  render(<ODSTalk />)
  await screen.findByText('Ready')
  expect(screen.queryByText('Remember Juniper')).toBeNull()
  submit('A premature message')
  expect(fetcher.mock.calls.some(([url]) => url === '/api/talk/message/stream')).toBe(false)
  await act(async () => check.resolve(json({ conversation_id: history.id, expires_at: history.expiresAt, state: 'ready' })))
  expect(await screen.findByText('Remember Juniper')).toBeVisible()
  submit('What was the name?')
  expect(await screen.findByText('Juniper again')).toBeVisible()
  expect(fetcher.mock.calls.some(([url]) => url === '/api/talk/session')).toBe(false)
})

it.each([401, 410])('does not disclose or replace history after binding status %s', async status => {
  const history = saved()
  seed(history)
  const fetcher = setup(() => json({ detail: 'not available' }, status))
  render(<ODSTalk />)
  expect(await screen.findByTestId('talk-conversation-notice')).toHaveTextContent('no longer available')
  expect(screen.queryByText('Remember Juniper')).toBeNull()
  submit('Do it again')
  expect(fetcher.mock.calls.some(([url]) => url === '/api/talk/session' || url.endsWith('/message/stream'))).toBe(false)
  expect(JSON.parse(localStorage.getItem(TALK_HISTORY_KEY))).toEqual(history)
})

it('does not adopt a different conversation returned by a binding check', async () => {
  seed(saved())
  setup(() => json({ conversation_id: 'someone-else', expires_at: expiry(), state: 'ready' }))
  render(<ODSTalk />)
  expect(await screen.findByTestId('talk-conversation-notice')).toHaveTextContent('no longer available')
  expect(screen.queryByText('Remember Juniper')).toBeNull()
})

it('allocates once on a double submit and persists the ID and transcript together', async () => {
  const allocation = deferred()
  const fetcher = setup((url, options) => {
    if (url === '/api/talk/session') return allocation.promise
    expect(options.headers['X-ODS-Talk-Conversation']).toBe('fresh')
    return sse([{ type: 'complete', conversation_id: 'fresh', text: 'Hello there', status: 'ok' }])
  })
  render(<ODSTalk />)
  await screen.findByText('Ready')
  submit('Hello')
  submit('Hello twice')
  expect(fetcher.mock.calls.filter(([url]) => url === '/api/talk/session')).toHaveLength(1)
  await act(async () => allocation.resolve(json({ conversation_id: 'fresh', expires_at: expiry() })))
  expect(await screen.findByText('Hello there')).toBeVisible()
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/message/stream'))).toHaveLength(1)
  const snapshot = JSON.parse(localStorage.getItem(TALK_HISTORY_KEY))
  expect(snapshot.id).toBe('fresh')
  expect(snapshot.messages.map(m => m.text)).toContain('Hello there')
})

it('restores an interrupted response without restoring approvals or replaying its prompt', async () => {
  const history = saved({ messages: [{ id: 'reply', role: 'assistant', text: 'Partial answer', status: 'pending',
    approval: { command: 'dangerous command' }, imageUrl: 'blob:private', attachmentForRetry: { file: {} } }] })
  seed(history)
  const fetcher = setup(() => json({ conversation_id: history.id, expires_at: history.expiresAt, state: 'uncertain' }))
  render(<ODSTalk />)
  expect(await screen.findByText('Partial answer')).toBeVisible()
  expect(screen.queryByText('dangerous command')).toBeNull()
  expect(screen.getByRole('button', { name: 'Retry last message' })).toBeDisabled()
  submit('Retry')
  expect(fetcher.mock.calls.some(([url]) => url.endsWith('/message/stream') || url.endsWith('/approval'))).toBe(false)
  const restored = JSON.parse(localStorage.getItem(TALK_HISTORY_KEY))
  expect(restored.messages[0]).not.toHaveProperty('approval')
  expect(restored.messages[0]).not.toHaveProperty('imageUrl')
  expect(restored.messages[0]).not.toHaveProperty('attachmentForRetry')
})

it('keeps the old conversation until explicit new allocation succeeds', async () => {
  const history = saved()
  seed(history)
  const allocation = deferred()
  setup(url => url === '/api/talk/session' ? allocation.promise
    : json({ conversation_id: history.id, expires_at: history.expiresAt, state: 'ready' }))
  render(<ODSTalk />)
  await screen.findByText('Remember Juniper')
  fireEvent.click(screen.getByRole('button', { name: 'New conversation' }))
  expect(screen.getByText('Remember Juniper')).toBeVisible()
  await act(async () => allocation.resolve(json({ conversation_id: 'next', expires_at: expiry() })))
  await waitFor(() => expect(screen.queryByText('Remember Juniper')).toBeNull())
  expect(JSON.parse(localStorage.getItem(TALK_HISTORY_KEY)).id).toBe('next')
})

it('keeps an existing conversation if new allocation fails', async () => {
  const history = saved()
  seed(history)
  setup(url => url === '/api/talk/session' ? json({ detail: { message: 'Capacity reached' } }, 429)
    : json({ conversation_id: history.id, expires_at: history.expiresAt, state: 'ready' }))
  render(<ODSTalk />)
  await screen.findByText('Remember Juniper')
  fireEvent.click(screen.getByRole('button', { name: 'New conversation' }))
  expect(await screen.findByTestId('talk-conversation-notice')).toHaveTextContent('Capacity reached')
  expect(screen.getByText('Remember Juniper')).toBeVisible()
  expect(JSON.parse(localStorage.getItem(TALK_HISTORY_KEY)).id).toBe('previous')
})

it('blocks retry after a stream identifies another conversation', async () => {
  setup(url => url === '/api/talk/session' ? json({ conversation_id: 'fresh', expires_at: expiry() })
    : sse([{ type: 'complete', conversation_id: 'other', text: 'Wrong conversation', status: 'ok' }]))
  render(<ODSTalk />)
  await screen.findByText('Ready')
  submit('Hello')
  expect(await screen.findByRole('alert')).toHaveTextContent('different conversation')
  expect(screen.queryByText('Wrong conversation')).toBeNull()
  expect(screen.getByRole('button', { name: 'Retry last message' })).toBeDisabled()
  expect(JSON.parse(localStorage.getItem(TALK_HISTORY_KEY)).id).toBe('fresh')
})

it('preserves the in-memory reply when browser storage fills up', async () => {
  const originalStorage = localStorage
  vi.stubGlobal('localStorage', {
    getItem: key => originalStorage.getItem(key),
    setItem: () => { throw new Error('quota') },
  })
  setup(url => url === '/api/talk/session' ? json({ conversation_id: 'fresh', expires_at: expiry() })
    : sse([{ type: 'complete', conversation_id: 'fresh', text: 'Still here', status: 'ok' }]))
  render(<ODSTalk />)
  await screen.findByText('Ready')
  submit('Hello')
  expect(await screen.findByText('Still here')).toBeVisible()
  expect(screen.getByTestId('talk-conversation-notice')).toHaveTextContent('could not save')
})

it('does not dispatch a prompt if navigation wins conversation allocation', async () => {
  const allocation = deferred()
  const fetcher = setup(url => {
    expect(url).toBe('/api/talk/session')
    return allocation.promise
  })
  const view = render(<ODSTalk />)
  await screen.findByText('Ready')
  submit('Do not send after navigation')
  view.unmount()
  await act(async () => allocation.resolve(json({ conversation_id: 'fresh', expires_at: expiry() })))
  expect(fetcher.mock.calls.some(([url]) => url.endsWith('/message/stream'))).toBe(false)
  expect(localStorage.getItem(TALK_HISTORY_KEY)).toBeNull()
})
