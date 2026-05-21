import { fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import DreamTalk from './DreamTalk' // eslint-disable-line no-unused-vars

const response = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
})

// Build a fake fetch Response with a streaming body. ``frames`` is an array
// of JS objects; each one is encoded as a single SSE frame (data: <json>\n\n).
// If ``chunks`` is provided it's an array of frame-index arrays — each chunk
// emits those frames in one reader.read() pass, simulating partial transport.
const sseResponse = (frames, { status = 200, chunks } = {}) => {
  const encoder = new TextEncoder()
  const frameBytes = frames.map(f => encoder.encode(`data: ${JSON.stringify(f)}\n\n`))
  const concatFrames = (group) => {
    const totalLen = group.reduce((acc, i) => acc + frameBytes[i].byteLength, 0)
    const out = new Uint8Array(totalLen)
    let offset = 0
    for (const i of group) {
      out.set(frameBytes[i], offset)
      offset += frameBytes[i].byteLength
    }
    return out
  }
  const chunkGroups = chunks
    ? chunks.map(group => concatFrames(group))
    : frameBytes
  let idx = 0
  const reader = {
    read: async () => {
      if (idx >= chunkGroups.length) return { done: true, value: undefined }
      const value = chunkGroups[idx++]
      return { done: false, value }
    },
  }
  return {
    ok: status >= 200 && status < 300,
    status,
    body: { getReader: () => reader },
    json: async () => ({}),
  }
}

describe('DreamTalk', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: false })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  test('renders a mobile text portal and sends a message', async () => {
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/talk/status') {
        return response({
          capabilities: {
            text_chat: true,
            tts: false,
            audio_message: false,
            live_mic_requires_secure_context: true,
          },
        })
      }
      if (url === '/api/talk/message/stream' && options.method === 'POST') {
        expect(JSON.parse(options.body)).toEqual({ text: 'What can you do?' })
        return sseResponse([
          { type: 'session', session_id: 'sid' },
          { type: 'delta', text: 'I can help' },
          { type: 'delta', text: ' from this Dream Server.' },
          { type: 'complete', session_id: 'sid', text: 'I can help from this Dream Server.', status: 'ok' },
          { type: 'done' },
        ])
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<DreamTalk />)

    expect(await screen.findByText('Ready')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('Message Dream Server'), {
      target: { value: 'What can you do?' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    expect(await screen.findByText('What can you do?')).toBeInTheDocument()
    expect(await screen.findByText('I can help from this Dream Server.')).toBeInTheDocument()
  })

  test('accumulates SSE deltas across split chunks', async () => {
    // Transport may split a single SSE frame across chunk boundaries. The
    // reader has to buffer the partial frame across reads, not drop bytes.
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/talk/status') {
        return response({ capabilities: { text_chat: true } })
      }
      if (url === '/api/talk/message/stream' && options.method === 'POST') {
        // Five frames in three transport chunks: [session][delta delta][complete done]
        return sseResponse(
          [
            { type: 'session', session_id: 'sid' },
            { type: 'delta', text: 'Hello' },
            { type: 'delta', text: ' world' },
            { type: 'complete', session_id: 'sid', text: 'Hello world', status: 'ok' },
            { type: 'done' },
          ],
          { chunks: [[0], [1, 2], [3, 4]] },
        )
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<DreamTalk />)
    expect(await screen.findByText('Ready')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('Message Dream Server'), {
      target: { value: 'hello' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    expect(await screen.findByText('Hello world')).toBeInTheDocument()
  })

  test('renders markdown in assistant bubbles (bold/lists), not raw asterisks', async () => {
    // Hermes formats replies with markdown by default. The bubble should
    // render that as HTML so a list looks like a list, not "- one\n- two".
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/talk/status') return response({ capabilities: { text_chat: true } })
      if (url === '/api/talk/message/stream' && options.method === 'POST') {
        return sseResponse([
          { type: 'session', session_id: 'sid' },
          { type: 'complete', session_id: 'sid', text: 'Pick **one**:\n\n- alpha\n- beta', status: 'ok' },
          { type: 'done' },
        ])
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const { container } = render(<DreamTalk />)
    expect(await screen.findByText('Ready')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('Message Dream Server'), {
      target: { value: 'choose' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    // Bold renders as <strong>, not as ** in the text.
    expect(await screen.findByText('one')).toBeInTheDocument()
    expect(screen.getByText('one').tagName).toBe('STRONG')
    // List items render as <li> under a <ul>.
    expect(container.querySelector('ul li')).toBeTruthy()
    expect(screen.getByText('alpha').closest('li')).toBeTruthy()
    // And the raw `**` is gone from the DOM text.
    expect(container.textContent).not.toContain('**')
  })

  test('surfaces SSE error frame as an assistant error', async () => {
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/talk/status') return response({ capabilities: { text_chat: true } })
      if (url === '/api/talk/message/stream' && options.method === 'POST') {
        return sseResponse([
          { type: 'session', session_id: 'sid' },
          { type: 'error', status_code: 502, detail: 'Hermes did not finish the response.' },
          { type: 'done' },
        ])
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<DreamTalk />)
    expect(await screen.findByText('Ready')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('Message Dream Server'), {
      target: { value: 'hi' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    expect(await screen.findByText('Hermes did not finish the response.')).toBeInTheDocument()
  })

  test('shows an expired owner-card session state', async () => {
    const fetchMock = vi.fn(async (url) => {
      if (url === '/api/talk/status') return response({ detail: 'expired' }, 401)
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<DreamTalk />)

    expect(await screen.findByText('Session expired. Scan the owner card again.')).toBeInTheDocument()
    expect(screen.getByText(/This owner session ended/i)).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Message Dream Server')).toBeDisabled()
  })

  test('keeps text usable and shows a clear live-mic fallback on local HTTP', async () => {
    const fetchMock = vi.fn(async (url) => {
      if (url === '/api/talk/status') {
        return response({
          capabilities: {
            text_chat: true,
            tts: true,
            audio_message: true,
            live_mic_requires_secure_context: true,
          },
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<DreamTalk />)

    expect(await screen.findByText('Ready')).toBeInTheDocument()
    expect(screen.getByText(/Live mic needs HTTPS/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Attach voice message' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Record voice' })).not.toBeInTheDocument()
  })

  test('can request spoken replies without blocking text chat', async () => {
    vi.stubGlobal('Audio', class {
      addEventListener() {}
      play() { return Promise.resolve() }
    })
    vi.stubGlobal('URL', {
      createObjectURL: () => 'blob:audio',
      revokeObjectURL: vi.fn(),
    })
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/talk/status') {
        return response({
          capabilities: { text_chat: true, tts: true, audio_message: false },
        })
      }
      if (url === '/api/talk/message/stream') {
        return sseResponse([
          { type: 'session', session_id: 'sid' },
          { type: 'delta', text: 'Spoken answer.' },
          { type: 'complete', session_id: 'sid', text: 'Spoken answer.', status: 'ok' },
          { type: 'done' },
        ])
      }
      if (url === '/api/talk/speak' && options.method === 'POST') {
        return {
          ok: true,
          status: 200,
          blob: async () => new globalThis.Blob(['audio'], { type: 'audio/mpeg' }),
        }
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<DreamTalk />)
    expect(await screen.findByText('Ready')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Turn spoken replies on' }))
    fireEvent.change(screen.getByPlaceholderText('Message Dream Server'), {
      target: { value: 'read it' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    expect(await screen.findByText('Spoken answer.')).toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/talk/speak',
      expect.objectContaining({ method: 'POST' }),
    ))
  })
})
