import { conversationLabels, deleteConversationLabels } from './pixelConversationLabels'
import {parseProjectTasks} from './pixelTaskActivity'
import {draftImageReceipts, messageImageRefs} from './pixelImages'
import {sha256} from '@noble/hashes/sha2.js'

export const CHAT_KEY = 'ods.pixel.chat.v1'
export const TAB_CHAT_KEY = 'ods.pixel.active-chat.v1'
const RECORD_PREFIX = 'ods.pixel.conversation.v2.'
const TOMBSTONE_PREFIX = 'ods.pixel.deleted-conversation.v2.'
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
export const isConversationDeleted = chatId => {
  const marker = localStorage.getItem(TOMBSTONE_PREFIX + chatId)
  if (marker !== null && marker !== '1') throw new Error('Saved deletion marker could not be read. Existing browser data has been preserved.')
  return deletedIds().includes(chatId) || marker === '1'
}
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

export function rememberActiveConversation(chatId) {
  // Selection is local to a browser tab. Shared legacy storage remains a
  // compatibility mirror, never the identity to restore in another open tab.
  try { globalThis.sessionStorage?.setItem(TAB_CHAT_KEY, chatId) } catch { /* Durable history is independent of selection. */ }
}

export function readActiveConversation() {
  let chatId
  try { chatId = globalThis.sessionStorage?.getItem(TAB_CHAT_KEY) } catch { /* Legacy fallback below. */ }
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(chatId || '')) return currentConversation()
  if (isConversationDeleted(chatId)) return null
  const visible = loadConversations().find(chat => chat.chatId === chatId)
  if (visible) return visible
  const stored = durableConversation(chatId)
  const mirror = currentConversation()
  if (legacyRelation(stored, mirror) === 'newer') return mirror
  return stored || {schema:1, chatId, messages:[], draft:''}
}

function durableConversation(chatId) {
  const raw = localStorage.getItem(RECORD_PREFIX + chatId)
  if (raw === null) return null
  const value = JSON.parse(raw)
  if (!valid(value) || value.chatId !== chatId || value.persistenceDigest !== undefined && !validSnapshotDigest(value)) {
    throw new Error('Saved conversation could not be read. Existing browser data has been preserved.')
  }
  return value
}

function validSnapshotDigest(chat) {
  return typeof chat?.persistenceDigest === 'string' && /^[a-f0-9]{64}$/.test(chat.persistenceDigest)
    && chat.persistenceDigest === snapshotHash(conversationSnapshot(chat))
}

function knownLibraryMirror(record, mirror) {
  if (!valid(mirror) || legacyRelation(record, mirror) !== 'conflict') return true
  // Our own stale index copies remain self-consistent at any age. Older
  // clients do not update this digest when editing; never discard that edit.
  return validSnapshotDigest(mirror) || record.persistenceLegacyBase === snapshotHash(conversationSnapshot(mirror))
}

function legacyRelation(record, current) {
  // Parent snapshots distinguish a partial mirror commit from an unrelated
  // legacy edit. No timestamp can authorize replacing a different transcript.
  if (!valid(record) || !valid(current) || record.chatId !== current.chatId) return 'separate'
  const own = conversationSnapshot(record), mirror = conversationSnapshot(current)
  if (own === mirror) return 'same'
  if (current.persistenceParent === snapshotHash(own)) return 'newer'
  if (record.persistenceParent === snapshotHash(mirror)) return 'older'
  return 'conflict'
}

function reconcileRecord(record, library, current) {
  let value = record
  for (const mirror of [library, current]) {
    if (legacyRelation(value, mirror) === 'newer') value = mirror
  }
  return value
}

function conversationChanged() {
  const error = new Error('This conversation changed in another tab. Download a recovery copy of your unsaved text, then reload this page to read the saved version.')
  error.code = 'conversation-changed'
  throw error
}

