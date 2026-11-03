import '@testing-library/jest-dom'
import {beforeEach} from 'vitest'

// Each test starts a fresh tab; remounts inside a test retain tab selection.
beforeEach(() => globalThis.sessionStorage?.clear())

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
