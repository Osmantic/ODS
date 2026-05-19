import { useEffect, useRef } from 'react'

/**
 * Ensure the dashboard user has a valid `dream-session` cookie.
 *
 * Why this exists
 * ---------------
 * Cookie-gated services (hermes-proxy today, more later) `forward_auth`
 * against `/api/auth/verify-session` and bounce anyone without a valid
 * dream-session cookie to a "you need an invite" page. Without this
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
 * Runs once per mount. Re-runs only if the page is reloaded — which is
 * fine, the verify-session call is cheap (one signature check, no
 * DB lookups). If session_signer isn't configured server-side the
 * admin-session POST 503s; we surface that in console as a hint to
 * set DREAM_SESSION_SECRET, but don't block the dashboard.
 */
export function useSessionBootstrap() {
  const ran = useRef(false)

  useEffect(() => {
    if (ran.current) return
    ran.current = true

    const run = async () => {
      try {
        const verify = await fetch('/api/auth/verify-session', {
          // Include the cookie if the browser has one — that's how
          // verify-session decides whether to return 200 or 401.
          credentials: 'same-origin',
        })
        if (verify.ok) return  // session already present

        // 401 (or any non-2xx, e.g. 503 if dashboard-api is mid-restart) —
        // try to mint a fresh session. nginx injects the API-key Authorization
        // header, so the POST is authenticated automatically.
        const mint = await fetch('/api/auth/admin-session', {
          method: 'POST',
          credentials: 'same-origin',
        })
        if (mint.ok) return

        // 503 = DREAM_SESSION_SECRET not configured. The dashboard still
        // works; cookie-gated services won't. Surface the hint quietly.
        if (mint.status === 503) {
          const body = await mint.json().catch(() => ({}))
          // eslint-disable-next-line no-console
          console.warn(
            '[dream-session] could not mint admin session:',
            body.detail || 'server misconfigured',
            '— set DREAM_SESSION_SECRET in .env and restart dashboard-api',
          )
          return
        }

        // Other failures (network, 5xx) — log for the operator but don't
        // crash the dashboard. The user still has the dashboard surface;
        // just the Hermes / chat tiles will route them to the invite page.
        // eslint-disable-next-line no-console
        console.warn('[dream-session] admin-session returned', mint.status)
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn('[dream-session] bootstrap failed:', err)
      }
    }

    run()
  }, [])
}
