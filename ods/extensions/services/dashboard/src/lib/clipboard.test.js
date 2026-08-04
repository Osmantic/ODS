import { afterEach, describe, expect, it, vi } from 'vitest'
import { copyText } from './clipboard'

const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
const originalExecCommand = document.execCommand

afterEach(() => {
  if (originalClipboard) {
    Object.defineProperty(navigator, 'clipboard', originalClipboard)
  } else {
    delete navigator.clipboard
  }
  document.execCommand = originalExecCommand
  vi.restoreAllMocks()
})

function setClipboard(value) {
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value,
  })
}

describe('copyText', () => {
  it('uses the modern Clipboard API when available', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    setClipboard({ writeText })

    await expect(copyText('hello')).resolves.toBe(true)

    expect(writeText).toHaveBeenCalledWith('hello')
    expect(document.querySelector('textarea')).toBeNull()
  })

  it('falls back to a temporary textarea when Clipboard API is unavailable', async () => {
    setClipboard(undefined)
    document.execCommand = vi.fn().mockReturnValue(true)

    await expect(copyText('local ODS URL')).resolves.toBe(true)

    expect(document.execCommand).toHaveBeenCalledWith('copy')
    expect(document.querySelector('textarea')).toBeNull()
  })

  it('uses the fallback when Clipboard API rejects the request', async () => {
    setClipboard({ writeText: vi.fn().mockRejectedValue(new Error('insecure context')) })
    document.execCommand = vi.fn().mockReturnValue(true)
    vi.spyOn(console, 'debug').mockImplementation(() => {})

    await expect(copyText('http://ods.local')).resolves.toBe(true)

    expect(document.execCommand).toHaveBeenCalledWith('copy')
  })

  it('reports failure when neither copy mechanism succeeds', async () => {
    setClipboard(undefined)
    document.execCommand = vi.fn().mockReturnValue(false)

    await expect(copyText('uncopied')).resolves.toBe(false)
  })

  it('cleans up and reports failure when the fallback throws', async () => {
    setClipboard(undefined)
    document.execCommand = vi.fn(() => { throw new Error('copy denied') })
    vi.spyOn(console, 'debug').mockImplementation(() => {})

    await expect(copyText('uncopied')).resolves.toBe(false)

    expect(document.querySelector('textarea')).toBeNull()
  })
})
