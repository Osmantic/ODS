import { useState, useEffect, useCallback } from 'react'

// Auth: nginx injects the Authorization header for /api/ requests
// (see nginx.conf). The fetch below is a plain relative URL.
//
// Server-side authoritative first-run gating.
//
// Why a hook (vs. just localStorage): localStorage flags are per-browser.
// A fresh device opened from a different phone, a re-imaged disk, or a
// cleared browser cache would all re-trigger the wizard for that browser
// while leaving an actually-onboarded device "unconfigured" from the new
// browser's perspective. The truth lives in the dashboard-api's
// `setup-complete.json` sentinel; this hook surfaces that truth to the UI.
//
// Failure mode: if the API call fails (network error, CORS, dashboard-api
// not yet up), we default to "not first run". A false negative ("wizard
// never shows; user reads docs") is far less disruptive than a false
// positive ("wizard re-appears whenever the API blips and the user
// can't reach the normal dashboard"). The wizard can always be
// re-triggered via the deeper `/setup` route once that exists.

export function useFirstRun() {
  const [firstRun, setFirstRun] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await fetch('/api/setup/status')
      if (!resp.ok) throw new Error(`setup-status returned ${resp.status}`)
      const data = await resp.json()
      setFirstRun(!!data.first_run)
      setError(null)
    } catch (err) {
      // See the failure-mode comment above. We mark loading=false so the
      // UI proceeds normally; the wizard is hidden until the next refresh.
      setFirstRun(false)
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  return { firstRun, loading, error, refresh }
}
