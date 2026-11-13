import { useCallback, useEffect, useRef, useState } from 'react'
import { ConnectedAppsError } from './useConnectedApps'

const BASE = '/api/pixel/connections/apps'
const GENERIC = 'ODS could not confirm this app connection. Check its saved state before trying again.'
const QUIET = ['absent', 'prepared', 'configured', 'cancelled', 'disconnected', 'failed']

async function reply(response) {
  const value = await response.json().catch(() => null)
  if (response.ok) return value
  const detail = value?.detail
  throw new ConnectedAppsError(typeof detail?.code === 'string' ? detail.code : 'native-app-unavailable',
    typeof detail?.message === 'string' ? detail.message : GENERIC, Boolean(detail?.help))
}

export async function reviewedAppAction(app, name, body = {}) {
  // A reviewed app ID, never an endpoint or server definition supplied by UI.
  if (app !== 'notion' || !['connect', 'complete', 'finish', 'cancel', 'disconnect', 'check'].includes(name)) {
    throw new ConnectedAppsError('invalid-request', GENERIC, true)
  }
  return reply(await fetch(`${BASE}/${app}/${name}`, {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}))
}

export function useReviewedApps() {
  const [status, setStatus] = useState(null)
  const [unavailable, setUnavailable] = useState(false)
  const [legacy, setLegacy] = useState(false)
  const latest = useRef(0)
  const refresh = useCallback(async () => {
    const version = ++latest.current
    try {
      const response = await fetch(BASE, {cache: 'no-store'})
      if (response.status === 404) {
        if (version === latest.current) { setLegacy(true); setUnavailable(false) }
        return null
      }
      const value = await reply(response)
      if (version === latest.current) { setStatus(value); setUnavailable(false); setLegacy(false) }
      return value
    } catch {
      if (version === latest.current) setUnavailable(true)
      return null
    }
  }, [])
  useEffect(() => {
    void refresh()
    const wake = () => { if (document.visibilityState !== 'hidden') void refresh() }
    window.addEventListener('focus', wake)
    document.addEventListener('visibilitychange', wake)
    return () => {
      latest.current++
      window.removeEventListener('focus', wake)
      document.removeEventListener('visibilitychange', wake)
    }
  }, [refresh])
  useEffect(() => {
    if (legacy || !status?.available) return undefined
    const app = status.apps?.notion
    if (!app || QUIET.includes(app.phase)) return undefined
    // Status only joins cached readiness to Root proof. It never starts an
    // OAuth exchange or provider probe; a fresh access check is an owner action.
    const delay = unavailable ? 10000 : app.phase === 'ready' ? 60000 : 3000
    const timer = setTimeout(() => { void refresh() }, delay)
    return () => clearTimeout(timer)
  }, [status, unavailable, legacy, refresh])
  return {status, unavailable, legacy, refresh}
}
