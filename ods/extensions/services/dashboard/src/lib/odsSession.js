// The dashboard keeps this browser's ods-session cookie valid so services
// that sign in with it (Hermes today) open signed in. See
// hooks/useSessionBootstrap.js for when the dashboard checks it.

// A minted session lives 12 hours (SESSION_TTL_SECONDS in dashboard-api
// routers/auth.py). Renew once less than this remains, so the cookie never
// lapses between two checks.
const RENEW_BEFORE_SECONDS = 60 * 60

// Services whose launch links need a valid ods-session when they open:
// hermes-proxy's /auth/ods entry, and its LAN hostname behind ods-proxy.
const SESSION_LAUNCH_SERVICES = new Set(['hermes-proxy'])

export function opensWithOdsSession(serviceId) {
  return SESSION_LAUNCH_SERVICES.has(serviceId)
}

async function needsNewSession(verify) {
  if (!verify.ok) return true
  const { expires_at: expiresAt } = await verify.json()
  return expiresAt - Date.now() / 1000 < RENEW_BEFORE_SECONDS
}

/**
 * Verify the ods-session cookie and mint a fresh one when it is missing,
 * expired or close to expiry. Never rejects: the dashboard keeps working
 * without a session, only cookie-gated services are affected.
 */
export async function refreshOdsSession(signal) {
  try {
    const verify = await fetch('/api/auth/verify-session', {
      // Include the cookie if the browser has one — that's how
      // verify-session decides whether to return 200 or 401.
      credentials: 'same-origin',
      signal,
    })
    if (signal?.aborted) return
    if (!(await needsNewSession(verify))) return
    if (signal?.aborted) return

    // 401 (or any non-2xx, e.g. 503 if dashboard-api is mid-restart), or a
    // session about to lapse — mint a fresh one. nginx injects the API-key
    // Authorization header, so the POST is authenticated automatically.
    const mint = await fetch('/api/auth/admin-session', {
      method: 'POST',
      credentials: 'same-origin',
      signal,
    })
    if (signal?.aborted) return
    if (mint.ok) return

    // 503 = ODS_SESSION_SECRET not configured. The dashboard still
    // works; cookie-gated services won't. Surface the hint quietly.
    if (mint.status === 503) {
      const body = await mint.json().catch(() => ({}))
      if (signal?.aborted) return
      console.warn(
        '[ods-session] could not mint admin session:',
        body.detail || 'server misconfigured',
        '— set ODS_SESSION_SECRET in .env and restart dashboard-api',
      )
      return
    }

    // Other failures (network, 5xx) — log for the operator but don't
    // crash the dashboard. The user still has the dashboard surface;
    // Hermes shows its "sign-in expired" page with the recovery step.
    console.warn('[ods-session] admin-session returned', mint.status)
  } catch (err) {
    if (!signal?.aborted) console.warn('[ods-session] bootstrap failed:', err)
  }
}

/**
 * Click handler for a launch link to a cookie-gated service. Opens the tab
 * inside the click (so popup blockers allow it), refreshes the session, then
 * sends the tab to the link. Modified clicks stay with the browser.
 */
export function openWithFreshSession(event) {
  const url = event.currentTarget.href
  if (!url || event.defaultPrevented || event.button !== 0
    || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  const tab = window.open('', '_blank')
  if (!tab) return
  event.preventDefault()
  // Same protection as rel="noopener": the service cannot reach this tab.
  tab.opener = null
  refreshOdsSession().then(() => {
    if (!tab.closed) tab.location.replace(url)
  })
}
