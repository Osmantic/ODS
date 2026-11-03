import { fireEvent, screen, waitFor } from '@testing-library/react'
import { createElement } from 'react'
import { render } from '../test/test-utils'
import LocalChat from './LocalChat.jsx'
import { STORAGE_KEY } from '../lib/localChatStore'

const READY = { state: 'ready', model: 'Llama-3.2-1B-Instruct-Q4_K_M.gguf', contextSize: 4096 }

function json(value, ok = true) {
  return { ok, json: async () => value }
}

function sse(chunks, { hangAfter = false, signal } = {}) {
  const encoder = new TextEncoder()
  let index = 0
  return { ok: true, body: { getReader: () => ({ read: () => {
    if (index < chunks.length) return Promise.resolve({ value: encoder.encode(chunks[index++]), done: false })
    if (!hangAfter) return Promise.resolve({ value: undefined, done: true })
    return new Promise((_, reject) => signal.addEventListener('abort',
      () => reject(new DOMException('aborted', 'AbortError'))))
  } }) } }
}

const answer = (text, note = { droppedMessages: 0, contextStart: 0, promptTokens: 40 }) => [
  `event: ods\ndata: ${JSON.stringify(note)}\n\n`,
  ...text.split(' ').map(word => `data: ${JSON.stringify({ choices: [{ delta: { content: `${word} ` } }] })}\n\n`),
  'data: [DONE]\n\n',
]

function setup(streams, status = READY) {
  const calls = []
  const fetchMock = vi.fn(async (url, options = {}) => {
    if (url === '/api/local-chat/status') return json(status)
    calls.push(JSON.parse(options.body))
    const next = streams.shift()
    return typeof next === 'function' ? next(options) : next
  })
  vi.stubGlobal('fetch', fetchMock)
  render(createElement(LocalChat))
  return { calls }
}

async function ask(text) {
  await screen.findByText(/4,096 tokens/)  // the model reported ready
  fireEvent.change(screen.getByLabelText('Message'), { target: { value: text } })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).not.toBeDisabled())
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
}

beforeEach(() => localStorage.clear())
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear() })

it('streams an answer, saves the chat, and sends the context start back next time', async () => {
  const { calls } = setup([sse(answer('The capital is Paris.', { droppedMessages: 2, contextStart: 2, promptTokens: 900 })),
    sse(answer('Berlin.'))])
  await ask('Capital of France?')
  expect(await screen.findByText(/The capital is Paris\./)).toBeInTheDocument()
  expect(screen.getByRole('note')).toHaveTextContent('2 earlier messages are still saved here')
  expect(calls[0]).toEqual({ messages: [{ role: 'user', content: 'Capital of France?' }] })
  await ask('And Germany?')
  await screen.findByText(/Berlin\./)
  expect(calls[1].contextStart).toBe(2)
  expect(calls[1].messages).toHaveLength(3)
  const saved = JSON.parse(localStorage.getItem(STORAGE_KEY)).conversations
  expect(saved).toHaveLength(1)
  expect(saved[0].title).toBe('Capital of France?')
  expect(saved[0].messages.map(m => m.role)).toEqual(['user', 'assistant', 'user', 'assistant'])
})

it('a refused message goes back into the box with the reason', async () => {
  setup([json({ error: { code: 'message_too_long', message: 'This message is too long for the model.' } }, false)])
  await ask('a very long message')
  expect(await screen.findByRole('alert')).toHaveTextContent('This message is too long for the model.')
  expect(screen.getByLabelText('Message')).toHaveValue('a very long message')
  expect(screen.queryByLabelText('ODS')).not.toBeInTheDocument()
  expect(localStorage.getItem(STORAGE_KEY)).toBe(null)
})

it('keeps chatting when the browser cannot save', async () => {
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('full', 'QuotaExceededError') })
  setup([sse(answer('Still here.'))])
  await ask('hello')
  expect(await screen.findByText(/Still here\./)).toBeInTheDocument()
  expect(screen.getByText(/not saving chats right now/)).toBeInTheDocument()
})

it('explains a stopped model and does not offer to send', async () => {
  setup([], { state: 'model_stopped', message: 'stopped' })
  expect(await screen.findByText(/local model is stopped/)).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'hi' } })
  expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
})

it('Stop keeps the partial answer', async () => {
  setup([options => sse(['data: {"choices":[{"delta":{"content":"Partial answer"}}]}\n\n'],
    { hangAfter: true, signal: options.signal })])
  await ask('Tell me a story')
  await screen.findByText(/Partial answer/)
  fireEvent.click(screen.getByRole('button', { name: 'Stop' }))
  expect(await screen.findByText(/Stopped\./)).toBeInTheDocument()
  const saved = JSON.parse(localStorage.getItem(STORAGE_KEY)).conversations[0]
  expect(saved.messages[1].content).toContain('Partial answer')
})

it('saves a chat as Markdown', async () => {
  const created = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:chat')
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  setup([sse(answer('Paris.'))])
  await ask('Capital of France?')
  await screen.findByText(/Paris\./)
  fireEvent.click(screen.getByRole('button', { name: 'Save chat as Markdown' }))
  expect(click).toHaveBeenCalled()
  const blob = created.mock.calls[0][0]
  expect(await blob.text()).toContain('## You\n\nCapital of France?')
})
