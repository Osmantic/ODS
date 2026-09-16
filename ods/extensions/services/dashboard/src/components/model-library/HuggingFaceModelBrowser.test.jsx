import { act, fireEvent, screen } from '@testing-library/react'
import { render } from '../../test/test-utils'
import HuggingFaceModelBrowser from './HuggingFaceModelBrowser' // eslint-disable-line no-unused-vars

const response = (body) => ({
  ok: true,
  json: async () => body,
})

describe('HuggingFaceModelBrowser', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  test.each(['close', 'unmount'])('aborts repository requests on %s', async action => {
    vi.useFakeTimers()
    let detailsSignal
    const fetchMock = vi.fn((url, options = {}) => {
      if (String(url).startsWith('/api/models/huggingface/search?')) {
        return Promise.resolve(response({
          models: [{
            id: 'org/model-GGUF',
            author: 'org',
            downloads: 10,
            likes: 2,
            license: 'apache-2.0',
            ggufFileCount: 1,
            runtimeCompatible: true,
          }],
          authenticated: false,
          stale: false,
        }))
      }
      if (String(url).includes('/api/models/huggingface/repositories/')) {
        detailsSignal = options.signal
        return new Promise((resolve, reject) => {
          options.signal.addEventListener('abort', () => {
            const error = new Error('request aborted')
            error.name = 'AbortError'
            reject(error)
          })
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const { unmount } = render(<HuggingFaceModelBrowser gpu={{ vramTotal: 16 }} />)
    await act(async () => vi.advanceTimersByTimeAsync(350))
    vi.useRealTimers()

    fireEvent.click(screen.getByRole('button', { name: /choose file/i }))
    expect(detailsSignal).toBeDefined()
    expect(detailsSignal.aborted).toBe(false)

    if (action === 'close') fireEvent.click(screen.getByTitle('Close'))
    else unmount()

    expect(detailsSignal.aborted).toBe(true)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
