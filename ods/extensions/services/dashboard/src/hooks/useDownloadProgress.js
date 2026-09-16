import { useState, useEffect, useCallback, useRef } from 'react'

const TERMINAL_DOWNLOAD_STATUSES = new Set(['failed', 'error', 'cancelled'])
// Allow the API's 30-second host-agent request to settle before giving up.
const CANCEL_ACK_TIMEOUT_MS = 45000
const PROGRESS_TIMEOUT_MS = 15000

function isTerminalProgress(progress) {
  return TERMINAL_DOWNLOAD_STATUSES.has(progress?.status)
}

async function cancelErrorFromResponse(response) {
  try {
    const payload = await response.json()
    const detail = payload?.detail
    if (typeof detail === 'string' && detail.trim()) return detail
    if (typeof payload?.error === 'string' && payload.error.trim()) return payload.error
    if (typeof payload?.message === 'string' && payload.message.trim()) return payload.message
  } catch {
    // Fall through to the stable user-facing fallback.
  }
  return 'Failed to cancel download.'
}

/**
 * Hook to poll download progress during model downloads.
 * Returns progress data when a download is active.
 */
export function useDownloadProgress(pollIntervalMs = 1000) {
  const [progress, setProgress] = useState(null)
  const [isDownloading, setIsDownloading] = useState(false)
  const [completedDownload, setCompletedDownload] = useState(null)
  const [cancelError, setCancelError] = useState(null)
  const [isCancelling, setIsCancelling] = useState(false)
  const lastCompleteKeyRef = useRef(null)
  const progressInFlightRef = useRef(null)
  const cancelInFlightRef = useRef(false)

  const fetchProgress = useCallback(async () => {
    if (progressInFlightRef.current) return null
    const controller = new AbortController()
    progressInFlightRef.current = controller
    let rejectAbort
    const aborted = new Promise((_, reject) => {
      rejectAbort = () => reject(new Error('Download status request timed out'))
      controller.signal.addEventListener('abort', rejectAbort, { once: true })
    })
    const timeout = setTimeout(() => controller.abort(), PROGRESS_TIMEOUT_MS)

    try {
      const pending = (async () => {
        const response = await fetch('/api/models/download-status', { signal: controller.signal })
        if (!response.ok) throw new Error(`Download status unavailable (HTTP ${response.status})`)
        return response.json()
      })()
      const data = await Promise.race([pending, aborted])
      if (progressInFlightRef.current !== controller) return null
      
      if (data.status === 'downloading' || data.status === 'verifying') {
        const downloaded = data.bytesDownloaded || 0
        const total = data.bytesTotal || 0
        const rawPercent = total > 0
          ? (downloaded / total) * 100
          : Number.isFinite(data.percent) ? data.percent : 0
        const percent = Math.min(100, Math.max(0, rawPercent))

        setIsDownloading(true)
        setProgress({
          model: data.model,
          status: data.status,
          percent,
          bytesDownloaded: downloaded,
          bytesTotal: total,
          speedMbps: data.speedBytesPerSec ? data.speedBytesPerSec / (1024 * 1024) : 0,
          eta: data.eta,
          startedAt: data.startedAt
        })
      } else if (data.status === 'complete' || data.status === 'idle') {
        setIsDownloading(false)
        setCancelError(null)
        if (data.status === 'complete') {
          setProgress(null)
          const completeKey = `${data.model || ''}:${data.updatedAt || ''}`
          if (completeKey && completeKey !== lastCompleteKeyRef.current) {
            lastCompleteKeyRef.current = completeKey
            setCompletedDownload({
              model: data.model,
              status: data.status,
              updatedAt: data.updatedAt
            })
          }
        } else {
          // A later idle snapshot must not erase the only visible record of a
          // failed or cancelled transfer. The next download or dismissal does.
          setProgress(current => isTerminalProgress(current) ? current : null)
        }
      } else if (TERMINAL_DOWNLOAD_STATUSES.has(data.status)) {
        setIsDownloading(false)
        setCancelError(null)
        setProgress({
          status: data.status,
          error: data.error || data.message || (data.status === 'cancelled' ? 'Download cancelled' : 'Download failed'),
          model: data.model
        })
      }
      return data
    } catch (error) {
      if (progressInFlightRef.current === controller) console.warn('Download progress unavailable:', error)
      return null
    } finally {
      clearTimeout(timeout)
      controller.signal.removeEventListener('abort', rejectAbort)
      if (progressInFlightRef.current === controller) progressInFlightRef.current = null
    }
  }, [])

  useEffect(() => {
    void fetchProgress()
    return () => {
      progressInFlightRef.current?.abort()
      progressInFlightRef.current = null
    }
  }, [fetchProgress])

  useEffect(() => {
    // Poll frequently only while downloading; otherwise check every 10s.
    // Skip ticks while the tab is hidden — nobody sees the progress bar and
    // the visibility handler below catches state up on return (#1490).
    const activeInterval = isDownloading ? pollIntervalMs : 10000
    const tick = () => { if (!document.hidden) fetchProgress() }
    const interval = setInterval(tick, activeInterval)

    // Resume immediately when the tab becomes visible again
    const onVisibility = () => { if (!document.hidden) fetchProgress() }
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      clearInterval(interval)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [fetchProgress, pollIntervalMs, isDownloading])

  // Format helpers
  const formatBytes = (bytes) => {
    if (!bytes) return '0 B'
    const gb = bytes / (1024 ** 3)
    if (gb >= 1) return `${gb.toFixed(2)} GB`
    const mb = bytes / (1024 ** 2)
    if (mb >= 1) return `${mb.toFixed(1)} MB`
    const kb = bytes / 1024
    if (kb >= 1) return `${kb.toFixed(0)} KB`
    return `${bytes.toFixed(0)} B`
  }

  const formatEta = (eta) => {
    if (!eta || eta === 'calculating...') return 'calculating...'
    if (typeof eta === 'number') {
      const mins = Math.floor(eta / 60)
      // Floor the seconds too — a fractional eta (bytes-remaining / rate is a
      // float) otherwise renders as "1m 30.700000000000003s". Mirrors the
      // Math.floor already applied to minutes.
      const secs = Math.floor(eta % 60)
      if (mins > 0) return `${mins}m ${secs}s`
      return `${secs}s`
    }
    return eta
  }

  const cancelDownload = useCallback(async () => {
    if (cancelInFlightRef.current) return null
    cancelInFlightRef.current = true
    setIsCancelling(true)
    setCancelError(null)
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), CANCEL_ACK_TIMEOUT_MS)
    try {
      const response = await fetch('/api/models/download/cancel', {
        method: 'POST',
        signal: controller.signal,
      })
      if (!response.ok) throw new Error(await cancelErrorFromResponse(response))
      // The acknowledgement owns this guard. Status polling remains authoritative
      // about the transfer and must not keep the Cancel button locked on a stall.
      return fetchProgress()
    } catch (err) {
      setCancelError(controller.signal.aborted
        ? 'Cancellation was not acknowledged within 45 seconds. Check download progress before retrying.'
        : err?.message || 'Failed to cancel download.')
      return null
    } finally {
      clearTimeout(timeout)
      cancelInFlightRef.current = false
      setIsCancelling(false)
    }
  }, [fetchProgress])

  const clearTerminal = useCallback(() => {
    setProgress(current => isTerminalProgress(current) ? null : current)
    setCancelError(null)
  }, [])

  return {
    isDownloading,
    progress,
    completedDownload,
    cancelError,
    isCancelling,
    formatBytes,
    formatEta,
    refresh: fetchProgress,
    cancelDownload,
    clearTerminal
  }
}
