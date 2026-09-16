import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { DEFAULT_ASSISTANT_NAME, normalizeDisplayName, readIdentityResponse } from '../lib/portalIdentity'

const Context = createContext(null)
const DEFAULT = { displayName: DEFAULT_ASSISTANT_NAME, document: null, ready: false, busy: false,
  error: null, notice: null, reload: async () => false, save: async () => false }
const PATH = '/api/pixel/identity'

export function PortalIdentityProvider({ children }) {
  const [state, setState] = useState({ document: null, ready: false, busy: false, error: null, notice: null })
  const latest = useRef(state)
  latest.current = state
  const mounted = useRef(false)
  const active = useRef(null)

  const execute = useCallback(async (saving, rawName) => {
    if (!mounted.current || active.current || (saving && !latest.current.ready)) return false
    let payload
    if (saving) {
      try {
        payload = { expectedRevision: latest.current.document.revision, displayName: normalizeDisplayName(rawName) }
      } catch {
        setState(previous => ({ ...previous, error: 'Use a display name of at most 60 characters without control characters.', notice: null }))
        return false
      }
    }
    const flight = { controller: new AbortController() }
    active.current = flight
    const current = () => mounted.current && active.current === flight
    const timer = setTimeout(() => flight.controller.abort(), saving ? 30000 : 15000)
    setState(previous => ({ ...previous, busy: true, ready: false, error: null, notice: null }))
    const request = async (method, body) => {
      const response = await fetch(method === 'POST' ? `${PATH}/save` : PATH, {
        method, cache: 'no-store', signal: flight.controller.signal,
        ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
      })
      if (!response.ok) {
        void response.body?.cancel().catch(() => {})
        const failure = new Error('Assistant identity request failed')
        failure.conflict = response.status === 409
        throw failure
      }
      return await readIdentityResponse(response)
    }
    try {
      if (payload) {
        const committed = await request('POST', payload)
        if (!current() || flight.controller.signal.aborted) return false
        if (committed.revision !== payload.expectedRevision + 1 || committed.displayName !== payload.displayName) throw new Error('Identity save mismatch')
      }
      const document = await request('GET')
      if (!current() || flight.controller.signal.aborted) return false
      if (payload && (document.revision !== payload.expectedRevision + 1 || document.displayName !== payload.displayName)) throw new Error('Identity readback mismatch')
      setState({ document, ready: true, busy: false, error: null, notice: saving ? 'Assistant name saved.' : null })
      return true
    } catch (failure) {
      if (current()) setState(previous => ({ ...previous, ready: false, busy: false, notice: null,
        error: failure?.conflict ? 'The assistant name changed elsewhere. Refresh the saved name before saving again.'
          : saving ? 'The name save could not be confirmed. Refresh the saved name before saving again.'
            : 'The saved assistant name could not be loaded. Refresh to try again.' }))
      return false
    } finally {
      clearTimeout(timer)
      if (current()) {
        active.current = null
        setState(previous => ({ ...previous, busy: false, ...(flight.controller.signal.aborted ? {
          ready: false, error: 'The identity request timed out. Refresh the saved name before saving again.',
        } : {}) }))
      }
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    void execute(false)
    return () => {
      mounted.current = false
      active.current?.controller.abort()
      active.current = null
    }
  }, [execute])
  const reload = useCallback(() => execute(false), [execute])
  const save = useCallback(name => execute(true, name), [execute])
  return <Context.Provider value={{ ...state, displayName: state.document?.displayName || DEFAULT_ASSISTANT_NAME, reload, save }}>{children}</Context.Provider>
}

export function usePortalIdentity() {
  return useContext(Context) || DEFAULT
}
