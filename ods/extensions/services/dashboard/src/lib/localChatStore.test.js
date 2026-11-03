import {
  MAX_CONVERSATIONS, STORAGE_KEY, exportFileName, exportJson, exportMarkdown, loadConversations, newConversation,
  pruneConversations, saveConversations, titleFor, upsertConversation,
} from './localChatStore'
import { isWindowsDesktop } from './runtimeProfile'

const chat = (id, words = 1) => ({ id, title: id, updatedAt: 0, contextStart: null,
  messages: [{ role: 'user', content: 'x'.repeat(words) }, { role: 'assistant', content: 'ok' }] })

function memoryStorage() {
  const data = new Map()
  return { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value), data }
}

describe('runtime profile', () => {
  it('recognizes only the Windows Desktop profile', () => {
    expect(isWindowsDesktop({ runtimeProfile: 'windows-desktop' })).toBe(true)
    expect(isWindowsDesktop({ runtimeProfile: 'compose' })).toBe(false)
    expect(isWindowsDesktop(null)).toBe(false)
  })
})

describe('local chat history', () => {
  it('titles a chat by its first question', () => {
    expect(titleFor([{ role: 'user', content: '  What   is\nODS? ' }])).toBe('What is ODS?')
    expect(titleFor([])).toBe('New chat')
    expect(titleFor([{ role: 'user', content: 'a'.repeat(200) }])).toHaveLength(60)
  })

  it('puts the updated chat first', () => {
    const list = [chat('a'), chat('b'), chat('c')]
    expect(upsertConversation(list, chat('c'), 5).map(item => item.id)).toEqual(['c', 'a', 'b'])
  })

  it('keeps at most the newest 50 chats', () => {
    const list = Array.from({ length: 80 }, (_, i) => chat(`c${i}`))
    const kept = pruneConversations(list)
    expect(kept).toHaveLength(MAX_CONVERSATIONS)
    expect(kept[0].id).toBe('c0')
  })

  it('drops the oldest chats to stay within the byte budget, never the open one', () => {
    // Each chat is about 6.2 KB as UTF-16; two would not fit in 9,000 bytes.
    const list = [chat('open', 3000), chat('old1', 3000), chat('old2', 3000)]
    expect(pruneConversations(list, { maxBytes: 9000 }).map(item => item.id)).toEqual(['open'])
    expect(pruneConversations(list, { maxBytes: 13000 }).map(item => item.id)).toEqual(['open', 'old1'])
    expect(pruneConversations([chat('huge', 20000)], { maxBytes: 100 }).map(item => item.id)).toEqual(['huge'])
  })

  it('a full or unavailable storage never throws', () => {
    const quota = { setItem: () => { throw new DOMException('full', 'QuotaExceededError') } }
    expect(saveConversations([chat('a')], quota).ok).toBe(false)
    expect(loadConversations({ getItem: () => { throw new Error('denied') } })).toEqual([])
  })

  it('round-trips and ignores damaged entries', () => {
    const storage = memoryStorage()
    expect(saveConversations([chat('a'), chat('b')], storage).ok).toBe(true)
    expect(loadConversations(storage).map(item => item.id)).toEqual(['a', 'b'])
    storage.setItem(STORAGE_KEY, '{"conversations":[{"id":"x","messages":[{"role":"system","content":1}]}')
    expect(loadConversations(storage)).toEqual([])
    storage.setItem(STORAGE_KEY, JSON.stringify({ conversations: [chat('ok'), { id: 'bad', messages: 'no' }] }))
    expect(loadConversations(storage).map(item => item.id)).toEqual(['ok'])
  })

  it('exports Markdown and JSON', () => {
    const conversation = { ...newConversation(1), title: 'Paris', messages: [
      { role: 'user', content: 'Capital of France?' }, { role: 'assistant', content: 'Paris.' }] }
    expect(exportMarkdown(conversation)).toBe('# Paris\n\n## You\n\nCapital of France?\n\n## ODS\n\nParis.\n')
    expect(JSON.parse(exportJson(conversation))).toEqual({ version: 1, title: 'Paris', messages: conversation.messages })
  })

  it('names an export after its title in any language', () => {
    expect(exportFileName('Tiếng Việt là gì?')).toBe('Tiếng-Việt-là-gì')
    expect(exportFileName('नमस्ते दुनिया')).toBe('नमस्ते-दुनिया')
    expect(exportFileName('a/b:c*?')).toBe('a-b-c')
    expect(exportFileName('???')).toBe('chat')
    // 𠜎 takes two UTF-16 units: a cut by units would leave half a character.
    expect(exportFileName('𠜎'.repeat(80))).toBe('𠜎'.repeat(60))
  })
})
