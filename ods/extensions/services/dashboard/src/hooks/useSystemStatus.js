import { useState, useEffect, useRef } from 'react'

const POLL_INTERVAL = 5000 // 5 seconds

// Mock data for development/demo - gated behind VITE_USE_MOCK_DATA env var
const USE_MOCK_DATA = import.meta.env.VITE_USE_MOCK_DATA === 'true'

// Mock data for development/demo
function getMockStatus() {
  return {
    gpu: {
      name: 'NVIDIA RTX 4070 Ti Super',
      vramUsed: 13.2,
      vramTotal: 16,
      utilization: 45,
      temperature: 62
    },
    services: [
      { name: 'llama-server', status: 'healthy', port: 8080, uptime: 7200 },
      { name: 'Open WebUI', status: 'healthy', port: 3000, uptime: 7200 },
      { name: 'Whisper (STT)', status: 'healthy', port: 9000, uptime: 7200 },
      { name: 'Kokoro (TTS)', status: 'healthy', port: 8880, uptime: 7200 },
      { name: 'Qdrant', status: 'healthy', port: 6333, uptime: 7200 },
      { name: 'n8n', status: 'healthy', port: 5678, uptime: 7200 }
    ],
    model: {
      name: 'Qwen2.5-32B-Instruct-AWQ',
      tokensPerSecond: 54,
      contextLength: 32768
    },
    bootstrap: null, // null means no bootstrap in progress
    uptime: 7200, // seconds
    version: '1.0.0',
    tier: 'Professional'
  }
}

const MOCK_STATUS = getMockStatus()

// Named export for dev-only mocking (explicit opt-in via VITE_USE_MOCK_DATA)
export { getMockStatus }

export function useSystemStatus() {
  const [status, setStatus] = useState(USE_MOCK_DATA ? MOCK_STATUS : {
    gpu: null,
    services: [],
    model: null,
    bootstrap: null,
    uptime: 0
  })
  const [loading, setLoading] = useState(!USE_MOCK_DATA)
  const [error, setError] = useState(null)
  // Guard against overlapping fetches — if the API is slow (e.g.
  // llama-server under inference load) we skip the next poll rather
  // than stacking concurrent requests that can amplify the problem.
  const fetchInFlight = useRef(false)
  // Allow the very first fetch to run even on a hidden tab so that
  // users who open the dashboard in a background window (multi-monitor,
  // restored session, browser automation) don't see a permanently stuck
  // loading skeleton. After the initial data lands, the hidden-tab
  // guard engages for subsequent polls to save CPU/network.
  const hasInitialData = useRef(false)

  useEffect(() => {
    const fetchStatus = async () => {
      if (USE_MOCK_DATA) {
        setLoading(false)
        return
      }

      // Pause polling when the tab is hidden — but only after the first
      // successful fetch so the loading skeleton is never permanent.
      if (document.hidden && hasInitialData.current) return

      // Skip this tick if the previous fetch hasn't returned yet.
      if (fetchInFlight.current) return
      fetchInFlight.current = true

      try {
        const response = await fetch('/api/status')
        if (!response.ok) throw new Error('Failed to fetch status')
        const data = await response.json()
        setStatus(data)
        setError(null)
        hasInitialData.current = true
      } catch (err) {
        setError(err.message)
      } finally {
        fetchInFlight.current = false
        setLoading(false)
      }
    }

    fetchStatus()
    const interval = setInterval(fetchStatus, POLL_INTERVAL)

    // Resume immediately when the tab becomes visible again
    const onVisibility = () => { if (!document.hidden) fetchStatus() }
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      clearInterval(interval)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [])

  return { status, loading, error }
}
