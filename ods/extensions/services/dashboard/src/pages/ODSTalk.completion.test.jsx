import { installTalkFetch, boundFrame } from '../test/talk-session'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { render } from '../test/test-utils'
import ODSTalk from './ODSTalk' // eslint-disable-line no-unused-vars

const encode = value => new TextEncoder().encode(`data: ${JSON.stringify(boundFrame(value))}\n\n`)
const json = body => ({ ok: true, status: 200, json: async () => body })

function stream(frames, { open = false, fail = false, split = false } = {}) {
  const cancel = vi.fn()
  let transport
  const body = new globalThis.ReadableStream({
    start(controller) {
      transport = controller
      for (const frame of frames) {
        const bytes = encode(frame)
        if (split) {
          for (const byte of bytes) controller.enqueue(new Uint8Array([byte]))
        } else controller.enqueue(bytes)
      }
      if (!open && !fail) controller.close()
    },
    cancel,
  })
  return { response: { ok: true, status: 200, body }, body, cancel,
    fail: () => transport.error(new Error('Network connection lost')) }
}

async function start(reply, { spoken = false, attachment = false } = {}) {
  localStorage.setItem('ods-talk-spoken-replies', spoken ? '1' : '0')
  const fetcher = vi.fn(async url => {
    if (url === '/api/talk/status') return json({ capabilities: { text_chat: true, tts: spoken } })
    if (url === '/api/talk/message/stream' || url === '/api/talk/attachment') return reply.response
    throw new Error(`Unexpected request: ${url}`)
  })
  installTalkFetch(fetcher)
  render(<ODSTalk />)
  await screen.findByText('Ready')
  if (attachment) {
    fireEvent.change(document.querySelector('input[type="file"][accept*="image"]'), {
      target: { files: [new File(['reference'], 'notes.txt', { type: 'text/plain' })] },
    })
  }
  fireEvent.change(screen.getByPlaceholderText('Message ODS'), { target: { value: 'Check this answer' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
  return fetcher
}

afterEach(() => { localStorage.removeItem('ods-talk-spoken-replies'); vi.unstubAllGlobals() })

it.each([
  ['empty EOF', []],
  ['status-only EOF', [{ type: 'status', label: 'Searching' }]],
  ['done without completion', [{ type: 'done' }]],
])('does not present %s as a successful answer', async (_name, frames) => {
  const reply = stream(frames)
  await start(reply)
  expect(await screen.findByRole('alert')).toHaveTextContent('ended before it was complete')
  expect(screen.getByRole('button', { name: 'Retry last message' })).toBeDisabled()
  expect(reply.body.locked).toBe(false)
})

it.each([false, true])('retains interrupted text and suppresses speech (attachment: %s)', async attachment => {
  const reply = stream([{ type: 'delta', text: 'Received partial answer' }])
  const fetcher = await start(reply, { spoken: true, attachment })
  expect(await screen.findByRole('alert')).toHaveTextContent('ended before it was complete')
  expect(screen.getByText('Received partial answer')).toBeVisible()
  expect(fetcher.mock.calls.some(([url]) => url === '/api/talk/speak')).toBe(false)
  expect(fetcher.mock.calls.some(([url]) => url === (attachment ? '/api/talk/attachment' : '/api/talk/message/stream'))).toBe(true)
})

it('preserves partial text and blocks retry after an unconfirmed server failure', async () => {
  const reply = stream([{ type: 'delta', text: 'Useful partial result' }, { type: 'error', detail: 'Hermes stopped working.' }, { type: 'done' }])
  await start(reply)
  expect(await screen.findByRole('alert')).toHaveTextContent('Hermes stopped working.')
  expect(screen.getByText('Useful partial result')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Retry last message' })).toBeDisabled()
})

it('retains text if the transport rejects after a delta', async () => {
  const reply = stream([{ type: 'delta', text: 'Result before disconnect' }], { fail: true })
  await start(reply)
  await screen.findByText('Result before disconnect')
  reply.fail()
  expect(await screen.findByRole('alert')).toHaveTextContent('Network connection lost')
  expect(screen.getByText('Result before disconnect')).toBeVisible()
  expect(reply.body.locked).toBe(false)
})

it('finishes at done without waiting for socket closure, cancels unread frames and releases the reader', async () => {
  const reply = stream([{ type: 'delta', text: 'Draft answer' }, { type: 'complete', text: 'Final answer', warning: 'Check the sources.' }, { type: 'done' }, { type: 'delta', text: 'must be ignored' }], { open: true, split: true })
  await start(reply)
  expect(await screen.findByText('Final answer')).toBeVisible()
  expect(screen.getByText('Check the sources.')).toBeVisible()
  await waitFor(() => expect(reply.cancel).toHaveBeenCalledTimes(1))
  expect(reply.body.locked).toBe(false)
  expect(screen.queryByText(/must be ignored/)).toBeNull()
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.queryByRole('button', { name: 'Retry last message' })).toBeNull()
  fireEvent.change(screen.getByPlaceholderText('Message ODS'), { target: { value: 'Next question' } })
  expect(screen.getByRole('button', { name: 'Send message' })).toBeEnabled()
})

it('accepts the authoritative complete receipt when EOF precedes done', async () => {
  const reply = stream([{ type: 'complete', text: 'Complete answer' }])
  await start(reply)
  expect(await screen.findByText('Complete answer')).toBeVisible()
  expect(screen.queryByRole('alert')).toBeNull()
  expect(reply.body.locked).toBe(false)
})

it.each([
  ['error', 'Hermes reported an error before finishing the response.'],
  ['interrupted', 'The response was interrupted before it finished.'],
  ['unexpected', 'The response ended with an unrecognized outcome.'],
])('keeps received text and permits retry for a complete event with status %s', async (status, detail) => {
  // The installed Hermes producer uses message.complete for failed and
  // interrupted turns too; its terminal text can contain cancellation metadata.
  const reply = stream([
    { type: 'delta', text: 'Useful work before the interruption' },
    { type: 'complete', text: 'Terminal failure metadata', status },
    { type: 'done' },
  ])
  const fetcher = await start(reply, { spoken: true })
  expect(await screen.findByRole('alert')).toHaveTextContent(detail)
  expect(screen.getByText('Useful work before the interruption')).toBeVisible()
  expect(screen.queryByText('Terminal failure metadata')).toBeNull()
  expect(screen.getByRole('button', { name: 'Retry last message' })).toHaveProperty('disabled', status === 'unexpected')
  expect(fetcher.mock.calls.some(([url]) => url === '/api/talk/speak')).toBe(false)
  expect(reply.body.locked).toBe(false)
})

it('retains failed terminal text when no earlier answer text was streamed', async () => {
  await start(stream([
    { type: 'complete', text: 'Retained work from Hermes', status: 'error' },
    { type: 'done' },
  ]))
  expect(await screen.findByRole('alert')).toHaveTextContent('Hermes reported an error')
  expect(screen.getByText('Retained work from Hermes')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Retry last message' })).toBeEnabled()
})

it.each(['complete', 'ok'])('accepts successful upstream status %s', async status => {
  const fetcher = await start(stream([
    { type: 'delta', text: 'Earlier draft' },
    { type: 'complete', text: 'Successful terminal answer', status },
    { type: 'done' },
  ]), { spoken: true })
  expect(await screen.findByText('Successful terminal answer')).toBeVisible()
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.queryByRole('button', { name: 'Retry last message' })).toBeNull()
  await waitFor(() => expect(fetcher.mock.calls.some(([url]) => url === '/api/talk/speak')).toBe(true))
})

describe('voice messages', () => {
  // /api/talk/audio-message answers with one JSON reply that carries the same
  // Hermes turn status as the stream's complete event.
  let recorders
  beforeEach(() => {
    recorders = []
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
  })
  afterEach(() => { Object.defineProperty(window, 'isSecureContext', { configurable: true, value: false }) })

  async function sendVoice(reply) {
    localStorage.setItem('ods-talk-spoken-replies', '1')
    const tracks = [{ stop: vi.fn() }]
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: vi.fn(async () => ({ getTracks: () => tracks })) } })
    vi.stubGlobal('MediaRecorder', class {
      state = 'inactive'
      mimeType = 'audio/webm'
      constructor() { recorders.push(this) }
      start() { this.state = 'recording' }
      stop() {
        this.state = 'inactive'
        const data = this.ondataavailable
        const stopped = this.onstop
        globalThis.queueMicrotask(() => {
          data?.({ data: new Blob(['voice'], { type: 'audio/webm' }) })
          stopped?.()
        })
      }
    })
    const fetcher = vi.fn(async url => {
      if (url === '/api/talk/status') return json({ capabilities: { text_chat: true, tts: true, audio_message: true } })
      if (url === '/api/talk/audio-message') return json(reply)
      if (url === '/api/talk/message/stream') return stream([{ type: 'complete', text: 'Answer on retry' }, { type: 'done' }]).response
      throw new Error(`Unexpected request: ${url}`)
    })
    installTalkFetch(fetcher)
    render(<ODSTalk />)
    await screen.findByText('Ready')
    fireEvent.click(screen.getByRole('button', { name: 'Record voice' }))
    await waitFor(() => expect(recorders).toHaveLength(1))
    fireEvent.click(screen.getByRole('button', { name: 'Stop recording' }))
    return fetcher
  }
  const spoke = fetcher => fetcher.mock.calls.some(([url]) => url === '/api/talk/speak')

  it.each([
    ['interrupted', 'The response was interrupted before it finished.'],
    ['error', 'Hermes reported an error before finishing the response.'],
    ['unexpected', 'The response ended with an unrecognized outcome.'],
  ])('never speaks a voice reply whose turn ended %s, and Retry resends the transcript', async (status, detail) => {
    const fetcher = await sendVoice({ text: 'Work before the stop', transcript: 'What is the weather?', status })
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(detail)
    expect(within(alert).getByRole('link', { name: 'Get help on Discord' })).toHaveAttribute('href', 'https://discord.gg/4ntNp9MAwC')
    expect(screen.getByText('Work before the stop')).toBeVisible()
    expect(screen.getByText('What is the weather?')).toBeVisible()
    expect(spoke(fetcher)).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Retry last message' }))
    expect(await screen.findByText('Answer on retry')).toBeVisible()
    const retried = fetcher.mock.calls.find(([url]) => url === '/api/talk/message/stream')
    expect(JSON.parse(retried[1].body)).toEqual({ text: 'What is the weather?' })
  })

  it.each([undefined, 'ok', 'complete'])('speaks a successful voice reply (status %s)', async status => {
    const fetcher = await sendVoice({ text: 'Sunny today', transcript: 'What is the weather?', status })
    expect(await screen.findByText('Sunny today')).toBeVisible()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Retry last message' })).toBeNull()
    await waitFor(() => expect(spoke(fetcher)).toBe(true))
  })
})
