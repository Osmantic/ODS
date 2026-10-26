import { useCallback, useEffect, useRef, useState } from 'react'
import { refreshDelay } from './connectedAppsStatus'

const GENERIC = "ODS couldn't finish this step. Try again, and ask on Discord if it keeps happening."
const PROJECT_KEY = 'ods.connectedApps.googleProject'

// Only the Google Cloud project ID, so later fixes can open the owner's own console pages.
export function savedProject() {
  try { return localStorage.getItem(PROJECT_KEY) } catch { return null }
}

export function saveProject(projectId) {
  try { localStorage.setItem(PROJECT_KEY, projectId) } catch { /* a private window keeps no preferences */ }
}

// A refusal as the dashboard API words it: the custodian's code, a plain message,
// and whether it is a real failure that gets the Discord link.
export class ConnectedAppsError extends Error {
  constructor(code, message, help) {
    super(message)
    this.code = code
    this.help = help
  }
}

async function reply(response) {
  const value = await response.json().catch(() => null)
  if (response.ok) return value
  const detail = value?.detail
  if (detail && typeof detail === 'object' && typeof detail.code === 'string' && typeof detail.message === 'string') {
    throw new ConnectedAppsError(detail.code, detail.message, Boolean(detail.help))
  }
  throw new ConnectedAppsError('unknown', GENERIC, true)
}

async function action(name, body) {
  return reply(await fetch(`/api/pixel/connections/google/${name}`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}))
}

export const connectGoogle = choices => action('connect', choices)
export const completeGoogle = (flowId, address) => action('complete', {flowId, address})
export const cancelGoogle = flowId => action('cancel', {flowId})
export const disconnectGoogle = () => action('disconnect', {})
export const syncGoogle = () => action('sync', {})
export const finishGoogle = () => action('finish', {})

// Calendar changes Portal suggested. An approval carries the hash shown with the change.
export const listCalendarChanges = async () => reply(await fetch('/api/pixel/connections/google/changes', {cache: 'no-store'}))
export const approveCalendarChange = entry => action('changes/approve', {proposalId: entry.proposalId, sha256: entry.sha256})
export const declineCalendarChange = entry => action('changes/decline', {proposalId: entry.proposalId})
export const checkCalendarChange = entry => action('changes/check', {proposalId: entry.proposalId})

// A reviewed plugin turned on or off for Portal; Portal's gateway restarts with it.
export const changePlugin = async (plugin, enabled) => reply(await fetch('/api/pixel/connections/plugin', {
  method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({plugin, enabled})}))

// Gmail drafts Portal wrote. An approval carries the hash shown with the draft; nothing is ever sent.
export const listGmailDrafts = async () => reply(await fetch('/api/pixel/connections/google/drafts', {cache: 'no-store'}))
export const approveGmailDraft = entry => action('drafts/approve', {proposalId: entry.proposalId, sha256: entry.sha256})
export const declineGmailDraft = entry => action('drafts/decline', {proposalId: entry.proposalId})
export const checkGmailDraft = entry => action('drafts/check', {proposalId: entry.proposalId})

// Private changes Portal made by itself, and undoing one; the owner's click is the approval.
export const listCalendarActivity = async () => reply(await fetch('/api/pixel/connections/google/activity', {cache: 'no-store'}))
export const undoCalendarChange = entry => action('activity/undo', {proposalId: entry.proposalId})

// Whether Settings shows Connected apps at all. Unknown until the dashboard answers.
export function useConnectedAppsEnabled() {
  const [enabled, setEnabled] = useState(null)
  const [unavailable, setUnavailable] = useState(false)
  const latest = useRef(0)
  const refresh = useCallback(async () => {
    const version = ++latest.current
    try {
      const response = await fetch('/api/pixel/connections/enabled')
      if (!response.ok) throw new Error('Connected apps setting is unavailable')
      const value = await response.json()
      if (typeof value?.enabled !== 'boolean') throw new Error('Connected apps setting is invalid')
      if (version === latest.current) { setEnabled(value.enabled); setUnavailable(false) }
    } catch {
      // A failed read is not the owner's decision to turn the feature off.
      if (version === latest.current) setUnavailable(true)
    }
  }, [])
  useEffect(() => {
    const focus = () => { void refresh() }
    void refresh()
    window.addEventListener('focus', focus)
    return () => { latest.current += 1; window.removeEventListener('focus', focus) }
  }, [refresh])
  return {enabled, unavailable, refresh}
}

// Connected-apps status, refreshed while a step runs, on focus, and on request.
export function useConnectedAppsStatus() {
  const [status, setStatus] = useState(null)
  const [unavailable, setUnavailable] = useState(false)
  const latest = useRef(0)
  const refresh = useCallback(async () => {
    const version = ++latest.current
    try {
      const response = await fetch('/api/pixel/connections', {cache: 'no-store'})
      if (!response.ok) throw new Error(String(response.status))
      const value = await response.json()
      if (version !== latest.current) return value
      setStatus(value)
      setUnavailable(false)
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
    const delay = unavailable ? null : refreshDelay(status)
    if (delay === null) return undefined
    const timer = setTimeout(() => { void refresh() }, delay)
    return () => clearTimeout(timer)
  }, [status, unavailable, refresh])
  return {status, unavailable, refresh}
}

// Full Access is the one Portal mode connected apps warn about; read once.
export function useFullAccessOn() {
  const [on, setOn] = useState(false)
  useEffect(() => {
    let current = true
    fetch('/api/pixel/access-mode')
      .then(response => response.ok ? response.json() : null)
      .then(value => { if (current) setOn(value?.configured_mode === 'full-access') })
      // An unreadable access mode is reported by Portal access itself; no warning here.
      .catch(() => {})
    return () => { current = false }
  }, [])
  return on
}
