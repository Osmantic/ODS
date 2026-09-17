import { useState, useEffect } from 'react'

function readDismissed() {
  try { return localStorage.getItem('dismissed-update') } catch { return null }
}

// Auth: nginx injects Authorization header for all /api/ requests (see nginx.conf).

export function useVersion() {
  const [version, setVersion] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [dismissed, setDismissed] = useState(readDismissed)

  useEffect(() => {
    let disposed = false
    let timer
    let controller
    let inFlight = false
    let pendingChecks = 0
    const checkVersion = async () => {
      if (disposed || inFlight) return
      inFlight = true
      clearTimeout(timer)
      controller = new AbortController()
      const timeout = setTimeout(() => controller.abort(), 8000)
      let delay = 30 * 60 * 1000
      try {
        const response = await fetch('/api/version', { signal: controller.signal })
        if (!response.ok) {
          throw new Error('Failed to check version')
        }
        const data = await response.json()
        if (disposed) return
        if (['checking', 'stale'].includes(data.check_status)) delay = ++pendingChecks <= 2 ? 5000 : 60000
        else if (data.check_status === 'unavailable') delay = 60000
        else pendingChecks = 0
        setError(null)
        setVersion(data)
      } catch (err) {
        if (!disposed) {
          setError(err.message)
        }
        delay = 60000
      } finally {
        clearTimeout(timeout)
        inFlight = false
        if (!disposed) {
          setLoading(false)
          timer = setTimeout(checkVersion, delay)
        }
      }
    }

    checkVersion()
    
    const refreshed = () => checkVersion()
    const storage = event => {
      if (event.key === 'dismissed-update') {
        setDismissed(event.newValue)
      }
    }
    window.addEventListener('ods-version-checked', refreshed)
    window.addEventListener('storage', storage)
    return () => {
      disposed = true
      clearTimeout(timer)
      controller?.abort()
      window.removeEventListener('ods-version-checked', refreshed)
      window.removeEventListener('storage', storage)
    }
  }, [])

  const dismissUpdate = () => {
    if (version?.latest) {
      try { localStorage.setItem('dismissed-update', version.latest) } catch { /* Private browsing. */ }
      setDismissed(version.latest)
    }
  }

  const showUpdate = Boolean(version?.update_available && version?.latest && version.latest !== dismissed
    && (!version.check_status || version.check_status === 'checked'))
  return { version, showUpdate, loading, error, dismissUpdate }
}

export async function triggerUpdate(action) {
  const response = await fetch(`/api/update`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action })
  })
  
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || 'Update action failed')
  }
  
  return response.json()
}
