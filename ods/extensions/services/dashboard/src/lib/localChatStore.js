// Windows Desktop local chat history, kept in this browser profile.
//
// The server keeps nothing, so storage is the only place a conversation
// lives. It is bounded so it can never fill up and stop chat: the newest 50
// conversations within 4 MB are kept, oldest dropped first, and the open
// conversation is never dropped. A storage failure (private window, quota)
// is reported to the caller and never blocks sending a message.

export const STORAGE_KEY = 'ods.local-chat.v1'
export const MAX_CONVERSATIONS = 50
export const MAX_BYTES = 4 * 1024 * 1024

const TITLE_LENGTH = 60

function isMessage(value) {
  return value && (value.role === 'user' || value.role === 'assistant') && typeof value.content === 'string'
}

function isConversation(value) {
  return value && typeof value.id === 'string' && Array.isArray(value.messages) && value.messages.every(isMessage)
}

export function newConversation(now = Date.now()) {
  return { id: `chat-${now.toString(36)}-${Math.random().toString(36).slice(2, 8)}`, title: 'New chat',
    updatedAt: now, messages: [], contextStart: null }
}

export function titleFor(messages) {
  const first = messages.find(message => message.role === 'user')?.content.trim().replace(/\s+/g, ' ')
  if (!first) return 'New chat'
  return first.length > TITLE_LENGTH ? `${first.slice(0, TITLE_LENGTH - 1)}…` : first
}

// Pure: put the updated conversation first.
export function upsertConversation(list, conversation, now = Date.now()) {
  const updated = { ...conversation, title: titleFor(conversation.messages), updatedAt: now }
  return [updated, ...list.filter(item => item.id !== conversation.id)]
}

function size(list) {
  // UTF-16 code units, which is what browsers count against the quota.
  return JSON.stringify({ version: 1, conversations: list }).length * 2
}

// Pure: newest first in, the bounded list out. The first (open) conversation
// is always kept, even when it alone exceeds the byte budget.
export function pruneConversations(list, { maxConversations = MAX_CONVERSATIONS, maxBytes = MAX_BYTES } = {}) {
  const kept = list.slice(0, maxConversations)
  while (kept.length > 1 && size(kept) > maxBytes) kept.pop()
  return kept
}

export function loadConversations(storage = globalThis.localStorage) {
  let raw
  try {
    raw = storage?.getItem(STORAGE_KEY)
  } catch {
    return [] // Storage unavailable: start empty, sending still works.
  }
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed?.conversations) ? parsed.conversations.filter(isConversation) : []
  } catch {
    return [] // A damaged entry is ignored; the next successful save replaces it.
  }
}

export function saveConversations(list, storage = globalThis.localStorage) {
  const bounded = pruneConversations(list)
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, conversations: bounded }))
    return { ok: true, conversations: bounded }
  } catch {
    return { ok: false, conversations: bounded }
  }
}

export function exportMarkdown(conversation) {
  const lines = [`# ${conversation.title || 'Chat'}`, '']
  for (const message of conversation.messages) {
    lines.push(`## ${message.role === 'user' ? 'You' : 'ODS'}`, '', message.content, '')
  }
  return lines.join('\n')
}

// Letters and marks of every script survive (Vietnamese, Hindi, Japanese);
// the length cut counts code points, so it never splits an emoji.
export function exportFileName(title) {
  const name = (title || '').replace(/[^\p{L}\p{M}\p{N}._-]+/gu, '-').replace(/^-+|-+$/g, '')
  return Array.from(name).slice(0, 60).join('') || 'chat'
}

export function exportJson(conversation) {
  return JSON.stringify({ version: 1, title: conversation.title, messages: conversation.messages }, null, 2)
}
