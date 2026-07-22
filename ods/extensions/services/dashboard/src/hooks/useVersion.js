import { useState, useEffect } from 'react'

// Auth: nginx injects Authorization header for all /api/ requests (see nginx.conf).

export function useVersion() {
  const [version, setVersion] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    const checkVersion = async () => {
      try {
        const response = await fetch(`/api/version`)
        if (!response.ok) {
          throw new Error('Failed to check version')
        }
        const data = await response.json()
        // Honor a previous dismissal across reloads. dismissUpdate() records
        // the dismissed `latest` in localStorage, but that value was never read
        // back — so every reload re-fetched update_available:true and the update
        // banner reappeared despite the user dismissing it. Suppress it only for
        // the exact version that was dismissed; a genuinely newer `latest` no
        // longer matches and surfaces normally.
        if (data.update_available && data.latest &&
            localStorage.getItem('dismissed-update') === data.latest) {
          data.update_available = false
        }
        setVersion(data)
      } catch (err) {
        setError(err.message)
        setVersion(null)
      } finally {
        setLoading(false)
      }
    }

    checkVersion()
    
    // Check every 30 minutes
    const interval = setInterval(checkVersion, 30 * 60 * 1000)
    return () => clearInterval(interval)
  }, [])

  const dismissUpdate = () => {
    if (version) {
      localStorage.setItem('dismissed-update', version.latest)
      setVersion({ ...version, update_available: false })
    }
  }

  return { version, loading, error, dismissUpdate }
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
