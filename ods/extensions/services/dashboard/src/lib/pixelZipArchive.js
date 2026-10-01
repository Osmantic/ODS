/* global Worker */
// Each operation has its own worker: abort/unmount terminates parsing and any
// inflate immediately, including hostile archives that never yield to JS.
const MAX_ARCHIVE_BYTES = 4 * 1024 * 1024
const DEADLINE_MS = 5000
function request(file, selectedPaths, { signal } = {}) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new globalThis.DOMException('ZIP reading cancelled.', 'AbortError')); return }
    if (!file?.size || file.size > MAX_ARCHIVE_BYTES) { reject(new Error('Choose a nonempty ZIP no larger than 4 MB.')); return }
    if (typeof globalThis.Worker !== 'function') { reject(new Error('ZIP reading requires a browser with Web Worker support.')); return }
    let worker, timeout
    const finish = (error, result) => {
      clearTimeout(timeout); signal?.removeEventListener('abort', abort)
      if (worker) { worker.onmessage = null; worker.onerror = null; worker.onmessageerror = null; worker.terminate() }
      if (error) reject(error); else resolve(result)
    }
    const abort = () => finish(new globalThis.DOMException('ZIP reading cancelled.', 'AbortError'))
    try {
      worker = new Worker(new URL('./pixelZipArchive.worker.js', import.meta.url), { type: 'module' })
      worker.onmessage = ({ data }) => data?.error ? finish(new Error(data.error)) : finish(null, data.result)
      worker.onerror = () => finish(new Error('ZIP could not be read safely.'))
      worker.onmessageerror = () => finish(new Error('ZIP could not be read safely.'))
      signal?.addEventListener('abort', abort, { once: true })
      timeout = setTimeout(() => finish(new Error('ZIP reading exceeded 5 seconds. Choose a smaller archive.')), DEADLINE_MS)
      worker.postMessage({ file, selectedPaths })
    } catch (error) { finish(error) }
  })
}
export const inspectZipArchive = (file, options) => request(file, null, options)
export const readZipTextEntries = (file, selectedPaths, options) => request(file, selectedPaths, options)
