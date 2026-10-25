import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import ODSTalk from './ODSTalk' // eslint-disable-line no-unused-vars

const storageKey = 'ods-talk-conversation-v1'
const identity = { handle: 'a'.repeat(64), tab: 'b'.repeat(64), established: true }
const headers = { 'X-ODS-Talk-Conversation': identity.handle, 'X-ODS-Talk-Tab': identity.tab }
const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body })
const status = { capabilities: { text_chat: true, durable_conversation_requested: true, durable_conversation: true } }
const snapshot = text => ({ messages: [{ role: 'assistant', text }], running: false })
const callsTo = (mock, url) => mock.mock.calls.filter(([target]) => target === url)

beforeEach(() => {
  const data = new Map([[storageKey, JSON.stringify(identity)]])
  vi.stubGlobal('sessionStorage', {
    getItem: key => data.get(key) || null,
    setItem: (key, value) => data.set(key, value),
    removeItem: key => data.delete(key),
  })
  const held = new Set()
  vi.stubGlobal('navigator', { locks: { request: async (key, options, callback) => {
    if (held.has(key)) return callback(null)
    held.add(key)
    try { return await callback({ name: key }) } finally { held.delete(key) }
  } } })
})

afterEach(async () => {
  cleanup()
  await Promise.resolve()
  vi.unstubAllGlobals()
})

test('reload and explicit refresh reuse the retained binding and hydrate without submission', async () => {
  let resumes = 0
  const fetch = vi.fn(async url => {
    if (url === '/api/talk/status') return response(status)
    if (url === '/api/talk/session') return response(snapshot(++resumes === 1 ? 'Retained answer' : 'Refreshed answer'))
    throw new Error(`Unexpected request ${url}`)
  })
  vi.stubGlobal('fetch', fetch)
  render(<ODSTalk />)
  expect(await screen.findByText('Retained answer')).toBeInTheDocument()
  fireEvent.click(screen.getByLabelText('Refresh ODS Talk status'))
  expect(await screen.findByText('Refreshed answer')).toBeInTheDocument()
  expect(screen.queryByText('Retained answer')).not.toBeInTheDocument()
  for (const [, options] of callsTo(fetch, '/api/talk/session')) {
    expect(options.headers).toMatchObject(headers)
    expect(JSON.parse(options.body)).toEqual({ new: false })
  }
  expect(callsTo(fetch, '/api/talk/message/stream')).toHaveLength(0)
})

test('New conversation creates a different binding only after the explicit click', async () => {
  let opens = 0
  const fetch = vi.fn(async url => {
    if (url === '/api/talk/status') return response(status)
    if (url === '/api/talk/session') return response(snapshot(++opens === 1 ? 'Old answer' : 'New empty conversation'))
    throw new Error(`Unexpected request ${url}`)
  })
  vi.stubGlobal('fetch', fetch)
  render(<ODSTalk />)
  await screen.findByText('Old answer')
  expect(opens).toBe(1)
  fireEvent.click(screen.getByRole('button', { name: 'New conversation' }))
  await screen.findByText('New empty conversation')
  const [, options] = callsTo(fetch, '/api/talk/session')[1]
  expect(JSON.parse(options.body)).toEqual({ new: true })
  expect(options.headers['X-ODS-Talk-Conversation']).not.toBe(identity.handle)
  expect(options.headers['X-ODS-Talk-Tab']).not.toBe(identity.tab)
  expect(screen.queryByText('Old answer')).not.toBeInTheDocument()
})

test('unsupported durable runtime stays unavailable without a legacy session or submit', async () => {
  const fetch = vi.fn(async () => response({ capabilities: {
    text_chat: false, durable_conversation_requested: true, durable_conversation: false,
  } }))
  vi.stubGlobal('fetch', fetch)
  render(<ODSTalk />)
  await screen.findAllByText('Durable conversation continuity is unavailable on this Hermes runtime.')
  expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(JSON.parse(sessionStorage.getItem(storageKey))).toEqual(identity)
})

