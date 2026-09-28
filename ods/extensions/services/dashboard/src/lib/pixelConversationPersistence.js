import { createConversationWriter, readConversations, isConversationDeleted, PERSISTENCE_OWNERSHIP } from './pixelConversations'
import { createConversationOwnership } from './pixelConversationOwnership'

export { PERSISTENCE_OWNERSHIP }

const CHAT_ID_RE = /^[A-Za-z0-9_-]{1,128}$/

function isLegacyRaw(raw) {
  if (!raw || typeof raw !== 'object') return false
  return raw.persistenceOwnership !== PERSISTENCE_OWNERSHIP
}

export function createConversationPersistence({
  ownership = createConversationOwnership(),
  readLatest = chatId => readConversations().find(c => c.chatId === chatId) ?? null,
  deleted = isConversationDeleted,
} = {}) {
  let generation = 0
  let disposed = false
  let chatId = null
  let writer = null
  let dirty = false
  let passiveRebased = false
  let boundRaw = null

  function releaseOwnership() {
    try { ownership.release() } catch { /* ignore */ }
  }

  function bind(nextChatId, rawSnapshot) {
    if (disposed) return false
    if (typeof nextChatId !== 'string' || !CHAT_ID_RE.test(nextChatId)) return false
    if (rawSnapshot != null && rawSnapshot.chatId !== nextChatId) return false
    releaseOwnership()
    generation++
    chatId = nextChatId
    writer = createConversationWriter(rawSnapshot ?? null)
    dirty = false
    passiveRebased = false
    boundRaw = rawSnapshot ?? null
    return true
  }

  async function acquireAuthor() {
    if (disposed || !writer || !chatId) return false
    dirty = true
    const myGen = generation
    const myId = chatId
    let result
    try {
      result = await ownership.acquire(myId)
    } catch {
      return false
    }
    if (disposed || generation !== myGen || chatId !== myId) return false
    if (!result || !result.owned) return false
    if (!ownership.owns(myId)) return false
    return true
  }

  async function acquirePassive() {
    if (disposed || !writer || !chatId) return { owned: false, reason: 'disposed' }
    if (isLegacyRaw(boundRaw)) return { owned: false, reason: 'legacy' }
    const myGen = generation
    const myId = chatId
    if (!owns()) passiveRebased = false
    let result
    try {
      result = await ownership.acquire(myId)
    } catch {
      return { owned: false, reason: 'unavailable' }
    }
    if (disposed || generation !== myGen || chatId !== myId) {
      return { owned: false, reason: 'released' }
    }
    if (!result || !result.owned) {
      return { owned: false, reason: result?.reason || 'locked' }
    }
    if (!ownership.owns(myId)) return { owned: false, reason: 'released' }
    if (dirty) return { owned: true, rebase: null }
    if (passiveRebased) return { owned: true, rebase: null }
    let latest
    try {
      if (deleted(myId)) {
        releaseOwnership()
        return { owned: false, reason: 'deleted' }
      }
      latest = readLatest(myId)
    } catch {
      releaseOwnership()
      return { owned: false, reason: 'unavailable' }
    }
    if (disposed || generation !== myGen || chatId !== myId) {
      return { owned: false, reason: 'released' }
    }
    if (latest && latest.chatId !== myId) {
      releaseOwnership()
      return { owned: false, reason: 'unavailable' }
    }
    if (latest && isLegacyRaw(latest)) {
      releaseOwnership()
      return { owned: false, reason: 'legacy' }
    }
    if (!latest && boundRaw) {
      releaseOwnership()
      return { owned: false, reason: 'missing' }
    }
    writer = createConversationWriter(latest ?? null)
    boundRaw = latest ?? null
    passiveRebased = true
    return { owned: true, rebase: latest ?? null }
  }

  function owns() {
    if (disposed || !chatId) return false
    try { return ownership.owns(chatId) } catch { return false }
  }

  function assertCurrent() {
    if (disposed || !writer || !chatId) {
      const error = new Error('Conversation is not bound')
      error.code = 'conversation-owned-elsewhere'
      throw error
    }
    if (!owns()) {
      const error = new Error('Conversation is owned elsewhere')
      error.code = 'conversation-owned-elsewhere'
      throw error
    }
    writer.assertCurrent(chatId)
  }

  function commit(record) {
    if (disposed || !writer || !chatId) {
      const error = new Error('Conversation is not bound')
      error.code = 'conversation-owned-elsewhere'
      throw error
    }
    if (!record || record.chatId !== chatId) {
      const error = new Error('Conversation is not bound')
      error.code = 'conversation-owned-elsewhere'
      throw error
    }
    if (!owns()) {
      const error = new Error('Conversation is owned elsewhere')
      error.code = 'conversation-owned-elsewhere'
      throw error
    }
    const stamped = { ...record, persistenceOwnership: PERSISTENCE_OWNERSHIP }
    const result = writer(stamped)
    boundRaw = stamped
    return result
  }

  function release() {
    generation++
    releaseOwnership()
    passiveRebased = false
  }

  function dispose() {
    if (disposed) return
    disposed = true
    generation++
    releaseOwnership()
    writer = null
    chatId = null
    dirty = false
    passiveRebased = false
    boundRaw = null
  }

  return { bind, acquireAuthor, acquirePassive, commit, owns, assertCurrent, release, dispose }
}

export default createConversationPersistence
