import '@testing-library/jest-dom'
import { beforeEach, afterEach } from 'vitest'
import { installFakeLocks } from './webLocks'

let testLocks
beforeEach(() => {
  if (typeof globalThis.navigator === 'undefined') return
  testLocks = installFakeLocks()
})
afterEach(() => {
  testLocks?.restore()
  testLocks = undefined
})

// Provide a working localStorage for jsdom environments that lack one
if (typeof globalThis.localStorage === 'undefined' || typeof globalThis.localStorage.getItem !== 'function') {
  const store = {}
  globalThis.localStorage = {
    getItem: (key) => store[key] ?? null,
    setItem: (key, val) => { store[key] = String(val) },
    removeItem: (key) => { delete store[key] },
    clear: () => { Object.keys(store).forEach(k => delete store[k]) },
    get length() { return Object.keys(store).length },
    key: (i) => Object.keys(store)[i] ?? null,
  }
}
