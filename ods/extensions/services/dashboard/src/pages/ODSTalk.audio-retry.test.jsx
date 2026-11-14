import { fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import ODSTalk from './ODSTalk' // eslint-disable-line no-unused-vars

const response = (body, status = 200) => ({ ok: status < 400, status, json: async () => body })

describe('Talk voice retry', () => {
  let uploads, textRequests, nextResponse, recorderStarted
  beforeEach(() => {
    uploads = []
    textRequests = []
    recorderStarted = false
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }) } })
    vi.stubGlobal('MediaRecorder', class {
      state = 'inactive'
      mimeType = 'audio/webm'
      start() { this.state = 'recording'; recorderStarted = true }
      stop() {
        this.state = 'inactive'
        globalThis.queueMicrotask(() => {
          this.ondataavailable?.({ data: new Blob(['original recorded bytes'], { type: 'audio/webm' }) })
          this.onstop?.()
        })
      }
    })
    vi.stubGlobal('fetch', vi.fn(async (url, options) => {
      if (url === '/api/talk/status') return response({ capabilities: { text_chat: true, audio_message: true } })
      if (url === '/api/talk/audio-message') {
        uploads.push(options.body.get('file'))
        return nextResponse()
      }
      textRequests.push(JSON.parse(options.body))
      return response({ detail: 'Text retry reached' }, 503)
    }))
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: false })
  })
  const record = async () => {
    render(<ODSTalk />)
    await screen.findByText('Ready')
    fireEvent.click(screen.getByRole('button', { name: 'Record voice' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Stop recording' })).toBeEnabled())
    await waitFor(() => expect(recorderStarted).toBe(true))
    fireEvent.click(screen.getByRole('button', { name: 'Stop recording' }))
  }

  test.each(['http', 'network'])('retry preserves the recording after a %s failure before transcription', async failure => {
    nextResponse = () => failure === 'network' ? Promise.reject(new Error('Connection lost')) : response({ detail: 'Transcription unavailable' }, 503)
    await record()
    await screen.findByRole('button', { name: 'Retry last message' })
    expect(uploads).toHaveLength(1)
    nextResponse = () => response({ transcript: 'Turn on the lights', text: 'Done', status: 'ok' })
    fireEvent.click(screen.getByRole('button', { name: 'Retry last message' }))
    await waitFor(() => expect(uploads).toHaveLength(2))
    expect(uploads[1].name).toBe('recording.webm')
    expect(uploads[1].type).toBe('audio/webm')
    expect(uploads[1].size).toBe(uploads[0].size)
    const read = file => new Promise(resolve => { const reader = new globalThis.FileReader(); reader.onload = () => resolve(reader.result); reader.readAsText(file) })
    expect(await read(uploads[1])).toBe('original recorded bytes')
    expect(textRequests).toEqual([])
    expect(await screen.findByText('Turn on the lights')).toBeInTheDocument()
    expect(await screen.findByText('Done')).toBeInTheDocument()
  })

  test('retry uses the received transcript when inference fails after transcription', async () => {
    nextResponse = () => response({ transcript: 'Turn on the lights', text: 'Partial answer', status: 'error' })
    await record()
    fireEvent.click(await screen.findByRole('button', { name: 'Retry last message' }))
    await waitFor(() => expect(textRequests).toEqual([{ text: 'Turn on the lights' }]))
    expect(uploads).toHaveLength(1)
  })
})
