import { beforeEach, vi } from 'vitest'
import { TALK_HISTORY_KEY } from '../hooks/useTalkConversation'

beforeEach(() => localStorage.removeItem(TALK_HISTORY_KEY))

// Existing presentation tests mock only the response under test. Supply the
// explicit allocation separately; continuity tests exercise the real contract.
export function installTalkFetch(fetcher) {
  vi.stubGlobal('fetch', async (url, options) => {
    if (url === '/api/talk/session') return {
      ok: true, status: 200,
      json: async () => ({ conversation_id: 'test-conversation', expires_at: Math.floor(Date.now() / 1000) + 3600 }),
    }
    const response = await fetcher(url, options)
    if (url === '/api/talk/audio-message' && response.ok) {
      return { ...response, json: async () => ({ ...await response.json(), conversation_id: 'test-conversation' }) }
    }
    return response
  })
}

export function boundFrame(frame) {
  return ['session', 'complete'].includes(frame.type)
    ? { conversation_id: 'test-conversation', ...frame } : frame
}
