import { useCallback, useEffect, useRef, useState } from 'react'
import { confirmOutcome, readOutcome, readRuntime, runtimeChangeRequest } from './pixelProviderRuntimeStatus'
import { providerRuntimeErrorMessage, readProviderRuntimeError } from './pixelProviderRuntimeError'

const PATH = '/api/pixel/providers/runtime'

async function request(flight, method, payload) {
  flight.timer = setTimeout(() => flight.controller.abort(), method === 'POST' ? 350000 : 75000)
  try {
    const response = await fetch(PATH, { method, cache: 'no-store', signal: flight.controller.signal,
      ...(payload ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) } : {}) })
    if (!response.ok) {
      const error = new Error(`HTTP ${response.status}`)
      if (method === 'POST') error.providerReason = await readProviderRuntimeError(response)
      throw error
    }
    return await response.json()
  } finally { clearTimeout(flight.timer) }
}

export default function usePixelProviderRuntime({ savedRevision, saving, blocked, onBusyChange }) {
  const [runtime, setRuntime] = useState(null)
  const [running, setRunning] = useState(null)
  const [stale, setStale] = useState(true)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [stage, setStage] = useState(null)
  const latest = useRef(null)
  latest.current = { savedRevision, saving, blocked, onBusyChange, runtime, stale }
  const active = useRef(null)
  const mounted = useRef(false)
  const sequence = useRef(0)

  const cancel = useCallback(() => {
    sequence.current += 1
    if (active.current) {
      clearTimeout(active.current.timer)
      active.current.controller.abort()
      active.current = null
    }
  }, [])

  const execute = useCallback(async (operation, replaceInspection = false) => {
    if (!mounted.current || latest.current.saving) return
    if (active.current && !(operation === 'inspect' && replaceInspection && active.current.operation === 'inspect')) return
    let payload
    if (operation !== 'inspect') {
      if (latest.current.stale || (operation !== 'recover' && latest.current.blocked)) return
      try { payload = runtimeChangeRequest(latest.current.runtime, operation, latest.current.savedRevision) }
      catch { setError('Refresh runtime status before changing providers.'); return }
    }
    const notify = latest.current.onBusyChange
    if (operation !== 'inspect' && notify(true) === false) return
    cancel()
    const flight = { operation, notify, controller: new AbortController(), sequence: sequence.current }
    active.current = flight
    const current = () => mounted.current && active.current === flight && sequence.current === flight.sequence
    const readable = () => current() && !flight.controller.signal.aborted
    setRunning(operation)
    setStale(true)
    setError(null)
    setNotice(null)
    setStage(operation === 'inspect' ? 'Inspecting current assistant runtime…' : 'Waiting for the provider controller. The assistant may restart…')
    let controllerReplied = false
    try {
      let outcome
      if (payload) {
        const raw = await request(flight, 'POST', payload)
        if (!readable()) return
        outcome = readOutcome(raw, payload)
        controllerReplied = true
        setStage('Controller replied. Checking current runtime…')
      }
      const raw = await request(flight, 'GET')
      if (!readable()) return
      const inspected = readRuntime(raw)
      setRuntime(inspected)
      if (outcome) {
        const message = confirmOutcome(outcome, inspected, payload)
        if (operation !== 'recover' && latest.current.savedRevision !== payload.providerRevision) {
          throw new Error('Saved revision changed during verification')
        }
        setNotice(message)
      }
      setStale(false)
    } catch (failure) {
      if (current()) {
        setStale(true)
        const reported = operation !== 'inspect' && providerRuntimeErrorMessage(failure?.providerReason)
        setError(reported || (operation === 'inspect'
          ? 'Runtime inspection failed. Saved providers remain separate; refresh runtime status to try again.'
          : controllerReplied
            ? 'The controller replied, but current runtime verification failed. Refresh runtime status before any further change.'
            : 'The provider change could not be confirmed. It may still be running; refresh runtime status before any further change.'))
      }
    } finally {
      if (current()) {
        // An abort is not evidence that the server cancelled the transaction.
        if (flight.controller.signal.aborted) {
          setStale(true)
          setError(operation === 'inspect' ? 'Runtime inspection timed out. Refresh runtime status to try again.'
            : 'The provider change timed out. It may still be running; refresh runtime status before any further change.')
        }
        active.current = null
        setRunning(null)
        setStage(null)
        if (operation !== 'inspect') flight.notify(false)
      }
    }
  }, [cancel])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      const flight = active.current
      cancel()
      if (flight && flight.operation !== 'inspect') flight.notify(false)
    }
  }, [cancel])

  useEffect(() => {
    if (!saving) void execute('inspect', true)
  }, [savedRevision, saving, execute])

  return { runtime, running, stale, error, notice, stage,
    inspect: () => execute('inspect'), change: operation => execute(operation) }
}
