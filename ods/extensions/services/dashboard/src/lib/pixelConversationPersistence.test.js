import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  createConversationPersistence,
} from './pixelConversationPersistence'
import {
  CHAT_KEY,
  readConversations,
  createConversationWriter,
} from './pixelConversations'
import { createConversationOwnership } from './pixelConversationOwnership'

const LIBRARY_KEY = 'ods.pixel.conversations.v1'
const DELETED_KEY = 'ods.pixel.deleted-conversations.v1'
const OWNERSHIP = 'web-lock-v1'

// Native-like lock pool: the callback's returned promise holds the lock.
// Cleanup (held.delete + releaseFn) happens on the microtask after the
// callback's returned promise settles.
function makeLockPool() {
  const held = new Map()
  return {
    request(name, options, cb) {
      if (held.has(name)) {
        return Promise.resolve(cb(null))
      }
      held.set(name, true)
      let releaseFn
      const release = new Promise(res => { releaseFn = res })
      const result = cb({ name, mode: options.mode })
      const done = Promise.resolve(result).then(() => {
        held.delete(name)
        releaseFn()
      })
      return done
    },
    _isHeld(name) {
      return held.has(name)
    },
  }
}

// Deferred lock pool: the grant callback is not invoked until the test
// explicitly flushes it. Used to observe state while a claim is pending.
function makeDeferredLockPool() {
  const held = new Map()
  const pending = []
  return {
    request(name, options, cb) {
      if (held.has(name)) {
        return Promise.resolve(cb(null))
      }
      held.set(name, true)
      let releaseFn
      const release = new Promise(res => { releaseFn = res })
      let grantCb = null
      const grant = () => {
        if (!grantCb) return
        const result = grantCb({ name, mode: options.mode })
        Promise.resolve(result).then(() => {
          held.delete(name)
          releaseFn()
        })
      }
      pending.push({ name, grant })
      grantCb = cb
      return release
    },
    _grantAll() {
      const list = pending.splice(0)
      for (const p of list) p.grant()
    },
    _isHeld(name) {
      return held.has(name)
    },
  }
}

function makeRecord(chatId, overrides = {}) {
  return {
    schema: 1,
    chatId,
    messages: [{ role: 'user', content: 'hello' }],
    updatedAt: Date.now(),
    ...overrides,
  }
}

function seedLibrary(records) {
  localStorage.setItem(LIBRARY_KEY, JSON.stringify(records))
}

function seedActive(record) {
  localStorage.setItem(CHAT_KEY, JSON.stringify(record))
}

function readActive() {
  return JSON.parse(localStorage.getItem(CHAT_KEY) || 'null')
}

let controllers = []

function makeController(opts = {}) {
  const locks = opts.locks || makeLockPool()
  const ownership = createConversationOwnership(locks)
  const controller = createConversationPersistence({ ownership, ...opts })
  controllers.push(controller)
  return { controller, locks, ownership }
}

// Flush microtasks so native-fake lock cleanup runs after dispose/release.
const flush = () => new Promise(res => setTimeout(res, 0))

beforeEach(() => {
  localStorage.clear()
  controllers = []
})

afterEach(() => {
  for (const c of controllers) {
    try { c.dispose() } catch { /* ignore */ }
  }
  controllers = []
})

