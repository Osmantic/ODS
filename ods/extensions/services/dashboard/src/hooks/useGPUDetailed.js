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
    let disposed = false
    let activeController = null
    const fetchAll = async () => {
      if (document.hidden) return
      if (fetchInFlight.current) return
      fetchInFlight.current = true
      const controller = new AbortController()
      activeController = controller
      let rejectAbort
      const aborted = new Promise((_, reject) => {
        rejectAbort = () => reject(new Error('GPU status request timed out'))
        controller.signal.addEventListener('abort', rejectAbort, {once:true})
      })
      const timer = setTimeout(() => controller.abort(), 15000)
      try {
        const snapshot = Promise.all(['detailed', 'history', 'topology'].map(async endpoint => {
          const response = await fetch(`/api/gpu/${endpoint}`, {signal:controller.signal})
          return response.ok ? response.json() : undefined
        }))
        const [details, samples, links] = await Promise.race([snapshot, aborted])
        if (disposed) return
        if (details !== undefined) setDetailed(details)
        if (samples !== undefined) setHistory(samples)
        if (links !== undefined) setTopology(links)
        setError(null)
      } catch (err) {
        if (!disposed) setError(err.message)
      } finally {
        clearTimeout(timer)
        controller.signal.removeEventListener('abort', rejectAbort)
        if (activeController === controller) activeController = null
        fetchInFlight.current = false
        if (!disposed) setLoading(false)
      }
    }

    fetchAll()
    const interval = setInterval(fetchAll, POLL_INTERVAL)
    const onVisibility = () => { if (!document.hidden) fetchAll() }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      disposed = true
      activeController?.abort()
      clearInterval(interval)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [])

  return { detailed, history, topology, loading, error }
}
