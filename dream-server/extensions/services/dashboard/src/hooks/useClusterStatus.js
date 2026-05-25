import { useState, useEffect, useRef, useCallback } from 'react'

const POLL_INTERVAL = 5000

export function useClusterStatus() {
  const [cluster, setCluster] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const fetchInFlight = useRef(false)
  // Mirrors the effect's `alive` local so refetch() can short-circuit once
  // the component has unmounted (effect cleanup flips it to false).
  const aliveRef = useRef(true)

  const fetchCluster = useCallback(async () => {
    if (!aliveRef.current) return
    if (document.hidden) return
    if (fetchInFlight.current) return
    fetchInFlight.current = true
    try {
      const res = await fetch('/api/cluster/status')
      if (!aliveRef.current) return
      if (res.ok) {
        const data = await res.json()
        if (!aliveRef.current) return
        setCluster(data)
        setError(null)
      } else {
        throw new Error(`HTTP ${res.status}`)
      }
    } catch (err) {
      if (!aliveRef.current) return
      setError(err.message)
    } finally {
      fetchInFlight.current = false
      if (aliveRef.current) setLoading(false)
    }
  }, [])

  const refetch = useCallback(() => {
    setLoading(true)
    setError(null)
    return fetchCluster()
  }, [fetchCluster])

  useEffect(() => {
    aliveRef.current = true
    fetchCluster()
    const interval = setInterval(fetchCluster, POLL_INTERVAL)
    const onVisibility = () => { if (!document.hidden) fetchCluster() }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      aliveRef.current = false
      clearInterval(interval)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [fetchCluster])

  return { cluster, loading, error, refetch }
}
