import { fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import DreamTalk from './DreamTalk' // eslint-disable-line no-unused-vars

const response = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
})

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
      if (url === '/api/talk/message' && options.method === 'POST') {
        expect(JSON.parse(options.body)).toEqual({ text: 'What can you do?' })
        return response({ session_id: 'sid', text: 'I can help from this Dream Server.' })
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
      if (url === '/api/talk/message') {
        return response({ session_id: 'sid', text: 'Spoken answer.' })
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