function loadConversations(preserveInvalid = false) {
  const stored = storedArray(LIBRARY_KEY)
  let entries = preserveInvalid ? stored : stored.filter(valid)
  const current = currentConversation()
  if (valid(current) && (current.persistenceVersion === 2 || !entries.some(item => valid(item) && item.chatId === current.chatId))) {
    // The active record is committed first. Reconcile a library write that
    // failed afterward, including deletion of an emptied unsent draft.
    entries = entries.filter(item => !valid(item) || item.chatId !== current.chatId)
    if (current.messages.length || current.draft?.trim() || current.draftImages?.length) entries.push(current)
  }
  // Each conversation commits independently. A concurrent writer can replace
  // the library index, but cannot erase another chat's durable record.
  for (let index = 0; index < localStorage.length; index++) {
    const key = localStorage.key(index)
    if (!key?.startsWith(RECORD_PREFIX)) continue
    const chatId = key.slice(RECORD_PREFIX.length)
    if (!/^[A-Za-z0-9_-]{1,128}$/.test(chatId)) continue
    const record = durableConversation(chatId)
    if (!record) continue
    const library = stored.find(item => valid(item) && item.chatId === chatId)
    if (!knownLibraryMirror(record, library)) conversationChanged()
    const value = reconcileRecord(record, library, current)
    entries = entries.filter(item => !valid(item) || item.chatId !== chatId)
    if (value.messages.length || value.draft?.trim() || value.draftImages?.length) entries.push(value)
  }
  return entries.filter(item => !valid(item) || !isConversationDeleted(item.chatId))
    .sort((a, b) => (Number.isFinite(b?.updatedAt) ? b.updatedAt : 0) - (Number.isFinite(a?.updatedAt) ? a.updatedAt : 0))
}

export function readConversations() {
  try {
    // Isolate unreadable entries in the view; preserve their raw storage on save.
    return loadConversations()
  } catch { return [] }
}

function conversationSnapshot(chat) {
  if (!chat) return null
  // Save timestamps alone do not make identical content a conflicting edit.
  return JSON.stringify(Object.fromEntries(Object.entries(chat)
    .filter(([key]) => !['updatedAt', 'persistenceVersion', 'persistenceRevision', 'persistenceParent', 'persistenceDigest', 'persistenceLegacyBase', 'recoverySource'].includes(key))
    .sort(([left], [right]) => left.localeCompare(right))))
}

