import { renderHook, waitFor } from '@testing-library/react'
import { useDownloadProgress } from '../useDownloadProgress'

describe('useDownloadProgress', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  test('sets isDownloading when status is downloading', async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        status: 'downloading',
        model: 'test-model',
        percent: 50,
        bytesDownloaded: 5e9,
        bytesTotal: 10e9,
      })
    })

    const { result } = renderHook(() => useDownloadProgress())

    await waitFor(() => {
      expect(result.current.isDownloading).toBe(true)
    })
    expect(result.current.progress.percent).toBe(50)
    expect(result.current.progress.model).toBe('test-model')
  })

  test('clears progress when status is complete', async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        status: 'complete',
        model: 'test-model',
        updatedAt: '2026-05-16T12:00:00Z'
      })
    })

    const { result } = renderHook(() => useDownloadProgress())

    await waitFor(() => {
      // isDownloading starts false and stays false for 'complete'
      expect(fetch).toHaveBeenCalled()
    })
    expect(result.current.isDownloading).toBe(false)
    expect(result.current.progress).toBeNull()
    expect(result.current.completedDownload).toEqual({
      status: 'complete',
      model: 'test-model',
      updatedAt: '2026-05-16T12:00:00Z'
    })
  })

  test('formatBytes formats GB/MB/KB correctly', () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ status: 'idle' })
    })

    const { result } = renderHook(() => useDownloadProgress())

    expect(result.current.formatBytes(5e9)).toBe('4.66 GB')
    expect(result.current.formatBytes(5e6)).toBe('4.8 MB')
    expect(result.current.formatBytes(5000)).toBe('5 KB')
    expect(result.current.formatBytes(0)).toBe('0 B')
    expect(result.current.formatBytes(null)).toBe('0 B')
  })

  test('formatEta formats minutes and seconds', () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ status: 'idle' })
    })

    const { result } = renderHook(() => useDownloadProgress())

    expect(result.current.formatEta(90)).toBe('1m 30s')
    expect(result.current.formatEta(30)).toBe('30s')
    expect(result.current.formatEta(null)).toBe('calculating...')
    expect(result.current.formatEta('calculating...')).toBe('calculating...')
  })
})
