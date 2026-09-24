import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { runInNewContext } from 'node:vm'

describe('dashboard service worker', () => {
  test('activation deletes only stale ODS dashboard caches', async () => {
    const handlers = {}
    const worker = {
      addEventListener: vi.fn((name, handler) => { handlers[name] = handler }),
      skipWaiting: vi.fn(() => Promise.resolve()),
      clients: { claim: vi.fn(() => Promise.resolve()) },
    }
    const cacheStorage = {
      keys: vi.fn(() => Promise.resolve([
        'ods-dashboard-sw-v0',
        'ods-dashboard-sw-v1',
        'ods-dashboard-sw-preview',
        'open-webui-assets',
        'operator-cache',
      ])),
      delete: vi.fn(() => Promise.resolve(true)),
    }
    const source = readFileSync(
      resolve(process.cwd(), 'public/sw.js'),
      'utf-8',
    )
    runInNewContext(source, { self: worker, caches: cacheStorage, Promise })

    let activation
    handlers.activate({ waitUntil: promise => { activation = promise } })
    await activation

    expect(worker.clients.claim).toHaveBeenCalledOnce()
    expect(cacheStorage.delete.mock.calls.map(([key]) => key)).toEqual([
      'ods-dashboard-sw-v0',
      'ods-dashboard-sw-preview',
    ])
  })
})