test('a restored pending request can be approved and stopped using the same conversation', async () => {
  let stopped = false
  const fetch = vi.fn(async url => {
    if (url === '/api/talk/status') return response(status)
    if (url === '/api/talk/session') return response(stopped ? snapshot('Stopped history') : {
      ...snapshot('Pending history'), running: true,
      pending_approval: { command: 'synthetic command', description: 'Needs approval' },
    })
    if (url === '/api/talk/approval') return response({ ok: true })
    if (url === '/api/talk/cancel') { stopped = true; return response({ ok: true }) }
    throw new Error(`Unexpected request ${url}`)
  })
  vi.stubGlobal('fetch', fetch)
  render(<ODSTalk />)
  await screen.findByText('synthetic command')
  expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Allow once' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Allow once' })).not.toBeInTheDocument())
  fireEvent.click(screen.getByRole('button', { name: 'Stop request' }))
  await screen.findByText('Stopped history')
  expect(await screen.findByText('Ready')).toBeInTheDocument()
  for (const route of ['approval', 'cancel']) {
    const sent = callsTo(fetch, '/api/talk/' + route)
    expect(sent).toHaveLength(1)
    expect(sent[0][1].headers).toMatchObject(headers)
  }
  expect(callsTo(fetch, '/api/talk/message/stream')).toHaveLength(0)
})

test.each([401, 409])('resume auth/binding refusal %s preserves identity without creating a replacement', async code => {
  const fetch = vi.fn(async url => url === '/api/talk/status' ? response(status) :
    response({ detail: 'Conversation is unavailable for this tab.' }, code))
  vi.stubGlobal('fetch', fetch)
  render(<ODSTalk />)
  await screen.findAllByText(code === 401 ? 'Session expired. Scan the owner card again.' : 'Conversation is unavailable for this tab.')
  expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()
  expect(JSON.parse(sessionStorage.getItem(storageKey))).toEqual(identity)
  expect(JSON.parse(callsTo(fetch, '/api/talk/session')[0][1].body)).toEqual({ new: false })
})

test('a pending history load disables competing refresh/new actions without sending', async () => {
  let finish
  const pending = new Promise(resolve => { finish = resolve })
  const fetch = vi.fn(async url => url === '/api/talk/status' ? response(status) : pending)
  vi.stubGlobal('fetch', fetch)
  render(<ODSTalk />)
  await waitFor(() => expect(callsTo(fetch, '/api/talk/session')).toHaveLength(1))
  expect(screen.getByRole('button', { name: 'New conversation' })).toBeDisabled()
  expect(screen.getByLabelText('Refresh ODS Talk status')).toBeDisabled()
  await act(async () => finish(response(snapshot('Loaded safely'))))
  await screen.findByText('Loaded safely')
  expect(screen.getByRole('button', { name: 'New conversation' })).toBeEnabled()
  expect(callsTo(fetch, '/api/talk/session')).toHaveLength(1)
})

test('a failed stop reports uncertainty and retains the conversation for manual retry', async () => {
  const fetch = vi.fn(async url => {
    if (url === '/api/talk/status') return response(status)
    if (url === '/api/talk/session') return response({ ...snapshot('Pending history'), running: true })
    throw new Error('Synthetic control network failure')
  })
  vi.stubGlobal('fetch', fetch)
  render(<ODSTalk />)
  await screen.findByText('Pending history')
  fireEvent.click(screen.getByRole('button', { name: 'Stop request' }))
  await screen.findAllByText('Synthetic control network failure')
  expect(screen.getByRole('button', { name: 'Stop request' })).toBeEnabled()
  expect(callsTo(fetch, '/api/talk/session')).toHaveLength(1)
  expect(JSON.parse(sessionStorage.getItem(storageKey))).toEqual(identity)
})