function persistenceRevision() {
  // getRandomValues is also available on HTTP LAN origins, unlike randomUUID.
  return Array.from(globalThis.crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('')
}

const snapshotHash = snapshot => Array.from(sha256(new TextEncoder().encode(snapshot)), byte => byte.toString(16).padStart(2, '0')).join('')
function recoveryContext(chat, includeWorkspace = false) {
  return conversationSnapshot({...Object.fromEntries(Object.entries(chat)
    .filter(([key]) => !['requestId', 'inFlight', 'interrupted', ...(includeWorkspace ? [] : ['preview', 'workspaceOpen'])].includes(key))),
  messages:chat.messages.slice(0, -1)})
}

const terminalOutcome = message => JSON.stringify({status:message?.status,
  content:message?.content || (message?.status === 'done' ? 'Completed without a text response.' : '')})

const EMPTY_DRAFT_KEYS = new Set(['schema', 'chatId', 'messages', 'draft', 'requestId', 'inFlight',
  'interrupted', 'contextStart', 'compactionRequestId', 'preview', 'workspaceOpen', 'updatedAt', 'persistenceVersion', 'persistenceRevision', 'persistenceParent', 'persistenceDigest', 'persistenceLegacyBase', 'draftImages'])
function omittedEmptyBaseline(chat) {
  // The library omits an empty draft. Moving the shared active pointer does
  // not edit that draft, but pending operations and unknown metadata stay strict.
  return Array.isArray(chat?.messages) && chat.messages.length === 0 && !chat.draft?.trim() && !chat.draftImages?.length
    && !chat.requestId && !chat.inFlight && !chat.interrupted && !chat.compactionRequestId && !chat.preview
    && (chat.contextStart == null || chat.contextStart === 0)
    && Object.keys(chat).every(key => EMPTY_DRAFT_KEYS.has(key))
}

/** Bind a mounted editor to the exact record it read, including legacy data.
 * This is an optimistic stale-editor check; localStorage has no atomic CAS.
 */
export function createConversationWriter(initial = null) {
  let record = initial
  let chatId = initial?.chatId
  let expected = conversationSnapshot(initial)
  let omitted = omittedEmptyBaseline(initial)
  // A recovery receipt names one exact pending revision, never a transcript or
  // a chain of earlier receipts. Only its original writer can finish the handoff.
  let revision = initial?.persistenceRevision
  let requestId = initial?.requestId
  let ownsPending = false
  const committed = value => {
    record = value
    chatId = value.chatId; expected = conversationSnapshot(value); omitted = omittedEmptyBaseline(value)
    revision = value.persistenceRevision; requestId = value.requestId
    ownsPending = value.inFlight === true && Boolean(requestId)
  }
  const recoveryOfExpected = current => Boolean(ownsPending && revision && requestId && current?.chatId === chatId
    && current.recoverySource?.revision === revision && current.recoverySource?.requestId === requestId
    && current.recoverySource?.sourceHash === snapshotHash(expected)
    && current.recoverySource?.resultHash === snapshotHash(conversationSnapshot(current)))
  const write = chat => saveConversation(chat, {
    matches: current => {
      if (conversationSnapshot(current) === (chat.chatId === chatId ? expected : null)
        || (chat.chatId === chatId && current === null && omitted)) return true
      if (chat.chatId !== chatId || !recoveryOfExpected(current)) return false
      const pending = chat.inFlight || chat.interrupted || chat.requestId
      // Only response progress may be ignored while this reader drains. A
      // sender-side edit must report a conflict immediately, and must not regain
      // permission to replace the recovery when a later DONE reaches the UI.
      if (chat.messages.at(-1)?.role !== 'assistant'
        || (pending ? recoveryContext(record, true) !== recoveryContext({...record, ...chat}, true)
          : terminalOutcome(current.messages.at(-1)) !== terminalOutcome(chat.messages.at(-1)))) {
        ownsPending = false
        return false
      }
      return true
    },
    // A retained terminal response can arrive before the originating SSE
    // reader drains. Never replace it with that reader's remaining partials.
    skip: current => recoveryOfExpected(current) && (chat.inFlight || chat.interrupted || chat.requestId),
    committed,
  })
  write.recover = chat => {
    if (!requestId || chat.chatId !== chatId || chat.requestId || chat.inFlight || chat.interrupted
      || record.messages.at(-1)?.role !== 'assistant' || chat.messages.at(-1)?.role !== 'assistant'
      || recoveryContext(record) !== recoveryContext(chat)) throw new Error('Invalid conversation recovery')
    return saveConversation(chat, {
      matches: current => conversationSnapshot(current) === expected,
      recoverySource: revision ? {revision, requestId, sourceHash:snapshotHash(expected)} : null,
      committed,
    })
  }
  // Selecting an existing conversation may move the shared active pointer,
  // but must not normalize a live request or end a pending recovery handoff.
  write.activate = () => saveConversation(record, {
    matches: current => conversationSnapshot(current) === expected,
    activate: true,
    committed: value => {committed(value); ownsPending = false},
  })
  return write
}

export function saveConversation(chat, checkpoint) {
  if (!valid(chat)) throw new Error('Invalid conversation')
  draftImageReceipts(chat.draftImages)
  chat.messages.forEach(messageImageRefs)
  if (chat.messages.some(message=>message.projectTasks!==undefined
    && (message.role!=='assistant' || !parseProjectTasks(message.projectTasks)))) throw new Error('Invalid project metadata')
  if (isConversationDeleted(chat.chatId)) throw new Error('This conversation was deleted in another tab. Start a new chat.')
  // A read error is not an empty library. Never overwrite unreadable history.
  const entries = loadConversations(true)
  const previous = entries.find(item => valid(item) && item.chatId === chat.chatId)
  const current = currentConversation()
  const record = durableConversation(chat.chatId)
  // Empty drafts are absent from the library but still have an active record.
  const reconciled = record && reconcileRecord(record, previous, current)
  const durable = reconciled && (reconciled.messages.length || reconciled.draft?.trim() || reconciled.draftImages?.length || omittedEmptyBaseline(reconciled)) ? reconciled : null
  const latest = durable || (valid(current) && current.chatId === chat.chatId && (current.persistenceVersion === 2 || !previous) ? current : previous)
  if (legacyRelation(reconciled, current) === 'conflict' || checkpoint && !checkpoint.matches(latest ?? null)) conversationChanged()
  if (checkpoint?.skip?.(latest)) return latest
  const value = checkpoint?.activate
    ? {...latest, persistenceVersion:2, persistenceRevision:latest.persistenceRevision || persistenceRevision()}
    : { ...previous, ...chat, updatedAt: Date.now(), persistenceVersion: 2, persistenceRevision: persistenceRevision() }
  if (!checkpoint?.activate) {
    value.persistenceParent = latest ? snapshotHash(conversationSnapshot(latest)) : null
    value.persistenceLegacyBase = record?.persistenceLegacyBase || previous?.persistenceLegacyBase
      || (latest && !validSnapshotDigest(latest) ? snapshotHash(conversationSnapshot(latest)) : null)
  }
  // A normal edit (even one made by the recovery observer) ends the handoff.
  // Never carry a receipt forward through an object spread from saved data.
  if (!checkpoint?.activate) delete value.recoverySource
  if (checkpoint?.recoverySource) value.recoverySource = {...checkpoint.recoverySource, resultHash:snapshotHash(conversationSnapshot(value))}
  if (!checkpoint?.activate) value.persistenceDigest = snapshotHash(conversationSnapshot(value))
  const remaining = entries.filter(item => !valid(item) || item.chatId !== value.chatId)
  const next = value.messages.length || value.draft?.trim() || value.draftImages?.length ? [value, ...remaining] : remaining
  if (valid(current) && current.chatId !== value.chatId) {
    // Do not replace the only durable copy of a previous partial save when
    // switching tasks. Flush its reconciled library before moving the pointer.
    const foreignRecord = durableConversation(current.chatId)
    const foreign = foreignRecord ? reconcileRecord(foreignRecord,
      entries.find(item => valid(item) && item.chatId === current.chatId), current)
      : entries.find(item => valid(item) && item.chatId === current.chatId) || current
    if (foreignRecord && legacyRelation(foreign, current) === 'conflict') conversationChanged()
    // This also retains a partially committed *empty* draft, absent from the
    // index. If quota still prevents its record, keep its pointer and refuse.
    if (!foreignRecord || conversationSnapshot(foreignRecord) !== conversationSnapshot(foreign)) {
      localStorage.setItem(RECORD_PREFIX + foreign.chatId, JSON.stringify(foreign))
    }
    localStorage.setItem(LIBRARY_KEY, JSON.stringify(entries))
  }
  // Validate/read the library before either write. Commit the reload authority
  // first so a failed second write cannot restore stale text over newer text.
  localStorage.setItem(CHAT_KEY, JSON.stringify(value))
  // Advance as soon as the reload authority commits, even if the library
  // write fails afterward. A retry must recognize this editor's partial save.
  checkpoint?.committed(value)
  localStorage.setItem(RECORD_PREFIX + value.chatId, JSON.stringify(value))
  rememberActiveConversation(value.chatId)
  try {
    // Never silently evict an older conversation when browser storage fills up.
    localStorage.setItem(LIBRARY_KEY, JSON.stringify(next))
  } finally {
    window.dispatchEvent(new Event(LIBRARY_EVENT))
  }
  return value
}

export function conversationTitle(chat) {
  return conversationLabels(chat.chatId).title || chat.messages.find(message => message.role === 'user' && typeof message.content === 'string')?.content.trim().slice(0, 80) || chat.draft?.trim().slice(0, 80) || 'Untitled conversation'
}

export async function purgeConversationImages(chatId) {
  if(typeof chatId!=='string' || !/^[A-Za-z0-9_-]{1,128}$/.test(chatId) || chatId.length>128)throw new Error('Invalid conversation.')
  const chat=readConversations().find(item=>item.chatId===chatId)
  if(chat?.inFlight || chat?.interrupted || chat?.compactionRequestId)throw new Error('Finish, stop or recover this conversation before deleting it.')
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),30000)
  try {
    const response=await fetch(`/api/pixel/images/${encodeURIComponent(chatId)}`,{method:'DELETE',signal:controller.signal})
    let receipt;try {receipt=await response.json()}catch {throw new Error('Deletion is not confirmed. Your local history is preserved; retry deletion.')}
    if(!response.ok)throw new Error(typeof receipt?.detail==='string' && receipt.detail.length<300?receipt.detail:'Deletion is not confirmed. Your local history is preserved; retry deletion.')
    if(Object.keys(receipt||{}).sort().join()!=='deleted,schemaVersion' || receipt.schemaVersion!==1 || receipt.deleted!==true)throw new Error('Deletion is not confirmed. Your local history is preserved; retry deletion.')
  } catch(error) {
    if(error?.name==='AbortError')throw new Error('Deletion timed out. Your local history is preserved; retry deletion to confirm cleanup.')
    throw error
  } finally {clearTimeout(timer)}
}

