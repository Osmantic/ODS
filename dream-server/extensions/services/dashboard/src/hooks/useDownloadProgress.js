import { useState, useEffect, useCallback, useRef } from 'react'

/**
 * Hook to poll download progress during model downloads.
 * Returns progress data when a download is active.
 */
export function useDownloadProgress(pollIntervalMs = 1000) {
  const [progress, setProgress] = useState(null)
  const [isDownloading, setIsDownloading] = useState(false)
  const [completedDownload, setCompletedDownload] = useState(null)
  const lastCompleteKeyRef = useRef(null)

  const fetchProgress = useCallback(async () => {
    try {
      const response = await fetch('/api/models/download-status')
      if (!response.ok) return
      
      const data = await response.json()
      
      if (data.status === 'downloading' || data.status === 'verifying') {
        const downloaded = data.bytesDownloaded || 0
        const total = data.bytesTotal || 0
        const percent = total > 0 ? (downloaded / total) * 100 : 0

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
        setProgress(null)
        if (data.status === 'complete') {
          const completeKey = `${data.model || ''}:${data.updatedAt || ''}`
          if (completeKey && completeKey !== lastCompleteKeyRef.current) {
            lastCompleteKeyRef.current = completeKey
            setCompletedDownload({
              model: data.model,
              status: data.status,
              updatedAt: data.updatedAt
            })
          }
        }
      } else if (data.status === 'failed' || data.status === 'error' || data.status === 'cancelled') {
        setIsDownloading(false)
        setProgress({
          error: data.error || data.message || (data.status === 'cancelled' ? 'Download cancelled' : 'Download failed'),
          model: data.model
        })
      }
    } catch {
      // Silently fail - API might not be available
    }
  }, [])

  useEffect(() => {
    fetchProgress()

    // Poll frequently only while downloading; otherwise check every 10s
    const activeInterval = isDownloading ? pollIntervalMs : 10000
    const interval = setInterval(fetchProgress, activeInterval)
    return () => clearInterval(interval)
  }, [fetchProgress, pollIntervalMs, isDownloading])

  // Format helpers
  const formatBytes = (bytes) => {
    if (!bytes) return '0 B'
    const gb = bytes / (1024 ** 3)
    if (gb >= 1) return `${gb.toFixed(2)} GB`
    const mb = bytes / (1024 ** 2)
    if (mb >= 1) return `${mb.toFixed(1)} MB`
    return `${(bytes / 1024).toFixed(0)} KB`
  }

  const formatEta = (eta) => {
    if (!eta || eta === 'calculating...') return 'calculating...'
    if (typeof eta === 'number') {
      const mins = Math.floor(eta / 60)
      const secs = eta % 60
      if (mins > 0) return `${mins}m ${secs}s`
      return `${secs}s`
    }
    return eta
  }

  const cancelDownload = useCallback(async () => {
    try {
      await fetch('/api/models/download/cancel', { method: 'POST' })
      fetchProgress()
    } catch (err) {
      console.error('Failed to cancel download:', err)
    }
  }, [fetchProgress])

  return {
    isDownloading,
    progress,
    completedDownload,
    formatBytes,
    formatEta,
    refresh: fetchProgress,
    cancelDownload
  }
}
