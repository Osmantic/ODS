import { renderHook, act } from '@testing-library/react'
import { usePwaInstallPrompt } from '../usePwaInstallPrompt'

// Helper: fire a synthetic beforeinstallprompt event that the hook listens for.
function fireBeforeInstall() {
  const event = new Event('beforeinstallprompt')
  // The real event has prompt() + userChoice + preventDefault. Stub them.
  event.preventDefault = vi.fn()
  event.prompt = vi.fn().mockResolvedValue()
  event.userChoice = Promise.resolve({ outcome: 'accepted' })
  window.dispatchEvent(event)
  return event
}

function fireAppInstalled() {
  window.dispatchEvent(new Event('appinstalled'))
}

describe('usePwaInstallPrompt', () => {
  beforeEach(() => {
    globalThis.localStorage.clear()
    globalThis.sessionStorage.clear()
    // Reset navigator UA detection (jsdom defaults are non-iOS, but be explicit)
    Object.defineProperty(window.navigator, 'userAgent', {
      value: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
      configurable: true,
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  // --------------------------------------------------------------------
  // Visit-count gating
  // --------------------------------------------------------------------

  test('does not show on first visit even after beforeinstallprompt fires', () => {
    const { result } = renderHook(() => usePwaInstallPrompt())
    act(() => { fireBeforeInstall() })
    // First mount bumps the visit count to 1; that's below the 3-visit
    // threshold so the banner stays hidden.
    expect(result.current.visitCount).toBe(1)
    expect(result.current.shouldShow).toBe(false)
  })

  test('shows on the 3rd visit', () => {
    // Simulate prior visits by pre-setting localStorage.
    globalThis.localStorage.setItem('ods-pwa-visit-count', '2')
    const { result } = renderHook(() => usePwaInstallPrompt())
    act(() => { fireBeforeInstall() })
    expect(result.current.visitCount).toBe(3)
    expect(result.current.shouldShow).toBe(true)
  })

  test('only ticks once per tab session', () => {
    globalThis.localStorage.setItem('ods-pwa-visit-count', '2')
    // First mount: increments to 3.
    const first = renderHook(() => usePwaInstallPrompt())
    expect(first.result.current.visitCount).toBe(3)
    // Second mount in the same tab session: sessionStorage guard kicks in.
    const second = renderHook(() => usePwaInstallPrompt())
    expect(second.result.current.visitCount).toBe(3)
  })

  // --------------------------------------------------------------------
  // Dismissal
  // --------------------------------------------------------------------

  test('dismiss() persists across reloads', () => {
    globalThis.localStorage.setItem('ods-pwa-visit-count', '5')
    const { result, unmount } = renderHook(() => usePwaInstallPrompt())
    act(() => { fireBeforeInstall() })
    expect(result.current.shouldShow).toBe(true)
    act(() => { result.current.dismiss() })
    expect(result.current.shouldShow).toBe(false)
    unmount()

    // Simulate a fresh tab: clear session, keep localStorage.
    globalThis.sessionStorage.clear()
    const { result: result2 } = renderHook(() => usePwaInstallPrompt())
    act(() => { fireBeforeInstall() })
    expect(result2.current.shouldShow).toBe(false)
  })

  // --------------------------------------------------------------------
  // Install lifecycle
  // --------------------------------------------------------------------

  test('promptInstall calls the browser-provided prompt event', async () => {
    globalThis.localStorage.setItem('ods-pwa-visit-count', '5')
    const { result } = renderHook(() => usePwaInstallPrompt())
    let event
    act(() => { event = fireBeforeInstall() })
    expect(result.current.shouldShow).toBe(true)

    await act(async () => {
      await result.current.promptInstall()
    })
    expect(event.prompt).toHaveBeenCalled()
  })

  test('appinstalled event marks the PWA installed and hides the banner', () => {
    globalThis.localStorage.setItem('ods-pwa-visit-count', '5')
    const { result } = renderHook(() => usePwaInstallPrompt())
    act(() => { fireBeforeInstall() })
    expect(result.current.shouldShow).toBe(true)

    act(() => { fireAppInstalled() })
    expect(result.current.installed).toBe(true)
    expect(result.current.shouldShow).toBe(false)
    expect(globalThis.localStorage.getItem('ods-pwa-installed')).toBe('1')
  })

  test('does not show when already installed (display-mode: standalone)', () => {
    // Trick the hook into thinking the PWA is already running standalone.
    const origMatchMedia = window.matchMedia
    window.matchMedia = vi.fn().mockReturnValue({ matches: true })
    try {
      globalThis.localStorage.setItem('ods-pwa-visit-count', '5')
      const { result } = renderHook(() => usePwaInstallPrompt())
      act(() => { fireBeforeInstall() })
      expect(result.current.installed).toBe(true)
      expect(result.current.shouldShow).toBe(false)
    } finally {
      window.matchMedia = origMatchMedia
    }
  })

  // --------------------------------------------------------------------
  // iOS path (no beforeinstallprompt event)
  // --------------------------------------------------------------------

  test('iOS shows the banner without beforeinstallprompt firing', () => {
    Object.defineProperty(window.navigator, 'userAgent', {
      value: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15',
      configurable: true,
    })
    globalThis.localStorage.setItem('ods-pwa-visit-count', '5')
    const { result } = renderHook(() => usePwaInstallPrompt())
    expect(result.current.isIos).toBe(true)
    expect(result.current.shouldShow).toBe(true)
  })

  test('iOS promptInstall is a no-op (Safari refuses programmatic install)', async () => {
    Object.defineProperty(window.navigator, 'userAgent', {
      value: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15',
      configurable: true,
    })
    globalThis.localStorage.setItem('ods-pwa-visit-count', '5')
    const { result } = renderHook(() => usePwaInstallPrompt())
    let outcome
    await act(async () => {
      outcome = await result.current.promptInstall()
    })
    expect(outcome.platform).toBe('ios')
  })
})
