import { useCallback, useEffect, useRef, useState } from 'react'

// Smart PWA install prompt for the dashboard.
//
// What the browser gives us:
//   * Chrome/Edge/Android Chrome fire `beforeinstallprompt` when the
//     PWA criteria (manifest + SW + HTTPS / localhost) are met.
//   * Firefox + iOS Safari do NOT fire this event. iOS users have to
//     manually use Share → Add to Home Screen. We can't programmatically
//     trigger that, but we can still show our nudge UI with
//     iOS-specific copy.
//   * Once accepted/dismissed, the event can't be reused — the browser
//     will mint a new one if/when criteria are met again.
//
// What we add on top:
//   * Don't pester on first visit. Wait until the user has come back
//     N times (default 3) — that's a real signal they value this UI.
//   * Remember "user said not now" forever (until they clear storage).
//     A "remind me later" path is implicit in the visit-counter pattern
//     and doesn't need separate state.
//   * Detect already-installed PWAs and stay out of their way.
//
// Visit counting: we increment on each TAB SESSION (sessionStorage
// guard), not on each F5. The first session bumps to 1, the third
// session bumps to 3 — that's when the banner can show.

const VISIT_COUNT_KEY = 'ods-pwa-visit-count'
const SESSION_TICKED_KEY = 'ods-pwa-session-ticked'
const DISMISSED_KEY = 'ods-pwa-prompt-dismissed'
const INSTALLED_KEY = 'ods-pwa-installed'
const MIN_VISITS_BEFORE_PROMPT = 3

function safeGet(storage, key, fallback = null) {
  try { return storage?.getItem(key) ?? fallback } catch { return fallback }
}
function safeSet(storage, key, value) {
  try { storage?.setItem(key, value) } catch { /* private mode / quota */ }
}

function isAlreadyInstalled() {
  // `display-mode: standalone` is true once the user has launched the
  // installed PWA. window.matchMedia is the cross-browser way to read it.
  if (typeof window === 'undefined') return false
  try {
    if (window.matchMedia?.('(display-mode: standalone)').matches) return true
    // iOS exposes this on navigator instead — Safari-only quirk.
    if (window.navigator?.standalone === true) return true
  } catch {
    // matchMedia failures shouldn't crash the hook.
  }
  return safeGet(globalThis.localStorage, INSTALLED_KEY) === '1'
}

function isIos() {
  if (typeof navigator === 'undefined') return false
  // Old iPhone / iPad UAs + iPadOS-which-claims-to-be-macOS-but-is-touch.
  const ua = navigator.userAgent || ''
  if (/iPad|iPhone|iPod/.test(ua)) return true
  return /Macintosh/.test(ua) && navigator.maxTouchPoints > 1
}

export function usePwaInstallPrompt() {
  const promptEventRef = useRef(null)
  // installable: browser fired beforeinstallprompt OR we detected iOS
  // (where the install path is manual but the banner is still useful).
  const [installable, setInstallable] = useState(false)
  const [installed, setInstalled] = useState(() => isAlreadyInstalled())
  const [visitCount, setVisitCount] = useState(() => {
    const raw = safeGet(globalThis.localStorage, VISIT_COUNT_KEY, '0')
    return Number.parseInt(raw, 10) || 0
  })
  const [dismissed, setDismissed] = useState(
    () => safeGet(globalThis.localStorage, DISMISSED_KEY) === '1'
  )
  const [ios] = useState(() => isIos())

  // Tick the visit counter ONCE per tab session. F5 / hot reload won't
  // re-tick because sessionStorage persists across reloads within the
  // same tab.
  useEffect(() => {
    if (installed) return
    if (safeGet(globalThis.sessionStorage, SESSION_TICKED_KEY) === '1') return
    safeSet(globalThis.sessionStorage, SESSION_TICKED_KEY, '1')
    setVisitCount(prev => {
      const next = prev + 1
      safeSet(globalThis.localStorage, VISIT_COUNT_KEY, String(next))
      return next
    })
  }, [installed])

  // Capture the browser-provided install event for non-iOS browsers.
  useEffect(() => {
    if (installed) return
    const onBeforeInstall = (event) => {
      event.preventDefault()
      promptEventRef.current = event
      setInstallable(true)
    }
    const onInstalled = () => {
      promptEventRef.current = null
      setInstallable(false)
      setInstalled(true)
      safeSet(globalThis.localStorage, INSTALLED_KEY, '1')
    }
    window.addEventListener('beforeinstallprompt', onBeforeInstall)
    window.addEventListener('appinstalled', onInstalled)
    return () => {
      window.removeEventListener('beforeinstallprompt', onBeforeInstall)
      window.removeEventListener('appinstalled', onInstalled)
    }
  }, [installed])

  // iOS doesn't fire beforeinstallprompt, but we still want to show a
  // hint. The banner copy on iOS becomes "Share → Add to Home Screen"
  // because we can't trigger the install programmatically.
  useEffect(() => {
    if (ios && !installed) setInstallable(true)
  }, [ios, installed])

  // Promptable when the user has shown intent (3+ visits) and we either
  // have a real event or are on iOS.
  const shouldShow =
    !installed &&
    !dismissed &&
    installable &&
    visitCount >= MIN_VISITS_BEFORE_PROMPT

  const promptInstall = useCallback(async () => {
    const event = promptEventRef.current
    if (!event) {
      // iOS path — no programmatic prompt. The banner stays open until
      // the user taps Dismiss; calling promptInstall is a no-op here
      // and the banner's iOS-specific copy explains what to do.
      return { platform: 'ios', outcome: 'manual' }
    }
    try {
      await event.prompt()
      const choice = await event.userChoice
      promptEventRef.current = null
      setInstallable(false)
      if (choice?.outcome === 'accepted') {
        setInstalled(true)
        safeSet(globalThis.localStorage, INSTALLED_KEY, '1')
      } else {
        // User chose "not now" in the OS dialog — same as our dismiss.
        setDismissed(true)
        safeSet(globalThis.localStorage, DISMISSED_KEY, '1')
      }
      return choice
    } catch {
      // The browser sometimes throws if prompt() is called twice. Treat
      // as "not installable any more."
      promptEventRef.current = null
      setInstallable(false)
      return { outcome: 'error' }
    }
  }, [])

  const dismiss = useCallback(() => {
    setDismissed(true)
    safeSet(globalThis.localStorage, DISMISSED_KEY, '1')
  }, [])

  return {
    shouldShow,
    isIos: ios,
    installed,
    visitCount,
    promptInstall,
    dismiss,
  }
}
