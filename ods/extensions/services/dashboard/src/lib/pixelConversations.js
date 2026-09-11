import { conversationLabels } from './pixelConversationLabels'

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
const valid = item => item?.schema === 1 && typeof item.chatId === 'string'
  && /^[A-Za-z0-9_-]{1,128}$/.test(item.chatId) && Array.isArray(item.messages)
  && item.messages.every(message => message && ['user', 'assistant'].includes(message.role) && typeof message.content === 'string')
  && (item.draft === undefined || typeof item.draft === 'string')

function currentConversation() {
  try {
    return JSON.parse(localStorage.getItem(CHAT_KEY) || 'null')
  } catch (error) {
    if (error instanceof SyntaxError) return null
    throw error
  }
}

function loadConversations(preserveInvalid = false) {
  const stored = storedArray(LIBRARY_KEY)
  let entries = preserveInvalid ? stored : stored.filter(valid)
  const current = currentConversation()
  if (valid(current) && (current.persistenceVersion === 2 || !entries.some(item => valid(item) && item.chatId === current.chatId))) {
    // The active record is committed first. Reconcile a library write that
    // failed afterward, including deletion of an emptied unsent draft.
    entries = entries.filter(item => !valid(item) || item.chatId !== current.chatId)
    if (current.messages.length || current.draft?.trim()) entries.push(current)
  }
  const deleted = deletedIds()
  return entries.filter(item => !valid(item) || !deleted.includes(item.chatId))
    .sort((a, b) => (Number.isFinite(b?.updatedAt) ? b.updatedAt : 0) - (Number.isFinite(a?.updatedAt) ? a.updatedAt : 0))
}

export function readConversations() {
  try {
    // Isolate unreadable entries in the view; preserve their raw storage on save.
    return loadConversations()
  } catch { return [] }
}

export function saveConversation(chat) {
  if (!valid(chat)) throw new Error('Invalid conversation')
  if (deletedIds().includes(chat.chatId)) throw new Error('This conversation was deleted in another tab. Start a new chat.')
  // A read error is not an empty library. Never overwrite unreadable history.
  const entries = loadConversations(true)
  const previous = entries.find(item => valid(item) && item.chatId === chat.chatId)
  const value = { ...previous, ...chat, updatedAt: Date.now(), persistenceVersion: 2 }
  const remaining = entries.filter(item => !valid(item) || item.chatId !== value.chatId)
  const next = value.messages.length || value.draft?.trim() ? [value, ...remaining] : remaining
  const current = currentConversation()
  if (valid(current) && current.chatId !== value.chatId) {
    // Do not replace the only durable copy of a previous partial save when
    // switching tasks. Flush its reconciled library before moving the pointer.
    localStorage.setItem(LIBRARY_KEY, JSON.stringify(entries))
  }
  // Validate/read the library before either write. Commit the reload authority
  // first so a failed second write cannot restore stale text over newer text.
  localStorage.setItem(CHAT_KEY, JSON.stringify(value))
  try {
    // Never silently evict an older conversation when browser storage fills up.
    localStorage.setItem(LIBRARY_KEY, JSON.stringify(next))
  } finally {
    window.dispatchEvent(new Event(LIBRARY_EVENT))
  }
}

export function conversationTitle(chat) {
  return conversationLabels(chat.chatId).title || chat.messages.find(message => message.role === 'user' && typeof message.content === 'string')?.content.trim().slice(0, 80) || chat.draft?.trim().slice(0, 80) || 'Untitled conversation'
}

export function deleteConversation(chatId) {
  const entries = loadConversations(true)
  const chat = entries.find(item => valid(item) && item.chatId === chatId)
  if (!chat) return
  if (chat.inFlight || chat.interrupted) throw new Error('Stop or resume this task before deleting its conversation.')
  // Write the deletion marker first: stale open tabs must never resurrect a deleted chat.
  localStorage.setItem(DELETED_KEY, JSON.stringify([...new Set([...deletedIds(), chatId])]))
  localStorage.setItem(LIBRARY_KEY, JSON.stringify(entries.filter(item => !valid(item) || item.chatId !== chatId)))
  const current = currentConversation()
  if (current?.chatId === chatId) localStorage.removeItem(CHAT_KEY)
  window.dispatchEvent(new Event(LIBRARY_EVENT))
}
