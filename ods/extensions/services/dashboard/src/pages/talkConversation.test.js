import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { claimConversation, conversationHeaders, establishConversation } from './talkConversation'

let held
let claims
beforeEach(() => {
  held = new Set()
  claims = []
  const data = new Map()
  vi.stubGlobal('sessionStorage', {
    getItem: key => data.get(key) || null,
    setItem: (key, value) => data.set(key, value),
    removeItem: key => data.delete(key),
  })
  vi.stubGlobal('navigator', { locks: { request: async (key, options, callback) => {
    if (held.has(key)) return callback(null)
    held.add(key)
    try { return await callback({ name: key }) } finally { held.delete(key) }
  } } })
})
afterEach(async () => {
  claims.forEach(c => c.release())
  await Promise.resolve()
  vi.unstubAllGlobals()
})
it('retains identity on reload and puts the same binding on every request', async () => {
  const first = await claimConversation(); claims.push(first)
  establishConversation(first)
  const headers = conversationHeaders(first)
  first.release(); await Promise.resolve()
  const reloaded = await claimConversation(); claims.push(reloaded)
  expect(reloaded.identity.established).toBe(true)
  expect(conversationHeaders(reloaded)).toEqual(headers)
})
it('gives a duplicated tab a distinct handle and tab secret', async () => {
  const first = await claimConversation(); claims.push(first)
  establishConversation(first)
  const duplicate = await claimConversation(); claims.push(duplicate)
  expect(duplicate.identity.handle).not.toBe(first.identity.handle)
  expect(duplicate.identity.tab).not.toBe(first.identity.tab)
  expect(duplicate.identity.established).toBe(false)
})
it('refuses unavailable locking instead of sharing a copied tab identity', async () => {
  vi.stubGlobal('navigator', {})
  await expect(claimConversation()).rejects.toThrow('Web Locks')
})
