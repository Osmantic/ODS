import { useEffect } from 'react'

/**
 * Ensure the dashboard user has a valid `ods-session` cookie.
 *
 * Why this exists
 * ---------------
 * Cookie-gated services (hermes-proxy today, more later) `forward_auth`
 * against `/api/auth/verify-session` and bounce anyone without a valid
 * ods-session cookie to the owner-card help page. Without this
 * bootstrap the install owner would have to mint + redeem a magic
 * link to themselves before they could reach their own services —
 * absurd UX.
 *
 * The dashboard already authenticates to dashboard-api with the admin
 * API key (nginx injects `Authorization: Bearer ${DASHBOARD_API_KEY}`
 * via proxy_set_header). On load we:
 *
 *   1. GET /api/auth/verify-session
 *   2. If 200 — already have a session, nothing to do.
 *   3. If 401 — POST /api/auth/admin-session, which mints a signed
 *      cookie (same shape magic-link redemption issues) and the
 *      browser keeps it for 12 hours.
 *
 * From the user's POV: open dashboard → sidebar's Hermes link just
 * works. No invite-to-yourself dance.
 *
 * Runs once per enabled route lifetime, including a fresh verification after
 * returning from the public Talk route. Leaving that lifetime cancels browser
 * work; an already accepted mint remains owned by the server. If session_signer isn't configured server-side the
 * admin-session POST 503s; we surface that in console as a hint to
 * set ODS_SESSION_SECRET, but don't block the dashboard.
 */
export function useSessionBootstrap(enabled = true) {
  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()

    const run = async () => {
      try {
        const verify = await fetch('/api/auth/verify-session', {
          // Include the cookie if the browser has one — that's how
          // verify-session decides whether to return 200 or 401.
          credentials: 'same-origin',
          signal: controller.signal,
        })
        if (controller.signal.aborted) return
        if (verify.ok) return  // session already present

        // 401 (or any non-2xx, e.g. 503 if dashboard-api is mid-restart) —
        // try to mint a fresh session. nginx injects the API-key Authorization
        // header, so the POST is authenticated automatically.
        const mint = await fetch('/api/auth/admin-session', {
          method: 'POST',
          credentials: 'same-origin',
          signal: controller.signal,
        })
        if (controller.signal.aborted) return
        if (mint.ok) return

        // 503 = ODS_SESSION_SECRET not configured. The dashboard still
        // works; cookie-gated services won't. Surface the hint quietly.
        if (mint.status === 503) {
          const body = await mint.json().catch(() => ({}))
          if (controller.signal.aborted) return
          console.warn(
            '[ods-session] could not mint admin session:',
            body.detail || 'server misconfigured',
            '— set ODS_SESSION_SECRET in .env and restart dashboard-api',
          )
          return
        }

        // Other failures (network, 5xx) — log for the operator but don't
        // crash the dashboard. The user still has the dashboard surface;
        // just the Hermes / chat tiles will route them to the invite page.
        console.warn('[ods-session] admin-session returned', mint.status)
      } catch (err) {
        if (!controller.signal.aborted) console.warn('[ods-session] bootstrap failed:', err)
      }
    }

    run()
    return () => controller.abort()
  }, [enabled])
}
