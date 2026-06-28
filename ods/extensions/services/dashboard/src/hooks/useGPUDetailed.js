import { useState, useEffect, useRef } from 'react'

// Auth: nginx injects Authorization header for all /api/ requests (see nginx.conf).

const POLL_INTERVAL = 5000

export function useGPUDetailed() {
  const [detailed, setDetailed] = useState(null)
  const [history, setHistory] = useState(null)
  const [topology, setTopology] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const fetchInFlight = useRef(false)

  useEffect(() => {
    const fetchAll = async () => {
      if (document.hidden) return
      if (fetchInFlight.current) return
      fetchInFlight.current = true
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
      clearInterval(interval)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [])

  return { detailed, history, topology, loading, error }
}
