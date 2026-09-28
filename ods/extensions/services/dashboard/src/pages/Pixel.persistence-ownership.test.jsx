import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { render } from '../test/test-utils'
import { StrictMode } from 'react'
import { CONTEXT_REQUEST_ID } from '../lib/portalContext'
import Pixel from './Pixel'
import { CHAT_KEY, PERSISTENCE_OWNERSHIP } from '../lib/pixelConversations'
const CONVERSATION_STORAGE_NAME = 'ods.pixel.conversations.v1'

const response = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
})

const sseResponse = (frames, { status = 200 } = {}) => {
  const encoder = new TextEncoder()
  const chunks = frames.map(f => encoder.encode(`data: ${f}\n\n`))
  let idx = 0
  const reader = {
    read: async () => idx >= chunks.length ? { done: true, value: undefined } : { done: false, value: chunks[idx++] },
    releaseLock: () => {},
  }
  return {
    ok: status >= 200 && status < 300,
    status,
    body: { getReader: () => reader },
    headers: new Map([['content-type', 'text/event-stream']]),
  }
}

const deferred = () => {
  let resolve, reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

const statusOk = () => response({ available: true })
const contextOk = () => response({ schemaVersion: 1, status: 'missing', sessionRevision: null, context: null, model: null, compaction: { status: 'idle', count: 0 }, history: { revision: null, acknowledgedMessages: 0 } })
const activityTerminal = () => response({ state: 'terminal' })

function installFetch(handler) {
  const mock = vi.fn((url, options) => {
    if (url === '/api/pixel/status') return Promise.resolve(statusOk())
    if (url === '/api/pixel/chat/context') return Promise.resolve(contextOk())
    if (url === '/api/pixel/chat/activity') return Promise.resolve(activityTerminal())
    return handler(url, options)
  })
  globalThis.fetch = mock
  return mock
}

function seedOwnedChat({ chatId, requestId, messages, draft = '', inFlight = false, interrupted = false }) {
  const record = {
    schema: 1, chatId, requestId, inFlight, interrupted, draft,
    messages, persistenceVersion: 2, persistenceOwnership: PERSISTENCE_OWNERSHIP,
    updatedAt: Date.now(),
  }
  localStorage.setItem(CHAT_KEY, JSON.stringify(record))
  localStorage.setItem(CONVERSATION_STORAGE_NAME, JSON.stringify([record]))
  return record
}

function seedLegacyChat({ chatId, messages, draft = '', inFlight = false, interrupted = false }) {
  const record = {
    schema: 1, chatId, inFlight, interrupted, draft, messages,
    updatedAt: Date.now(),
  }
  localStorage.setItem(CHAT_KEY, JSON.stringify(record))
  localStorage.setItem(CONVERSATION_STORAGE_NAME, JSON.stringify([record]))
  return record
}

function readRaw(key) {
  return localStorage.getItem(key)
}

function parseRaw(key) {
  const value = localStorage.getItem(key)
  return value ? JSON.parse(value) : null
}

describe('Pixel Web Lock ownership integration', () => {
  let locksHandle

  beforeEach(() => {
    localStorage.clear()
    locksHandle = {locks: navigator.locks}
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('journey 1: a passive viewer cannot write or duplicate an active authored request', async () => {
    const chatId = 'owner-chat-1'
    seedOwnedChat({ chatId, requestId: null, messages: [], inFlight: false })
    const finalFrame = deferred()
    const streamCalls = []
    installFetch((url, options) => {
      if (url === '/api/pixel/chat/stream') {
        streamCalls.push(JSON.parse(options.body))
        return finalFrame.promise
      }
      if (url === '/api/pixel/chat/result') return Promise.resolve(response({state:'active',events:''}))
      throw new Error(`Unexpected request ${url}`)
    })
    const owner = render(<Pixel />)
    await within(owner.container).findByText('Available')
    fireEvent.change(within(owner.container).getByPlaceholderText('Message Portal...'), {target:{value:'Build the thing'}})
    fireEvent.click(within(owner.container).getByTitle('Send'))
    await waitFor(() => expect(streamCalls).toHaveLength(1))
    expect(parseRaw(CHAT_KEY)).toMatchObject({chatId,inFlight:true,requestId:streamCalls[0].request_id})
    const beforeChat = readRaw(CHAT_KEY)
    const beforeLibrary = readRaw(CONVERSATION_STORAGE_NAME)
    const follower = render(<Pixel />)
    await within(follower.container).findByText('Working in this chat')
    expect(within(follower.container).getByPlaceholderText('Message Portal...')).toBeDisabled()
    expect(readRaw(CHAT_KEY)).toBe(beforeChat)
    expect(readRaw(CONVERSATION_STORAGE_NAME)).toBe(beforeLibrary)
    expect(streamCalls).toHaveLength(1)
    await act(async () => finalFrame.resolve(sseResponse([
      JSON.stringify({choices:[{delta:{content:'Final answer'}}]}),'[DONE]',
    ])))
    expect(await within(owner.container).findByText('Final answer')).toBeVisible()
    await waitFor(() => expect(parseRaw(CHAT_KEY)).toMatchObject({requestId:null,inFlight:false,messages:[{role:'user',content:'Build the thing'},{role:'assistant',content:'Final answer',status:'done'}]}))
    expect(within(owner.container).queryByText(/changed in another tab/i)).toBeNull()
    expect(streamCalls).toHaveLength(1)
    owner.unmount()
    follower.unmount()
  })

  it('a follower opened before Send shows the saved progress and final without writing', async () => {
    const chatId = 'before-send-viewer'
    seedOwnedChat({ chatId, messages: [] })
    const finalFrame = deferred()
    const streamCalls = []
    installFetch((url, options) => {
      if (url === '/api/pixel/chat/stream') {
        streamCalls.push(JSON.parse(options.body))
        return finalFrame.promise
      }
      if (url === '/api/pixel/chat/result') return Promise.resolve(response({ state: 'active', events: '' }))
      throw new Error(`Unexpected request ${url}`)
    })
    const owner = render(<Pixel />)
    await within(owner.container).findByText('Available')
    fireEvent.change(within(owner.container).getByPlaceholderText('Message Portal...'), { target: { value: 'Build a forest page' } })
    await waitFor(() => expect(parseRaw(CHAT_KEY).draft).toBe('Build a forest page'))
    const follower = render(<Pixel />)
    await within(follower.container).findByText('Open in another tab')
    expect(within(follower.container).getByPlaceholderText('Message Portal...')).toHaveValue('Build a forest page')
    fireEvent.click(within(owner.container).getByTitle('Send'))
    await waitFor(() => expect(streamCalls).toHaveLength(1))
    const activeChat = readRaw(CHAT_KEY)
    const activeLibrary = readRaw(CONVERSATION_STORAGE_NAME)
    await within(follower.container).findByText('Working in this chat', {}, { timeout: 6000 })
    expect(within(follower.container).getByText('Build a forest page')).toBeVisible()
    expect(within(follower.container).getByPlaceholderText('Message Portal...')).toHaveValue('')
    expect(readRaw(CHAT_KEY)).toBe(activeChat)
    expect(readRaw(CONVERSATION_STORAGE_NAME)).toBe(activeLibrary)
    await act(async () => finalFrame.resolve(sseResponse([
      JSON.stringify({ choices: [{ delta: { content: 'Published forest preview' } }] }), '[DONE]',
    ])))
    expect(await within(owner.container).findByText('Published forest preview')).toBeVisible()
    await waitFor(() => expect(parseRaw(CHAT_KEY).inFlight).toBe(false))
    const finalChat = readRaw(CHAT_KEY)
    const finalLibrary = readRaw(CONVERSATION_STORAGE_NAME)
    await within(follower.container).findByText('Published forest preview', {}, { timeout: 6000 })
    expect(within(follower.container).getByText('Open in another tab')).toBeVisible()
    expect(within(follower.container).getByPlaceholderText('Message Portal...')).toHaveValue('')
    expect(readRaw(CHAT_KEY)).toBe(finalChat)
    expect(readRaw(CONVERSATION_STORAGE_NAME)).toBe(finalLibrary)
    expect(streamCalls).toHaveLength(1)
    owner.unmount()
    follower.unmount()
  }, 12000)

  it('a follow-up based on the displayed final reply can send after the owner closes', async () => {
    const chatId = 'displayed-reply-follow-up'
    seedOwnedChat({ chatId, messages: [] })
    const finalFrame = deferred()
    const streamCalls = []
    installFetch((url, options) => {
      if (url === '/api/pixel/chat/stream') {
        streamCalls.push(JSON.parse(options.body))
        return streamCalls.length === 1 ? finalFrame.promise : Promise.resolve(sseResponse([
          JSON.stringify({ choices: [{ delta: { content: 'Follow-up answer' } }] }), '[DONE]',
        ]))
      }
      if (url === '/api/pixel/chat/result') return Promise.resolve(response({ state: 'active', events: '' }))
      throw new Error(`Unexpected request ${url}`)
    })
    const owner = render(<Pixel />)
    await within(owner.container).findByText('Available')
    fireEvent.change(within(owner.container).getByPlaceholderText('Message Portal...'), { target: { value: 'First question' } })
    await waitFor(() => expect(parseRaw(CHAT_KEY).draft).toBe('First question'))
    const follower = render(<Pixel />)
    await within(follower.container).findByText('Open in another tab')
    fireEvent.click(within(owner.container).getByTitle('Send'))
    await waitFor(() => expect(streamCalls).toHaveLength(1))
    await act(async () => finalFrame.resolve(sseResponse([
      JSON.stringify({ choices: [{ delta: { content: 'First answer' } }] }), '[DONE]',
    ])))
    await within(follower.container).findByText('First answer', {}, { timeout: 6000 })
    fireEvent.change(within(follower.container).getByPlaceholderText('Message Portal...'), { target: { value: 'A follow-up to the displayed answer' } })
    owner.unmount()
    await within(follower.container).findByText('Available', {}, { timeout: 6000 })
    fireEvent.click(within(follower.container).getByTitle('Send'))
    expect(await within(follower.container).findByText('Follow-up answer')).toBeVisible()
    expect(streamCalls).toHaveLength(2)
    expect(streamCalls[1].messages.slice(0, 2)).toEqual([
      { role: 'user', content: 'First question' }, { role: 'assistant', content: 'First answer' },
    ])
    expect(within(follower.container).queryByText(/conversation changed in another tab/i)).toBeNull()
    follower.unmount()
  }, 12000)

  it('journey 2: owner unmount releases lease; follower retries and rebases before terminal replay', async () => {
    const chatId = 'owner-chat-2'
    const requestId = 'owner-req-2'
    seedOwnedChat({
      chatId, requestId, inFlight: true,
      messages: [
        { role: 'user', content: 'Long task' },
        { role: 'assistant', content: 'Partial' },
      ],
    })

    let resultState = 'active'
    const streamCalls = []
    installFetch((url, options) => {
      if (url === '/api/pixel/chat/stream') {
        streamCalls.push(JSON.parse(options.body))
        return Promise.resolve(sseResponse([JSON.stringify({ choices: [{ delta: { content: 'unexpected' } }] }), '[DONE]']))
      }
      if (url === '/api/pixel/chat/result') {
        if (resultState === 'active') return Promise.resolve(response({ state: 'active', events: '' }))
        return Promise.resolve(response({
          state: 'complete',
          events: [
            'data: ' + JSON.stringify({ choices: [{ delta: { content: 'Backend final' } }] }),
            'data: [DONE]',
            '',
          ].join('\n'),
        }))
      }
      throw new Error(`Unexpected request ${url}`)
    })

    const owner = render(<Pixel />)
    await within(owner.container).findByText('Working in this chat')
    // Owner is recovering; no new stream.
    expect(streamCalls).toHaveLength(0)

    // Follower mounts while owner holds the lease.
    const follower = render(<Pixel />)
    await within(follower.container).findByText('Working in this chat')

    // Owner unmounts, releasing the lease.
    owner.unmount()

    // Backend completes.
    resultState = 'complete'

    // Follower should retry within ~2s and pick up the terminal result.
    await waitFor(() => {
      expect(within(follower.container).getByText('Backend final')).toBeVisible()
    }, { timeout: 6000 })

    // Follower persisted the done answer without resubmitting.
    await waitFor(() => {
      const saved = parseRaw(CHAT_KEY)
      expect(saved?.messages?.at(-1)?.content).toBe('Backend final')
      expect(saved?.inFlight).toBe(false)
    })
    expect(streamCalls).toHaveLength(0)

    follower.unmount()

    // Reopen a third time: same final answer, no resubmit.
    const third = render(<Pixel />)
    await screen.findByText('Backend final')
    expect(streamCalls).toHaveLength(0)
    third.unmount()
  })

  it('journey 3: legacy raw without ownership marker is preserved and not migrated', async () => {
    const chatId = 'legacy-chat-3'
    const legacy = seedLegacyChat({
      chatId, inFlight: true,
      messages: [
        { role: 'user', content: 'Legacy task' },
        { role: 'assistant', content: 'Legacy partial' },
      ],
    })
    const beforeChat = readRaw(CHAT_KEY)
    const beforeLibrary = readRaw(CONVERSATION_STORAGE_NAME)

    installFetch((url) => {
      if (url === '/api/pixel/chat/result') return Promise.resolve(response({ state: 'active', events: '' }))
      throw new Error(`Unexpected request ${url}`)
    })

    const view = render(<Pixel />)
    await screen.findByText('Available')
    // Give the passive acquire a chance to run.
    await new Promise(r => setTimeout(r, 50))

    // Legacy raw bytes are preserved exactly.
    expect(readRaw(CHAT_KEY)).toBe(beforeChat)
    expect(readRaw(CONVERSATION_STORAGE_NAME)).toBe(beforeLibrary)
    // No lock was claimed for the legacy chat.
    expect(locksHandle.locks._isHeld(`ods:pixel-chat-writer:${chatId}`)).toBe(false)
    // Legacy record still lacks the ownership marker.
    expect(parseRaw(CHAT_KEY).persistenceOwnership).toBeUndefined()
    expect(legacy.persistenceOwnership).toBeUndefined()

    view.unmount()
  })

  it('journey 4: follower stale draft is preserved; owner edit wins; follower send is rejected', async () => {
    const chatId = 'owner-chat-4'
    seedOwnedChat({
      chatId, requestId: null, inFlight: false,
      messages: [
        { role: 'user', content: 'First' },
        { role: 'assistant', content: 'First answer', status: 'done' },
      ],
    })

    const streamCalls = []
    installFetch((url, options) => {
      if (url === '/api/pixel/chat/stream') {
        streamCalls.push(JSON.parse(options.body))
        return Promise.resolve(sseResponse([
          JSON.stringify({ choices: [{ delta: { content: 'Owner new answer' } }] }),
          '[DONE]',
        ]))
      }
      throw new Error(`Unexpected request ${url}`)
    })

    const owner = render(<Pixel />)
    await within(owner.container).findByText('Available')
    // Owner acquires the lease on first author action.
    const ownerTextarea = within(owner.container).getByPlaceholderText('Message Portal...')
    fireEvent.change(ownerTextarea, { target: { value: 'Owner new message' } })
    await waitFor(() => expect(locksHandle.locks._isHeld(`ods:pixel-chat-writer:${chatId}`)).toBe(true))

    // Follower mounts while owner holds the lease.
    const follower = render(<Pixel />)
    await within(follower.container).findByText('Open in another tab')
    const followerTextarea = within(follower.container).getByPlaceholderText('Message Portal...')
    fireEvent.change(followerTextarea, { target: { value: 'Follower stale draft' } })
    // Follower's local text is preserved.
    expect(followerTextarea).toHaveValue('Follower stale draft')

    // Owner sends and durably saves.
    const ownerSend = within(owner.container).getByTitle('Send')
    fireEvent.click(ownerSend)
    await screen.findByText('Owner new answer')
    await waitFor(() => {
      const saved = parseRaw(CHAT_KEY)
      expect(saved?.messages?.at(-1)?.content).toBe('Owner new answer')
    })

    // After the owner closes, the stale editor can claim a lease but cannot overwrite its newer checkpoint.
    owner.unmount()
    await act(async () => {})
    // Follower attempts to send: must be rejected before any remote work.
    const followerSend = within(follower.container).getByTitle('Send')
    fireEvent.click(followerSend)
    await within(follower.container).findByText(/changed in another tab/i)
    expect(streamCalls).toHaveLength(1)
    // Follower's text is still visible.
    expect(followerTextarea).toHaveValue('Follower stale draft')
    // Neither source record nor newer text was overwritten by the follower.
    const saved = parseRaw(CHAT_KEY)
    expect(saved?.messages?.at(-1)?.content).toBe('Owner new answer')

    follower.unmount()
  })

  it('journey 5: owned unsent draft survives unmount/reopen; new chat releases prior lease', async () => {
    const chatId = 'owner-chat-5'
    seedOwnedChat({
      chatId, requestId: null, inFlight: false,
      messages: [{ role: 'user', content: 'Hello' }, { role: 'assistant', content: 'Hi', status: 'done' }],
    })

    const streamCalls = []
    installFetch((url, options) => {
      if (url === '/api/pixel/chat/stream') {
        streamCalls.push(JSON.parse(options.body))
        return Promise.resolve(sseResponse([JSON.stringify({ choices: [{ delta: { content: 'x' } }] }), '[DONE]']))
      }
      throw new Error(`Unexpected request ${url}`)
    })

    const first = render(<Pixel />)
    await screen.findByText('Available')
    const textarea = within(first.container).getByPlaceholderText('Message Portal...')
    fireEvent.change(textarea, { target: { value: 'Unsent draft' } })
    await waitFor(() => expect(locksHandle.locks._isHeld(`ods:pixel-chat-writer:${chatId}`)).toBe(true))
    await waitFor(() => {
      const saved = parseRaw(CHAT_KEY)
      expect(saved?.draft).toBe('Unsent draft')
    })
    first.unmount()

    // Reopen: draft is restored, no remote inference fired.
    const second = render(<Pixel />)
    await within(second.container).findByText('Available', {}, { timeout: 6000 })
    const restored = within(second.container).getByPlaceholderText('Message Portal...')
    await waitFor(() => expect(restored).toHaveValue('Unsent draft'))
    expect(streamCalls).toHaveLength(0)

    // Start a new chat: prior lease is released.
    const newChatButton = within(second.container).getByTitle('Start a new chat')
    fireEvent.click(newChatButton)
    await waitFor(() => expect(locksHandle.locks._isHeld(`ods:pixel-chat-writer:${chatId}`)).toBe(false))
    expect(streamCalls).toHaveLength(0)

    second.unmount()
  })

  it('journey 6: unsupported locks prevents new send; typed text preserved; no POST', async () => {
    const chatId = 'owner-chat-6'
    seedOwnedChat({
      chatId, requestId: null, inFlight: false,
      messages: [{ role: 'user', content: 'Hi' }, { role: 'assistant', content: 'Hello', status: 'done' }],
    })

    // Explicitly unsupported: navigator.locks is null.
    Object.defineProperty(globalThis.navigator, 'locks', { configurable: true, writable: true, value: null })

    const streamCalls = []
    installFetch((url, options) => {
      if (url === '/api/pixel/chat/stream') {
        streamCalls.push(JSON.parse(options.body))
        return Promise.resolve(sseResponse([JSON.stringify({ choices: [{ delta: { content: 'x' } }] }), '[DONE]']))
      }
      throw new Error(`Unexpected request ${url}`)
    })

    const view = render(<Pixel />)
    await within(view.container).findByText('Saving unavailable')
    const textarea = within(view.container).getByPlaceholderText('Message Portal...')
    fireEvent.change(textarea, { target: { value: 'Cannot send' } })
    const send = within(view.container).getByTitle('Send')
    fireEvent.click(send)
    await new Promise(r => setTimeout(r, 50))
    expect(streamCalls).toHaveLength(0)
    expect(textarea).toHaveValue('Cannot send')

    view.unmount()
  })

  it('journey 3b: StrictMode participating record recovers persistently after lease free', async () => {
    const chatId = 'strict-chat-3b'
    seedOwnedChat({
      chatId, requestId: 'strict-req', inFlight: true,
      messages: [
        { role: 'user', content: 'Strict task' },
        { role: 'assistant', content: 'Strict partial' },
      ],
    })

    let resultState = 'active'
    installFetch((url) => {
      if (url === '/api/pixel/chat/result') {
        if (resultState === 'active') return Promise.resolve(response({ state: 'active', events: '' }))
        return Promise.resolve(response({
          state: 'complete',
          events: [
            'data: ' + JSON.stringify({ choices: [{ delta: { content: 'Strict recovered' } }] }),
            'data: [DONE]',
            '',
          ].join('\n'),
        }))
      }
      throw new Error(`Unexpected request ${url}`)
    })

    const view = render(<StrictMode><Pixel /></StrictMode>)
    await within(view.container).findByText('Working in this chat')
    // StrictMode double-mounts; the lease must be held by the surviving mount.
    await waitFor(() => expect(locksHandle.locks._isHeld(`ods:pixel-chat-writer:${chatId}`)).toBe(true), {timeout:6000})

    resultState = 'complete'
    await waitFor(() => {
      expect(within(view.container).getByText('Strict recovered')).toBeVisible()
    }, { timeout: 6000 })

    await waitFor(() => {
      const saved = parseRaw(CHAT_KEY)
      expect(saved?.messages?.at(-1)?.content).toBe('Strict recovered')
      expect(saved?.inFlight).toBe(false)
    })

    view.unmount()
  })

  it('journey 7: pure follower rebase after owner compaction surfaces completed notice and clears pending', async () => {
    const chatId = 'owner-chat-7'
    seedOwnedChat({
      chatId, requestId: null, inFlight: false,
      messages: [
        { role: 'user', content: 'Original question' },
        { role: 'assistant', content: 'Original answer', status: 'done' },
      ],
      draft: 'Follower draft',
    })

    const compactDeferred = deferred()
    const compactCalls = []
    const streamCalls = []
    let contextSnapshot = {
      schemaVersion: 1, status: 'ready', sessionRevision: 'session-1',
      model: { id: 'small-model', provider: 'local', contextWindow: 8192 },
      context: { used: 1200, window: 8192, measuredAt: '2026-09-16T12:00:00.000Z' },
      compaction: { status: 'idle', count: 0 },
      history: { revision: 'a'.repeat(64), acknowledgedMessages: 2 },
    }

    const mock = vi.fn((url, options) => {
      if (url === '/api/pixel/status') return Promise.resolve(statusOk())
      if (url === '/api/pixel/chat/activity') return Promise.resolve(activityTerminal())
      if (url === '/api/pixel/chat/context') return Promise.resolve(response(contextSnapshot))
      if (url === '/api/pixel/chat/compact') {
        const body = JSON.parse(options.body)
        expect(parseRaw(CHAT_KEY).compactionRequestId).toBe(body.request_id)
        compactCalls.push(body)
        return compactDeferred.promise
      }
      if (url === '/api/pixel/chat/stream') {
        streamCalls.push(JSON.parse(options.body))
        return Promise.resolve(sseResponse([JSON.stringify({ choices: [{ delta: { content: 'unexpected' } }] }), '[DONE]']))
      }
      throw new Error(`Unexpected request ${url}`)
    })
    globalThis.fetch = mock

    // Owner claims the existing record; follower mounts before compaction starts.
    const owner = render(<Pixel />)
    await within(owner.container).findByText('Available')
    const follower = render(<Pixel />)
    await within(follower.container).findByText('Open in another tab')
    const followerTextarea = within(follower.container).getByPlaceholderText('Message Portal...')
    await waitFor(() => expect(followerTextarea).toHaveValue('Follower draft'))

    // The author starts a real compaction from its UI.
    fireEvent.click(within(owner.container).getByRole('button', { name: 'Open prompt commands' }))
    fireEvent.click(within(owner.container).getByRole('button', { name: /Compact Free context/ }))

    // Owner must persist its request ID BEFORE the POST is issued.
    await waitFor(() => expect(compactCalls).toHaveLength(1))
    const ownerId = compactCalls[0].request_id
    expect(ownerId).toMatch(CONTEXT_REQUEST_ID)
    expect(parseRaw(CHAT_KEY)).toMatchObject({ chatId, compactionRequestId: ownerId })

    // Owner unmounts while backend is still running; lease is released.
    owner.unmount()

    // Backend completes the compaction with the owner's request ID.
    contextSnapshot = {
      ...contextSnapshot,
      context: { used: 400, window: 8192, measuredAt: '2026-09-16T12:00:01.000Z' },
      compaction: { status: 'completed', count: 1, requestId: ownerId },
    }
    await act(async () => compactDeferred.resolve(response(contextSnapshot)))

    // Follower retries/rebases the SAME chat and shows the completed notice.
    await waitFor(() => {
      expect(within(follower.container).getByText('Context compacted. Your full conversation is preserved.')).toBeVisible()
    }, { timeout: 6000 })

    // Follower persisted compactionRequestId:null and preserved draft/transcript.
    await waitFor(() => {
      const saved = parseRaw(CHAT_KEY)
      expect(saved?.compactionRequestId).toBeNull()
      expect(saved?.draft).toBe('Follower draft')
      expect(saved?.messages).toEqual([
        { role: 'user', content: 'Original question' },
        { role: 'assistant', content: 'Original answer', status: 'done' },
      ])
    })

    // No duplicate compact or stream POSTs from the follower.
    expect(compactCalls).toHaveLength(1)
    expect(streamCalls).toHaveLength(0)

    follower.unmount()

    // Reopen: same state, no resubmit.
    const reopened = render(<Pixel />)
    await within(reopened.container).findByText('Available', {}, { timeout: 6000 })
    await waitFor(() => expect(within(reopened.container).getByPlaceholderText('Message Portal...')).toHaveValue('Follower draft'))
    expect(within(reopened.container).getByText('Original answer')).toBeVisible()
    expect(compactCalls).toHaveLength(1)
    expect(streamCalls).toHaveLength(0)
    reopened.unmount()
  }, 12000)
})