describe('pixelConversationPersistence', () => {
  it('bind rejects invalid chat ids', () => {
    const { controller } = makeController()
    expect(controller.bind('bad id!', null)).toBe(false)
    expect(controller.bind('', null)).toBe(false)
    expect(controller.bind('ok-id_1', null)).toBe(true)
  })

  it('bind rejects mismatched raw chatId', () => {
    const { controller } = makeController()
    const raw = makeRecord('chat-a')
    expect(controller.bind('chat-b', raw)).toBe(false)
  })

  it('author acquires lock and commits stamped record', async () => {
    const { controller } = makeController()
    const raw = makeRecord('chat-a')
    seedLibrary([raw])
    seedActive(raw)
    expect(controller.bind('chat-a', raw)).toBe(true)
    const ok = await controller.acquireAuthor()
    expect(ok).toBe(true)
    expect(controller.owns()).toBe(true)
    const result = controller.commit({ ...raw, messages: [{ role: 'user', content: 'edited' }] })
    // saveConversation returns undefined intentionally; inspect persisted state.
    expect(result).toBeUndefined()
    const stored = readActive()
    expect(stored.persistenceOwnership).toBe(OWNERSHIP)
    expect(stored.messages[0].content).toBe('edited')
    const lib = readConversations()
    expect(lib.find(c => c.chatId === 'chat-a').messages[0].content).toBe('edited')
  })

  it('commit without ownership throws conversation-owned-elsewhere', () => {
    const { controller } = makeController()
    const raw = makeRecord('chat-b')
    seedLibrary([raw])
    seedActive(raw)
    controller.bind('chat-b', raw)
    let error
    try { controller.commit(raw) } catch (e) { error = e }
    expect(error).toBeTruthy()
    expect(error.code).toBe('conversation-owned-elsewhere')
  })

  it('commit with mismatched chatId throws', async () => {
    const { controller } = makeController()
    const raw = makeRecord('chat-c')
    seedLibrary([raw])
    seedActive(raw)
    controller.bind('chat-c', raw)
    await controller.acquireAuthor()
    let error
    try { controller.commit(makeRecord('other')) } catch (e) { error = e }
    expect(error.code).toBe('conversation-owned-elsewhere')
  })

  it('legacy passive returns legacy without requesting lock and leaves raw bytes unchanged', async () => {
    const locks = makeLockPool()
    const spy = vi.spyOn(locks, 'request')
    const { controller } = makeController({ locks })
    const legacy = makeRecord('chat-legacy')
    seedLibrary([legacy])
    seedActive(legacy)
    const beforeChat = localStorage.getItem(CHAT_KEY)
    const beforeLib = localStorage.getItem(LIBRARY_KEY)
    controller.bind('chat-legacy', legacy)
    const result = await controller.acquirePassive()
    expect(result).toEqual({ owned: false, reason: 'legacy' })
    expect(spy).not.toHaveBeenCalled()
    expect(localStorage.getItem(CHAT_KEY)).toBe(beforeChat)
    expect(localStorage.getItem(LIBRARY_KEY)).toBe(beforeLib)
  })

  it('legacy author claim can still save', async () => {
    const { controller } = makeController()
    const legacy = makeRecord('chat-legacy-author')
    seedLibrary([legacy])
    seedActive(legacy)
    controller.bind('chat-legacy-author', legacy)
    const ok = await controller.acquireAuthor()
    expect(ok).toBe(true)
    controller.commit({ ...legacy, messages: [{ role: 'user', content: 'saved' }] })
    expect(readActive().persistenceOwnership).toBe(OWNERSHIP)
  })

  it('newchat with null raw allows passive acquisition', async () => {
    const { controller } = makeController()
    controller.bind('chat-new', null)
    const result = await controller.acquirePassive()
    expect(result.owned).toBe(true)
    expect(result.rebase).toBe(null)
  })

  it('passive rebases to latest raw when not dirty', async () => {
    const { controller } = makeController()
    const raw = makeRecord('chat-rebase', { persistenceOwnership: OWNERSHIP })
    seedLibrary([raw])
    seedActive(raw)
    controller.bind('chat-rebase', raw)
    const result = await controller.acquirePassive()
    expect(result.owned).toBe(true)
    expect(result.rebase).toEqual(raw)
  })

  it('repeated passive acquire does not rebase again', async () => {
    const { controller } = makeController()
    const raw = makeRecord('chat-rebase2', { persistenceOwnership: OWNERSHIP })
    seedLibrary([raw])
    seedActive(raw)
    controller.bind('chat-rebase2', raw)
    const first = await controller.acquirePassive()
    expect(first.owned).toBe(true)
    expect(first.rebase).toEqual(raw)
    const second = await controller.acquirePassive()
    expect(second.owned).toBe(true)
    expect(second.rebase).toBe(null)
  })

  it('stale writer rejects after takeover with conversation-changed', async () => {
    const locks = makeLockPool()
    const { controller: c1 } = makeController({ locks })
    const raw = makeRecord('chat-dirty', { persistenceOwnership: OWNERSHIP })
    seedLibrary([raw])
    seedActive(raw)
    c1.bind('chat-dirty', raw)
    await c1.acquireAuthor()
    c1.commit({ ...raw, messages: [{ role: 'user', content: 'first' }] })
    c1.dispose()
    await flush()

    const { controller: c2 } = makeController({ locks })
    const latest = readActive()
    c2.bind('chat-dirty', latest)
    const passive = await c2.acquirePassive()
    expect(passive.owned).toBe(true)
    c2.commit({ ...latest, messages: [{ role: 'user', content: 'second' }] })
    // Release c2 and let native-fake cleanup run before c3 claims.
    c2.dispose()
    await flush()

    const { controller: c3 } = makeController({ locks })
    c3.bind('chat-dirty', raw)
    const ok = await c3.acquireAuthor()
    expect(ok).toBe(true)
    let error
    try { c3.commit({ ...raw, messages: [{ role: 'user', content: 'stale' }] }) } catch (e) { error = e }
    expect(error).toBeTruthy()
    expect(error.code).toBe('conversation-changed')
    // Stale typed payload preserved in storage.
    expect(readActive().messages[0].content).toBe('second')
  })

  it('deleted existing record refuses passive takeover', async () => {
    const { controller } = makeController()
    const raw = makeRecord('chat-deleted', { persistenceOwnership: OWNERSHIP })
    seedLibrary([raw])
    seedActive(raw)
    localStorage.setItem(DELETED_KEY, JSON.stringify(['chat-deleted']))
    controller.bind('chat-deleted', raw)
    const result = await controller.acquirePassive()
    expect(result.owned).toBe(false)
    expect(result.reason).toBe('deleted')
  })

  it('missing existing record refuses passive takeover', async () => {
    const { controller } = makeController()
    const raw = makeRecord('chat-missing', { persistenceOwnership: OWNERSHIP })
    seedLibrary([raw])
    seedActive(raw)
    controller.bind('chat-missing', raw)
    localStorage.removeItem(LIBRARY_KEY)
    localStorage.removeItem(CHAT_KEY)
    const result = await controller.acquirePassive()
    expect(result.owned).toBe(false)
    expect(result.reason).toBe('missing')
  })

  it('deferred grant: author action while pending sets dirty and strict commit rejects stale', async () => {
    const locks = makeDeferredLockPool()
    const { controller } = makeController({ locks })
    const raw = makeRecord('chat-defer', { persistenceOwnership: OWNERSHIP })
    seedLibrary([raw])
    seedActive(raw)
    controller.bind('chat-defer', raw)

    // Start passive claim; grant callback is deferred.
    const passivePromise = controller.acquirePassive()
    // Author action while pending: acquireAuthor sets dirty synchronously.
    const authorPromise = controller.acquireAuthor()

    // Updated storage raw while pending.
    const updated = { ...raw, messages: [{ role: 'user', content: 'updated-while-pending' }] }
    seedActive(updated)
    seedLibrary([updated])

    // Now grant the deferred lock callback.
    locks._grantAll()

    const [passive, author] = await Promise.all([passivePromise, authorPromise])
    expect(author).toBe(true)
    expect(passive.owned).toBe(true)
    // Passive must NOT rebase because dirty was set synchronously.
    expect(passive.rebase).toBe(null)

    // Strict commit with stale typed payload must reject.
    let error
    try { controller.commit({ ...raw, messages: [{ role: 'user', content: 'stale' }] }) } catch (e) { error = e }
    expect(error).toBeTruthy()
    expect(error.code).toBe('conversation-changed')
  })

  it('two simultaneous controllers: follower denied passive, author final commit succeeds', async () => {
    const locks = makeLockPool()
    const { controller: author } = makeController({ locks })
    const raw = makeRecord('chat-simul', {
      persistenceOwnership: OWNERSHIP,
      inFlight: true,
      requestId: 'r1',
    })
    seedLibrary([raw])
    seedActive(raw)
    author.bind('chat-simul', raw)
    const ok = await author.acquireAuthor()
    expect(ok).toBe(true)

    // Mounted follower reads same raw, attempts passive claim -> denied.
    const { controller: follower } = makeController({ locks })
    follower.bind('chat-simul', raw)
    const passive = await follower.acquirePassive()
    expect(passive.owned).toBe(false)
    expect(passive.reason).toBe('locked')

    // Follower attempts normalization commit -> rejected (no ownership).
    const beforeChat = localStorage.getItem(CHAT_KEY)
    const beforeLib = localStorage.getItem(LIBRARY_KEY)
    let error
    try {
      follower.commit({ ...raw, inFlight: false, requestId: null, messages: [{ role: 'user', content: 'normalized' }] })
    } catch (e) { error = e }
    expect(error).toBeTruthy()
    expect(error.code).toBe('conversation-owned-elsewhere')
    expect(localStorage.getItem(CHAT_KEY)).toBe(beforeChat)
    expect(localStorage.getItem(LIBRARY_KEY)).toBe(beforeLib)

    // Author final terminal commit STILL succeeds.
    author.commit({
      ...raw,
      inFlight: false,
      requestId: null,
      messages: [{ role: 'user', content: 'final' }],
    })
    expect(readActive().messages[0].content).toBe('final')
    expect(readActive().inFlight).toBe(false)
  })

  it('after owner dispose and cleanup, follower rebases to latest and commits recovered snapshot', async () => {
    const locks = makeLockPool()
    const { controller: author } = makeController({ locks })
    const raw = makeRecord('chat-recover', { persistenceOwnership: OWNERSHIP })
    seedLibrary([raw])
    seedActive(raw)
    author.bind('chat-recover', raw)
    await author.acquireAuthor()
    author.commit({ ...raw, messages: [{ role: 'user', content: 'final' }] })
    const finalRaw = readActive()
    author.dispose()
    await flush()

    const { controller: follower } = makeController({ locks })
    follower.bind('chat-recover', raw)
    const passive = await follower.acquirePassive()
    expect(passive.owned).toBe(true)
    expect(passive.rebase).toEqual(finalRaw)
    follower.commit({ ...passive.rebase, messages: [{ role: 'user', content: 'recovered' }] })
    expect(readActive().messages[0].content).toBe('recovered')
  })

  it('bind stale generation does not write new chat', async () => {
    const { controller } = makeController()
    const rawA = makeRecord('chat-gen-a', { persistenceOwnership: OWNERSHIP })
    const rawB = makeRecord('chat-gen-b', { persistenceOwnership: OWNERSHIP })
    seedLibrary([rawA, rawB])
    seedActive(rawA)
    controller.bind('chat-gen-a', rawA)
    await controller.acquireAuthor()
    controller.bind('chat-gen-b', rawB)
    let error
    try { controller.commit({ ...rawA, messages: [{ role: 'user', content: 'stale' }] }) } catch (e) { error = e }
    expect(error).toBeTruthy()
    expect(error.code).toBe('conversation-owned-elsewhere')
  })

  it('unsupported lock manager fails closed', async () => {
    const ownership = createConversationOwnership(null)
    const controller = createConversationPersistence({ ownership })
    controllers.push(controller)
    const raw = makeRecord('chat-nolock', { persistenceOwnership: OWNERSHIP })
    seedLibrary([raw])
    seedActive(raw)
    controller.bind('chat-nolock', raw)
    const result = await controller.acquirePassive()
    expect(result.owned).toBe(false)
    expect(result.reason).toBe('unavailable')
  })

  it('dispose releases ownership and blocks further commits', async () => {
    const { controller } = makeController()
    const raw = makeRecord('chat-dispose', { persistenceOwnership: OWNERSHIP })
    seedLibrary([raw])
    seedActive(raw)
    controller.bind('chat-dispose', raw)
    await controller.acquireAuthor()
    controller.dispose()
    expect(controller.owns()).toBe(false)
    let error
    try { controller.commit(raw) } catch (e) { error = e }
    expect(error.code).toBe('conversation-owned-elsewhere')
  })

  it('newchat durable draft with marker commits', async () => {
    const { controller } = makeController()
    controller.bind('chat-draft', null)
    await controller.acquireAuthor()
    const draft = {
      schema: 1,
      chatId: 'chat-draft',
      messages: [],
      draft: 'unsent text',
    }
    controller.commit(draft)
    const stored = readActive()
    expect(stored.persistenceOwnership).toBe(OWNERSHIP)
    expect(stored.draft).toBe('unsent text')
  })

  it('readLatest throwing fails closed without writing', async () => {
    const locks = makeLockPool()
    const ownership = createConversationOwnership(locks)
    const controller = createConversationPersistence({
      ownership,
      readLatest: () => { throw new Error('boom') },
    })
    controllers.push(controller)
    const raw = makeRecord('chat-throw', { persistenceOwnership: OWNERSHIP })
    seedLibrary([raw])
    seedActive(raw)
    controller.bind('chat-throw', raw)
    const result = await controller.acquirePassive()
    expect(result.owned).toBe(false)
    expect(result.reason).toBe('unavailable')
    expect(readActive()).toEqual(raw)
  })

  it('both chat key and library bytes identical after rejected commit', async () => {
    const locks = makeLockPool()
    const { controller: c1 } = makeController({ locks })
    const raw = makeRecord('chat-bytes', { persistenceOwnership: OWNERSHIP })
    seedLibrary([raw])
    seedActive(raw)
    c1.bind('chat-bytes', raw)
    await c1.acquireAuthor()
    c1.commit({ ...raw, messages: [{ role: 'user', content: 'first' }] })
    c1.dispose()
    await flush()

    const { controller: c2 } = makeController({ locks })
    const latest = readActive()
    c2.bind('chat-bytes', latest)
    await c2.acquirePassive()
    c2.commit({ ...latest, messages: [{ role: 'user', content: 'second' }] })
    c2.dispose()
    await flush()

    const beforeChat = localStorage.getItem(CHAT_KEY)
    const beforeLib = localStorage.getItem(LIBRARY_KEY)

    const { controller: c3 } = makeController({ locks })
    c3.bind('chat-bytes', raw)
    const ok = await c3.acquireAuthor()
    expect(ok).toBe(true)
    let error
    try { c3.commit({ ...raw, messages: [{ role: 'user', content: 'stale' }] }) } catch (e) { error = e }
    expect(error.code).toBe('conversation-changed')
    expect(localStorage.getItem(CHAT_KEY)).toBe(beforeChat)
    expect(localStorage.getItem(LIBRARY_KEY)).toBe(beforeLib)
  })

  it('readConversations returns [] on unreadable library', () => {
    localStorage.setItem(LIBRARY_KEY, 'not-json')
    expect(readConversations()).toEqual([])
  })

  it('createConversationWriter rejects stale commit with conversation-changed', () => {
    const raw = makeRecord('chat-writer', { persistenceOwnership: OWNERSHIP })
    seedLibrary([raw])
    seedActive(raw)
    const writer = createConversationWriter(raw)
    // Simulate another tab advancing the record.
    const advanced = { ...raw, messages: [{ role: 'user', content: 'advanced' }] }
    seedActive(advanced)
    seedLibrary([advanced])
    let error
    try { writer({ ...raw, messages: [{ role: 'user', content: 'stale' }] }) } catch (e) { error = e }
    expect(error).toBeTruthy()
    expect(error.code).toBe('conversation-changed')
  })
})
