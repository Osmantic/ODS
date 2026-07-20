import { fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import ODSTalk from './ODSTalk' // eslint-disable-line no-unused-vars

const response = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
})

// Build a fake fetch Response with a streaming body. ``frames`` is an array
// of JS objects; each one is encoded as a single SSE frame (data: <json>\n\n).
// If ``chunks`` is provided it's an array of frame-index arrays — each chunk
// emits those frames in one reader.read() pass, simulating partial transport.
const sseResponse = (frames, { status = 200, chunks, holdOpen = false } = {}) => {
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
      if (idx >= chunkGroups.length) {
        // ``holdOpen`` keeps the reader awaiting indefinitely after the final
        // chunk — useful when a test wants to assert intermediate spinner /
        // status UI without the SPA's "stream closed → finalize bubble" path
        // wiping the state before the assertion fires.
        if (holdOpen) return new Promise(() => {})
        return { done: true, value: undefined }
      }
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

describe('ODSTalk', () => {
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
          { type: 'delta', text: ' from this ODS.' },
          { type: 'complete', session_id: 'sid', text: 'I can help from this ODS.', status: 'ok' },
          { type: 'done' },
        ])
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ODSTalk />)

    expect(await screen.findByText('Ready')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('Message ODS'), {
      target: { value: 'What can you do?' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    expect(await screen.findByText('What can you do?')).toBeInTheDocument()
    expect(await screen.findByText('I can help from this ODS.')).toBeInTheDocument()
  })

  test('shows model compatibility reason and disables send when text chat is blocked', async () => {
    const fetchMock = vi.fn(async (url) => {
      if (url === '/api/talk/status') {
        return response({
          reason: 'Phi direct chat works, but ODS Talk is not revalidated.',
          modelCompatibility: {
            hermesTalk: {
              status: 'unsupported_until_revalidated',
              reason: 'Phi direct chat works, but ODS Talk is not revalidated.',
            },
          },
          capabilities: {
            text_chat: false,
            tts: false,
            audio_message: false,
          },
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ODSTalk />)

    expect((await screen.findAllByText('Phi direct chat works, but ODS Talk is not revalidated.')).length).toBeGreaterThan(0)
    fireEvent.change(screen.getByPlaceholderText('Message ODS'), {
      target: { value: 'hello' },
    })
    expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  test('refreshes transient offline status until text chat becomes ready', async () => {
    const fetchMock = vi.fn(async (url) => {
      if (url === '/api/talk/status') {
        const ready = fetchMock.mock.calls.length > 1
        return response({
          capabilities: {
            text_chat: ready,
            tts: false,
            audio_message: false,
          },
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ODSTalk />)

    expect((await screen.findAllByText('ODS Talk text chat is not available.')).length).toBeGreaterThan(0)
    expect(await screen.findByText('Ready', {}, { timeout: 2500 })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  test('keeps retrying when readiness takes more than one attempt', async () => {
    // The retry is a one-shot setTimeout re-armed by an effect dependency. A
    // failed attempt used to set status/retry flags to the values they already
    // held, so React bailed out of the re-render, no dependency changed, and
    // the effect never ran a second time — the page sat on 'offline' until a
    // manual reload. A backend container taking longer than one interval to
    // come up is the normal case, not an edge case.
    const fetchMock = vi.fn(async (url) => {
      if (url === '/api/talk/status') {
        const ready = fetchMock.mock.calls.length > 3
        return response({
          capabilities: { text_chat: ready, tts: false, audio_message: false },
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ODSTalk />)

    expect((await screen.findAllByText('ODS Talk text chat is not available.')).length).toBeGreaterThan(0)
    expect(await screen.findByText('Ready', {}, { timeout: 8000 })).toBeInTheDocument()
    expect(fetchMock.mock.calls.length).toBeGreaterThan(3)
  })

  test('stops polling once a permanent incompatibility is reported', async () => {
    // A compatibility reason is a settled answer, not a transient outage:
    // retrying must stay off so the page does not poll forever.
    const fetchMock = vi.fn(async (url) => {
      if (url === '/api/talk/status') {
        return response({
          capabilities: { text_chat: false, tts: false, audio_message: false },
          reason: 'Model does not support tool calling.',
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ODSTalk />)

    expect((await screen.findAllByText('Model does not support tool calling.')).length).toBeGreaterThan(0)
    const seen = fetchMock.mock.calls.length
    await new Promise(resolve => setTimeout(resolve, 2500))
    expect(fetchMock.mock.calls.length).toBe(seen)
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

    render(<ODSTalk />)
    expect(await screen.findByText('Ready')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('Message ODS'), {
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

    const { container } = render(<ODSTalk />)
    expect(await screen.findByText('Ready')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('Message ODS'), {
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

  test('renders dynamic SSE status caption + detail while a tool is in flight', async () => {
    // Bridge emits `status` frames between session and the first delta —
    // the SPA should swap the default "Thinking…" caption for the friendly
    // label and show the detail string (e.g. the search query) underneath.
    // Once message.delta frames start arriving, the caption is dropped and
    // the bubble shows live text. Then on `complete`, the bubble settles.
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/talk/status') return response({ capabilities: { text_chat: true } })
      if (url === '/api/talk/message/stream' && options.method === 'POST') {
        return sseResponse([
          { type: 'session', session_id: 'sid' },
          { type: 'status', label: 'Searching the web…', tool: 'web_search', detail: 'weather forecast Philadelphia' },
        ], { chunks: [[0, 1]], holdOpen: true })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ODSTalk />)
    expect(await screen.findByText('Ready')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('Message ODS'), {
      target: { value: 'weather?' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    // The friendly caption replaces the default "Thinking…" wholesale.
    expect(await screen.findByText('Searching the web…')).toBeInTheDocument()
    expect(screen.queryByText('Thinking…')).not.toBeInTheDocument()
    // The detail string (the query Hermes is running) renders below the caption.
    expect(screen.getByText('weather forecast Philadelphia')).toBeInTheDocument()
  })

  test('drops the status caption when message.delta frames start arriving', async () => {
    // Hermes flow on a real tool-using turn: status -> deltas -> complete.
    // Once the first delta lands the bubble must switch from "Searching…"
    // to live token rendering — otherwise the user keeps seeing the stale
    // status while the model is actually writing.
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/talk/status') return response({ capabilities: { text_chat: true } })
      if (url === '/api/talk/message/stream' && options.method === 'POST') {
        return sseResponse([
          { type: 'session', session_id: 'sid' },
          { type: 'status', label: 'Searching the web…', tool: 'web_search', detail: 'weather' },
          { type: 'status', label: null, tool: null, detail: null },
          { type: 'delta', text: 'It is ' },
          { type: 'delta', text: '64°F.' },
          { type: 'complete', session_id: 'sid', text: 'It is 64°F.', status: 'ok' },
          { type: 'done' },
        ])
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ODSTalk />)
    expect(await screen.findByText('Ready')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('Message ODS'), {
      target: { value: 'weather?' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    expect(await screen.findByText('It is 64°F.')).toBeInTheDocument()
    // The status caption is gone once content has rendered.
    expect(screen.queryByText('Searching the web…')).not.toBeInTheDocument()
    expect(screen.queryByText('Thinking…')).not.toBeInTheDocument()
  })

  test('attaching an image POSTs to /api/talk/attachment + shows preview + renders user image', async () => {
    // Stub URL.createObjectURL so the blob: URL the SPA generates for the
    // preview doesn't break jsdom (which has no blob support by default).
    const createdUrls = []
    vi.stubGlobal('URL', {
      ...globalThis.URL,
      createObjectURL: (blob) => {
        const url = `blob:test-${createdUrls.length}`
        createdUrls.push({ url, blob })
        return url
      },
      revokeObjectURL: () => {},
    })

    let attachmentRequest = null
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/talk/status') return response({ capabilities: { text_chat: true } })
      if (url === '/api/talk/attachment' && options.method === 'POST') {
        attachmentRequest = options
        return sseResponse([
          { type: 'session', session_id: 'vision-oneshot' },
          { type: 'delta', text: 'A red square.' },
          { type: 'complete', session_id: 'vision-oneshot', text: 'A red square.', status: 'ok' },
          { type: 'done' },
        ])
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const { container } = render(<ODSTalk />)
    expect(await screen.findByText('Ready')).toBeInTheDocument()

    // Pick a fake image file via the hidden file input.
    const fileInput = container.querySelector('input[type="file"]')
    const blob = new globalThis.Blob([new Uint8Array([137, 80, 78, 71])], { type: 'image/png' })
    const file = new globalThis.File([blob], 'photo.png', { type: 'image/png' })
    fireEvent.change(fileInput, { target: { files: [file] } })

    // Preview strip shows the filename + an X button to discard.
    expect(await screen.findByText('photo.png')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove attachment' })).toBeInTheDocument()

    // Add a caption and send.
    fireEvent.change(screen.getByPlaceholderText('Message ODS'), {
      target: { value: 'What color is this?' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    // Assistant reply renders.
    expect(await screen.findByText('A red square.')).toBeInTheDocument()
    // Backend was hit on the attachment endpoint with FormData.
    expect(attachmentRequest).not.toBeNull()
    expect(attachmentRequest.body).toBeInstanceOf(globalThis.FormData)
    expect(attachmentRequest.body.get('file')).toBeTruthy()
    expect(attachmentRequest.body.get('text')).toBe('What color is this?')
    // The user's bubble shows the image inline.
    const userImg = container.querySelector('img[alt="Attached"]')
    expect(userImg).toBeTruthy()
    expect(userImg.getAttribute('src')).toMatch(/^blob:/)
  })

  test('retry keeps image attachment context after an attachment stream error', async () => {
    vi.stubGlobal('URL', {
      ...globalThis.URL,
      createObjectURL: () => 'blob:retry-image',
      revokeObjectURL: () => {},
    })

    let attachmentRequests = 0
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/talk/status') return response({ capabilities: { text_chat: true } })
      if (url === '/api/talk/attachment' && options.method === 'POST') {
        attachmentRequests += 1
        expect(options.body).toBeInstanceOf(globalThis.FormData)
        expect(options.body.get('file')).toBeTruthy()
        expect(options.body.get('text')).toBe('What color is this?')
        if (attachmentRequests === 1) {
          return sseResponse([
            { type: 'session', session_id: 'vision-oneshot' },
            { type: 'error', status_code: 502, detail: 'Vision timed out.' },
            { type: 'done' },
          ])
        }
        return sseResponse([
          { type: 'session', session_id: 'vision-oneshot' },
          { type: 'delta', text: 'A red square.' },
          { type: 'complete', session_id: 'vision-oneshot', text: 'A red square.', status: 'ok' },
          { type: 'done' },
        ])
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const { container } = render(<ODSTalk />)
    expect(await screen.findByText('Ready')).toBeInTheDocument()

    const fileInput = container.querySelector('input[type="file"]')
    const blob = new globalThis.Blob([new Uint8Array([137, 80, 78, 71])], { type: 'image/png' })
    const file = new globalThis.File([blob], 'photo.png', { type: 'image/png' })
    fireEvent.change(fileInput, { target: { files: [file] } })
    fireEvent.change(screen.getByPlaceholderText('Message ODS'), {
      target: { value: 'What color is this?' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    expect(await screen.findByText('Vision timed out.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry last message' }))

    expect(await screen.findByText('A red square.')).toBeInTheDocument()
    expect(attachmentRequests).toBe(2)
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

    render(<ODSTalk />)
    expect(await screen.findByText('Ready')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('Message ODS'), {
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

    render(<ODSTalk />)

    expect(await screen.findByText('Session expired. Scan the owner card again.')).toBeInTheDocument()
    expect(screen.getByText(/This owner session ended/i)).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Message ODS')).toBeDisabled()
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

    render(<ODSTalk />)

    expect(await screen.findByText('Ready')).toBeInTheDocument()
    expect(screen.getByText(/Live mic needs HTTPS/i)).toBeInTheDocument()
    // Paperclip is now the general image/file attach button (it used to be
    // mis-wired to "Attach voice message" + capture, which made iOS open
    // the video recorder).
    expect(screen.getByRole('button', { name: 'Attach image or file' })).toBeEnabled()
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

    render(<ODSTalk />)
    expect(await screen.findByText('Ready')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Turn spoken replies on' }))
    fireEvent.change(screen.getByPlaceholderText('Message ODS'), {
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
