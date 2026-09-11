export const CHAT_KEY = 'ods.pixel.chat.v1'
const LIBRARY_KEY = 'ods.pixel.conversations.v1'
export const LIBRARY_EVENT = 'ods:pixel-conversations-changed'
export const SELECT_EVENT = 'ods:pixel-select-conversation'
export const DELETE_EVENT = 'ods:pixel-delete-conversation'
const DELETED_KEY = 'ods.pixel.deleted-conversations.v1'
function storedArray(key) {
  const value = JSON.parse(localStorage.getItem(key) || '[]')
  if (!Array.isArray(value)) throw new Error('Saved chat history could not be read. Existing browser data has been preserved.')
  return value
}
const deletedIds = () => storedArray(DELETED_KEY)
export const isConversationDeleted = chatId => deletedIds().includes(chatId)
const valid = item => item?.schema === 1 && /^[A-Za-z0-9_-]{1,128}$/.test(item.chatId || '') && Array.isArray(item.messages)

function currentConversation() {
  try {
    return JSON.parse(localStorage.getItem(CHAT_KEY) || 'null')
  } catch (error) {
    if (error instanceof SyntaxError) return null
    throw error
  }
}

function loadConversations() {
  const entries = storedArray(LIBRARY_KEY).filter(valid)
  const current = currentConversation()
  if (valid(current) && current.messages.length && !entries.some(item => item.chatId === current.chatId)) entries.push(current)
  const deleted = deletedIds()
  return entries.filter(item => !deleted.includes(item.chatId)).sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))
}

export function readConversations() {
  try { return loadConversations() } catch { return [] }
}

export function saveConversation(chat) {
  if (!valid(chat)) throw new Error('Invalid conversation')
  if (deletedIds().includes(chat.chatId)) throw new Error('This conversation was deleted in another tab. Start a new chat.')
  // A read error is not an empty library. Never overwrite unreadable history.
  const entries = loadConversations()
  const previous = entries.find(item => item.chatId === chat.chatId)
  const value = { ...previous, ...chat, updatedAt: Date.now() }
  if (value.messages.length || value.draft?.trim()) {
    const next = [value, ...entries.filter(item => item.chatId !== value.chatId)]
    // Never silently evict an older conversation when browser storage fills up.
    localStorage.setItem(LIBRARY_KEY, JSON.stringify(next))
  }
  localStorage.setItem(CHAT_KEY, JSON.stringify(value))
  window.dispatchEvent(new Event(LIBRARY_EVENT))
}

export function conversationTitle(chat) {
  return chat.messages.find(message => message.role === 'user' && typeof message.content === 'string')?.content.trim().slice(0, 80) || chat.draft?.trim().slice(0, 80) || 'Untitled conversation'
}

export function deleteConversation(chatId) {
  const entries = loadConversations()
  const chat = entries.find(item => item.chatId === chatId)
  if (!chat) return
  if (chat.inFlight || chat.interrupted) throw new Error('Stop or resume this task before deleting its conversation.')
  // Write the deletion marker first: stale open tabs must never resurrect a deleted chat.
  localStorage.setItem(DELETED_KEY, JSON.stringify([...new Set([...deletedIds(), chatId])]))
  localStorage.setItem(LIBRARY_KEY, JSON.stringify(entries.filter(item => item.chatId !== chatId)))
  const current = currentConversation()
  if (current?.chatId === chatId) localStorage.removeItem(CHAT_KEY)
  window.dispatchEvent(new Event(LIBRARY_EVENT))
}
