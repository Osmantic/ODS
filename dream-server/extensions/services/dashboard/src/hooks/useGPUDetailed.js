import { useState, useEffect, useRef, useCallback } from 'react'

// Auth: nginx injects Authorization header for all /api/ requests (see nginx.conf).

const POLL_INTERVAL = 5000

export function useGPUDetailed() {
  const [detailed, setDetailed] = useState(null)
  const [history, setHistory] = useState(null)
  const [topology, setTopology] = useState(null)
  const [runtime, setRuntime] = useState(null)
  const [runtimeProbeRunning, setRuntimeProbeRunning] = useState(false)
  const [runtimeProbeError, setRuntimeProbeError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const fetchInFlight = useRef(false)
  const runtimeFetchInFlight = useRef(false)
  const activeProbeInFlight = useRef(false)
  const runtimeRequestSequence = useRef(0)
  const runtimeAppliedSequence = useRef(0)
  const mounted = useRef(true)

  const fetchRuntime = useCallback(async ({ active = false } = {}) => {
    const inFlight = active ? activeProbeInFlight : runtimeFetchInFlight
    if (inFlight.current) return false
    inFlight.current = true
    const requestSequence = ++runtimeRequestSequence.current
    if (active && mounted.current) {
      setRuntimeProbeRunning(true)
      setRuntimeProbeError(null)
    }
    try {
      const response = await fetch(active ? '/api/gpu/amd-runtime/probe' : '/api/gpu/amd-runtime', {
        method: active ? 'POST' : 'GET',
        ...(active ? { headers: { 'X-Requested-With': 'DreamServerDashboard' } } : {}),
      })
      if (!response.ok) throw new Error(`Runtime probe failed (${response.status})`)
      const payload = await response.json()
      if (mounted.current && requestSequence >= runtimeAppliedSequence.current) {
        runtimeAppliedSequence.current = requestSequence
        setRuntime(payload)
      }
      return true
    } catch (err) {
      // Runtime diagnostics are best-effort and must not hide GPU metrics.
      if (active && mounted.current) setRuntimeProbeError(err.message)
      return false
    } finally {
      inFlight.current = false
      if (active && mounted.current) setRuntimeProbeRunning(false)
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    const fetchAll = async () => {
      if (document.hidden) return
      if (fetchInFlight.current) return
      fetchInFlight.current = true
      void fetchRuntime()
      try {
        const [detRes, histRes, topoRes] = await Promise.all([
          fetch('/api/gpu/detailed'),
          fetch('/api/gpu/history'),
          fetch('/api/gpu/topology'),
        ])
        if (detRes.ok) setDetailed(await detRes.json())
        if (histRes.ok) setHistory(await histRes.json())
        if (topoRes.ok) setTopology(await topoRes.json())
        setError(null)
      } catch (err) {
        setError(err.message)
      } finally {
        fetchInFlight.current = false
        setLoading(false)
      }
    }

    fetchAll()
    const interval = setInterval(fetchAll, POLL_INTERVAL)
    const onVisibility = () => { if (!document.hidden) fetchAll() }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      mounted.current = false
      clearInterval(interval)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [fetchRuntime])

  const runRuntimeProbe = useCallback(() => fetchRuntime({ active: true }), [fetchRuntime])

  return {
    detailed,
    history,
    topology,
    runtime,
    runtimeProbeRunning,
    runtimeProbeError,
    runRuntimeProbe,
    loading,
    error,
  }
}
