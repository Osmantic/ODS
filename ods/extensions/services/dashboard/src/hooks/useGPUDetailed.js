import { useState, useEffect } from 'react'

// Auth: nginx injects Authorization header for all /api/ requests (see nginx.conf).

const POLL_INTERVAL = 5000

export function useGPUDetailed() {
  const [detailed, setDetailed] = useState(null)
  const [history, setHistory] = useState(null)
  const [topology, setTopology] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    // Each effect lifetime owns its poll; StrictMode cleanup must not block
    // the replacement lifetime or release its in-flight guard.
    let fetchInFlight = false
    let disposed = false
    let activeController = null
    const fetchAll = async () => {
      if (disposed || document.hidden || fetchInFlight) return
      fetchInFlight = true
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
        // Promise.all rejects as soon as one endpoint fails. Release its
        // still-pending siblings too, rather than dropping their deadline.
        controller.abort()
        if (activeController === controller) activeController = null
        fetchInFlight = false
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
