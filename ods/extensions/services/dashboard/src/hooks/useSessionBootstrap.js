import { useEffect } from 'react'
import { refreshOdsSession } from '../lib/odsSession'

// Well inside the 12-hour session lifetime and the one-hour renewal window
// in lib/odsSession.js, so an open dashboard never lets the cookie lapse.
const SESSION_CHECK_INTERVAL_MS = 15 * 60 * 1000

/**
 * Keep the dashboard user's `ods-session` cookie valid.
 *
 * Why this exists
 * ---------------
 * Cookie-gated services (hermes-proxy today, more later) `forward_auth`
 * against `/api/auth/verify-session` or sign in at `/auth/ods`, and refuse
 * anyone without a valid ods-session cookie. Without this
 * bootstrap the install owner would have to mint + redeem a magic
 * link to themselves before they could reach their own services —
 * absurd UX.
 *
 * The dashboard already authenticates to dashboard-api with the admin
 * API key (nginx injects `Authorization: Bearer ${DASHBOARD_API_KEY}`
 * via proxy_set_header). refreshOdsSession():
 *
 *   1. GET /api/auth/verify-session
 *   2. If 200 with more than an hour left — nothing to do.
 *   3. Otherwise POST /api/auth/admin-session, which mints a signed
 *      cookie (same shape magic-link redemption issues) and the
 *      browser keeps it for 12 hours.
 *
 * A dashboard tab can stay open for days, so the check runs on mount, every
 * SESSION_CHECK_INTERVAL_MS, and whenever the tab regains focus or becomes
 * visible. Launch links to cookie-gated services check again on click
 * (openWithFreshSession in lib/odsSession.js).
 *
 * From the user's POV: open dashboard → sidebar's Hermes link just
 * works, however long the dashboard has been open.
 *
 * Runs per enabled route lifetime, including a fresh verification after
 * returning from the public Talk route. Leaving that lifetime cancels browser
 * work; an already accepted mint remains owned by the server. If session_signer isn't configured server-side the
 * admin-session POST 503s; we surface that in console as a hint to
 * set ODS_SESSION_SECRET, but don't block the dashboard.
 */
export function useSessionBootstrap(enabled = true) {
  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    // Focus and visibility often fire together; one check at a time.
    let pending = null
    const check = () => {
      if (!pending) pending = refreshOdsSession(controller.signal).finally(() => { pending = null })
    }
    const checkWhenVisible = () => {
      if (document.visibilityState === 'visible') check()
    }

    check()
    const timer = setInterval(check, SESSION_CHECK_INTERVAL_MS)
    window.addEventListener('focus', check)
    document.addEventListener('visibilitychange', checkWhenVisible)
    return () => {
      controller.abort()
      clearInterval(timer)
      window.removeEventListener('focus', check)
      document.removeEventListener('visibilitychange', checkWhenVisible)
    }
  }, [enabled])
}
