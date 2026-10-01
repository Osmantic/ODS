// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { inspectZipArchive, readZipTextEntries } from './pixelZipArchive'

describe('disposable ZIP worker client', () => {
  let workers
  beforeEach(() => {
    workers = []; vi.useFakeTimers()
    vi.stubGlobal('Worker', class {
      constructor(url, options) { this.url = url; this.options = options; this.terminate = vi.fn(); this.postMessage = vi.fn(); workers.push(this) }
    })
  })
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })
  const file = { name: 'project.zip', size: 120 }
  it('lists metadata and extracts exact selections in separate module workers', async () => {
    const first = inspectZipArchive(file)
    expect(workers[0].url.pathname).toContain('pixelZipArchive.worker.js')
    expect(workers[0].options).toEqual({ type: 'module' })
    expect(workers[0].postMessage).toHaveBeenCalledWith({ file, selectedPaths: null })
    workers[0].onmessage({ data: { result: { entries: [] } } })
    expect(await first).toEqual({ entries: [] }); expect(workers[0].terminate).toHaveBeenCalledOnce()
    const second = readZipTextEntries(file, ['src/main.py'])
    expect(workers[1].postMessage).toHaveBeenCalledWith({ file, selectedPaths: ['src/main.py'] })
    workers[1].onmessage({ data: { result: { files: [] } } }); await second
    expect(workers[1].terminate).toHaveBeenCalledOnce()
  })
  it('terminates on cancellation and detaches callbacks', async () => {
    const controller = new AbortController(), pending = inspectZipArchive(file, { signal: controller.signal })
    const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' })
    controller.abort(); await rejected
    expect(workers[0].terminate).toHaveBeenCalledOnce(); expect(workers[0].onmessage).toBeNull()
    expect(vi.getTimerCount()).toBe(0)
  })
  it('terminates a stuck inflater at the hard deadline', async () => {
    const pending = inspectZipArchive(file), rejected = expect(pending).rejects.toThrow('5 seconds')
    await vi.advanceTimersByTimeAsync(5000); await rejected
    expect(workers[0].terminate).toHaveBeenCalledOnce()
  })
  it.each(['onerror', 'onmessageerror'])('cleans up %s failures', async callback => {
    const pending = inspectZipArchive(file), rejected = expect(pending).rejects.toThrow('safely')
    workers[0][callback](); await rejected; expect(workers[0].terminate).toHaveBeenCalledOnce()
  })
  it('rejects parser failures without returning a partial result', async () => {
    const pending = inspectZipArchive(file), rejected = expect(pending).rejects.toThrow('Invalid CRC32')
    workers[0].onmessage({ data: { error: 'Invalid CRC32' } }); await rejected
    expect(workers[0].terminate).toHaveBeenCalledOnce()
  })
  it('rejects oversized and pre-aborted work before creating a worker', async () => {
    await expect(inspectZipArchive({ ...file, size: 4194305 })).rejects.toThrow('4 MB')
    const controller = new AbortController(); controller.abort()
    await expect(inspectZipArchive(file, { signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' })
    expect(workers).toHaveLength(0)
  })
})
