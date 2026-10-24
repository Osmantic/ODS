// Client for the Windows Desktop local chat stream (/api/local-chat/stream).
//
// The server sends one "ods" event first ({droppedMessages, contextStart,
// promptTokens}), then llama-server's OpenAI-style deltas, then [DONE]. Any
// failure is {error: {code, message}} with a plain sentence for the owner.

// Pure: split SSE text into complete events; the remainder waits for more.
export function parseSse(buffer) {
  const events = []
  const blocks = buffer.split(/\r?\n\r?\n/)
  const rest = blocks.pop()
  for (const block of blocks) {
    let event = 'message'
    const data = []
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
    }
    if (data.length) events.push({ event, data: data.join('\n') })
  }
  return { events, rest }
}

const FALLBACK = { code: 'model_unavailable', message: 'The local model is not answering. Restart ODS and try again.' }

async function errorFrom(response) {
  try {
    const body = await response.json()
    if (body?.error?.code && body?.error?.message) return body.error
  } catch {
    // Not the local chat error shape; use the fixed fallback sentence.
  }
  return FALLBACK
}

export async function streamLocalChat({ messages, contextStart, signal, onContext, onText, fetchImpl = fetch }) {
  const response = await fetchImpl('/api/local-chat/stream', {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages, ...(Number.isInteger(contextStart) ? { contextStart } : {}) }),
  })
  if (!response.ok || !response.body) return { error: await errorFrom(response) }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let error = null
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parsed = parseSse(buffer)
    buffer = parsed.rest
    for (const { event, data } of parsed.events) {
      if (data === '[DONE]') return { error }
      let payload
      try { payload = JSON.parse(data) } catch { continue }
      if (event === 'ods') { onContext?.(payload); continue }
      if (payload?.error) { error = payload.error?.code ? payload.error : FALLBACK; continue }
      for (const choice of payload?.choices || []) {
        const piece = choice?.delta?.content
        if (typeof piece === 'string' && piece) onText?.(piece)
      }
    }
  }
  return { error }
}
