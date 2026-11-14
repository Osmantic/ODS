import { parseSse, streamLocalChat } from './localChatStream'

export function sseResponse(chunks, { ok = true, json } = {}) {
  const encoder = new TextEncoder()
  let index = 0
  return {
    ok,
    json: async () => json,
    body: ok ? { getReader: () => ({ read: async () => index < chunks.length
      ? { value: typeof chunks[index] === 'string' ? encoder.encode(chunks[index++]) : chunks[index++], done: false }
      : { value: undefined, done: true } }) } : null,
  }
}

describe('SSE parsing', () => {
  it('keeps an event split across chunks until it is complete', () => {
    const first = parseSse('event: ods\ndata: {"contextStart":4}\n\ndata: {"cho')
    expect(first.events).toEqual([{ event: 'ods', data: '{"contextStart":4}' }])
    expect(first.rest).toBe('data: {"cho')
    expect(parseSse(first.rest + 'ices":[]}\n\n').events).toEqual([{ event: 'message', data: '{"choices":[]}' }])
  })

  it('accepts CRLF line endings', () => {
    expect(parseSse('data: [DONE]\r\n\r\n').events).toEqual([{ event: 'message', data: '[DONE]' }])
  })
})

describe('streamLocalChat', () => {
  it('reports the context note, then the text, then finishes', async () => {
    const fetchImpl = vi.fn(async () => sseResponse([
      'event: ods\ndata: {"droppedMessages":2,"contextStart":2,"promptTokens":900}\n\n',
      'data: {"choices":[{"delta":{"content":"Pa"}}]}\n\ndata: {"choices":[{"delta":{"con',
      'tent":"ris"}}]}\n\ndata: [DONE]\n\n',
    ]))
    const pieces = []
    const notes = []
    const result = await streamLocalChat({ messages: [{ role: 'user', content: 'hi' }], contextStart: 2, fetchImpl,
      onContext: note => notes.push(note), onText: piece => pieces.push(piece) })
    expect(result.error).toBe(null)
    expect(pieces.join('')).toBe('Paris')
    expect(notes).toEqual([{ droppedMessages: 2, contextStart: 2, promptTokens: 900 }])
    expect(JSON.parse(fetchImpl.mock.calls[0][1].body)).toEqual({ messages: [{ role: 'user', content: 'hi' }], contextStart: 2 })
  })

  it('keeps a character whose UTF-8 bytes arrive in two network chunks', async () => {
    const bytes = new TextEncoder().encode('data: {"choices":[{"delta":{"content":"Tiếng Việt 👋"}}]}\n\ndata: [DONE]\n\n')
    const accent = bytes.indexOf(0xe1) + 1  // inside "ế"
    const emoji = bytes.indexOf(0xf0) + 2  // inside the emoji
    const fetchImpl = async () => sseResponse([bytes.slice(0, accent), bytes.slice(accent, emoji), bytes.slice(emoji)])
    const pieces = []
    await streamLocalChat({ messages: [], fetchImpl, onText: piece => pieces.push(piece) })
    expect(pieces.join('')).toBe('Tiếng Việt 👋')
  })

  it('omits contextStart when there is none yet', async () => {
    const fetchImpl = vi.fn(async () => sseResponse(['data: [DONE]\n\n']))
    await streamLocalChat({ messages: [{ role: 'user', content: 'hi' }], contextStart: null, fetchImpl })
    expect(JSON.parse(fetchImpl.mock.calls[0][1].body)).toEqual({ messages: [{ role: 'user', content: 'hi' }] })
  })

  it('returns the server code and sentence for a refused request', async () => {
    const error = { code: 'message_too_long', message: 'Shorten it.', promptTokens: 6000, budgetTokens: 3072 }
    const result = await streamLocalChat({ messages: [], fetchImpl: async () => sseResponse([], { ok: false, json: { error } }) })
    expect(result.error).toEqual(error)
  })

  it('falls back to a fixed sentence for an unexpected error body', async () => {
    const result = await streamLocalChat({ messages: [], fetchImpl: async () => sseResponse([], { ok: false, json: { detail: 'x' } }) })
    expect(result.error.code).toBe('model_unavailable')
  })

  it('reports an error event that arrives mid-answer', async () => {
    const fetchImpl = async () => sseResponse(['data: {"choices":[{"delta":{"content":"par"}}]}\n\n',
      'data: {"error":{"code":"model_unavailable","message":"Restart ODS."}}\n\ndata: [DONE]\n\n'])
    const pieces = []
    const result = await streamLocalChat({ messages: [], fetchImpl, onText: piece => pieces.push(piece) })
    expect(pieces).toEqual(['par'])
    expect(result.error).toEqual({ code: 'model_unavailable', message: 'Restart ODS.' })
  })
})