export function deleteConversation(chatId) {
  const entries = loadConversations(true)
  const chat = entries.find(item => valid(item) && item.chatId === chatId)
  if (!chat) {
    // A previous attempt may have removed the chat before metadata cleanup
    // failed. Retain the tombstone and finish only that explicit deletion.
    if (deletedIds().includes(chatId)) {
      deleteConversationLabels(chatId)
      window.dispatchEvent(new Event(LIBRARY_EVENT))
    }
    return
  }
  if (chat.inFlight || chat.interrupted) throw new Error('Stop or resume this task before deleting its conversation.')
  if (chat.compactionRequestId) throw new Error('Check the pending context compaction before deleting this conversation.')
  // Write the deletion marker first: stale open tabs must never resurrect a deleted chat.
  localStorage.setItem(TOMBSTONE_PREFIX + chatId, '1')
  localStorage.setItem(DELETED_KEY, JSON.stringify([...new Set([...deletedIds(), chatId])]))
  localStorage.setItem(LIBRARY_KEY, JSON.stringify(entries.filter(item => !valid(item) || item.chatId !== chatId)))
  const current = currentConversation()
  if (current?.chatId === chatId) localStorage.removeItem(CHAT_KEY)
  localStorage.removeItem(RECORD_PREFIX + chatId)
  deleteConversationLabels(chatId)
  window.dispatchEvent(new Event(LIBRARY_EVENT))
}
