import { describe, test, expect, beforeEach } from 'vitest'
import { readProgress, writeProgress, clearProgress } from './FirstBoot'

const PROGRESS_KEY = 'ods-firstboot-progress'

describe('FirstBoot progress persistence', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  test('readProgress returns null when nothing is stored', () => {
    expect(readProgress()).toBeNull()
  })

  test('writeProgress then readProgress round-trips the wizard state', () => {
    const progress = { step: 3, deviceName: 'ods', username: 'owner', stack: 'chat-agents' }
    writeProgress(progress)
    expect(readProgress()).toEqual(progress)
    expect(window.localStorage.getItem(PROGRESS_KEY)).toBe(JSON.stringify(progress))
  })

  test('clearProgress removes the stored state', () => {
    writeProgress({ step: 2 })
    clearProgress()
    expect(readProgress()).toBeNull()
    expect(window.localStorage.getItem(PROGRESS_KEY)).toBeNull()
  })

  test('readProgress returns null (not throw) for malformed JSON in storage', () => {
    window.localStorage.setItem(PROGRESS_KEY, '{not valid json')
    expect(() => readProgress()).not.toThrow()
    expect(readProgress()).toBeNull()
  })

  test('writeProgress does not throw when localStorage.setItem fails', () => {
    const original = window.localStorage.setItem
    window.localStorage.setItem = () => { throw new Error('quota exceeded') }
    try {
      expect(() => writeProgress({ step: 1 })).not.toThrow()
    } finally {
      window.localStorage.setItem = original
    }
  })

  test('clearProgress does not throw when localStorage.removeItem fails', () => {
    const original = window.localStorage.removeItem
    window.localStorage.removeItem = () => { throw new Error('blocked') }
    try {
      expect(() => clearProgress()).not.toThrow()
    } finally {
      window.localStorage.removeItem = original
    }
  })
})
