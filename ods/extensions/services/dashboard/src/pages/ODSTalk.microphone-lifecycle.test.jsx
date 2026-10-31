import { installTalkFetch } from '../test/talk-session'
import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import ODSTalk from './ODSTalk' // eslint-disable-line no-unused-vars

function deferred() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

describe('Talk microphone ownership', () => {
  let tracks, getUserMedia, instances, fetchMock
  beforeEach(() => {
    tracks = [{ stop: vi.fn() }]
    getUserMedia = vi.fn(async () => ({ getTracks: () => tracks }))
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } })
    instances = []
    vi.stubGlobal('MediaRecorder', class {
      state = 'inactive'
      mimeType = 'audio/webm'
      constructor(stream) { this.stream = stream; instances.push(this) }
      start() { this.state = 'recording' }
      stop() {
        this.state = 'inactive'
        // Real stop/data callbacks are queued after stop() returns.
        const data = this.ondataavailable
        const stopped = this.onstop
        globalThis.queueMicrotask(() => {
          data?.({ data: new Blob(['voice'], { type: 'audio/webm' }) })
          stopped?.()
        })
      }
    })
    fetchMock = vi.fn(async url => ({ ok: true, status: 200, json: async () => url === '/api/talk/status'
      ? { capabilities: { text_chat: true, audio_message: true } }
      : { text: 'Voice received', transcript: 'Hello' } }))
    installTalkFetch(fetchMock)
  })
  afterEach(() => { vi.unstubAllGlobals(); Object.defineProperty(window, 'isSecureContext', { configurable: true, value: false }) })
  const mount = async () => { const view = render(<ODSTalk />); await screen.findByText('Ready'); return view }
  const start = async () => { fireEvent.click(screen.getByRole('button', { name: 'Record voice' })); await waitFor(() => expect(instances).toHaveLength(1)) }
  const uploads = () => fetchMock.mock.calls.filter(([url]) => url === '/api/talk/audio-message')

  test('leaving while recording stops tracks without uploading queued recorder callbacks', async () => {
    const view = await mount()
    await start()
    view.unmount()
    await act(async () => {})
    expect(tracks[0].stop).toHaveBeenCalled()
    expect(instances[0].state).toBe('inactive')
    expect(uploads()).toHaveLength(0)
  })

  test('navigation after Stop ignores already queued data and stop callbacks', async () => {
    const view = await mount()
    await start()
    fireEvent.click(screen.getByRole('button', { name: 'Stop recording' }))
    view.unmount()
    await act(async () => {})
    expect(tracks[0].stop).toHaveBeenCalled()
    expect(uploads()).toHaveLength(0)
  })

  test('permission resolving after navigation releases tracks without starting an orphan recorder', async () => {
    const permission = deferred()
    getUserMedia.mockReturnValue(permission.promise)
    const view = await mount()
    fireEvent.click(screen.getByRole('button', { name: 'Record voice' }))
    view.unmount()
    const next = await mount()
    await act(async () => permission.resolve({ getTracks: () => tracks }))
    expect(tracks[0].stop).toHaveBeenCalled()
    expect(instances).toHaveLength(0)
    expect(uploads()).toHaveLength(0)
    expect(screen.getByRole('button', { name: 'Record voice' })).toBeEnabled()
    next.unmount()
  })

  test.each(['constructor', 'start'])('a recorder %s failure releases granted tracks', async phase => {
    vi.stubGlobal('MediaRecorder', class {
      constructor() { if (phase === 'constructor') throw new Error('unsupported recorder') }
      start() { throw new Error('cannot start recorder') }
      stop() {}
    })
    await mount()
    fireEvent.click(screen.getByRole('button', { name: 'Record voice' }))
    await waitFor(() => expect(tracks[0].stop).toHaveBeenCalled())
    expect(uploads()).toHaveLength(0)
  })

  test('explicit Stop submits the recorded file once and releases tracks', async () => {
    await mount()
    await start()
    fireEvent.click(screen.getByRole('button', { name: 'Stop recording' }))
    expect(await screen.findByText('Voice received')).toBeInTheDocument()
    expect(tracks[0].stop).toHaveBeenCalled()
    expect(uploads()).toHaveLength(1)
    expect(uploads()[0][1].body.get('file').name).toBe('recording.webm')
  })

  test.each(['permission pending', 'recording'])('text submission cannot compete while capture is %s', async phase => {
    if (phase === 'permission pending') getUserMedia.mockReturnValue(new Promise(() => {}))
    const view = await mount()
    fireEvent.click(screen.getByRole('button', { name: 'Record voice' }))
    if (phase === 'recording') await waitFor(() => expect(instances).toHaveLength(1))
    const composer = screen.getByPlaceholderText('Message ODS')
    fireEvent.change(composer, { target: { value: 'Draft while recording' } })
    expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Stop recording' })).toBeEnabled()
    fireEvent.keyDown(composer, { key: 'Enter' })
    // Direct form submission also goes through the guarded send callback.
    fireEvent.submit(composer.closest('form'))
    expect(fetchMock.mock.calls.filter(([url]) => url !== '/api/talk/status')).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Stop recording' }))
    if (phase === 'recording') {
      expect(await screen.findByText('Voice received')).toBeInTheDocument()
      expect(tracks[0].stop).toHaveBeenCalled()
      expect(uploads()).toHaveLength(1)
    }
    view.unmount()
  })

  test('a second click while permission is pending cancels and cannot start a second capture', async () => {
    const permission = deferred()
    getUserMedia.mockReturnValue(permission.promise)
    await mount()
    fireEvent.click(screen.getByRole('button', { name: 'Record voice' }))
    fireEvent.click(screen.getByRole('button', { name: 'Stop recording' }))
    await act(async () => permission.resolve({ getTracks: () => tracks }))
    expect(getUserMedia).toHaveBeenCalledTimes(1)
    expect(tracks[0].stop).toHaveBeenCalled()
    expect(instances).toHaveLength(0)
    expect(uploads()).toHaveLength(0)
  })
})
